"""Instance administration: overview/trend metrics and user management.

Authorization is the users.role column via the AdminUser/OwnerUser
dependencies — never the deployment mode. Metric definitions live in
docs/admin-metrics.md; responses are allowlisted aggregates and account
metadata, never private content, credentials, or raw error text. Role and
status changes land in admin_audit_log.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import Date as SADate
from sqlalchemy import cast, desc, func, or_, select

from .. import quota
from ..config import settings
from ..deps import AdminUser, DbSession, OwnerUser
from ..models import (
    AdminAuditLog,
    Article,
    ArticleProcessingEvent,
    LLMUsage,
    ReadingActivity,
    Subscription,
    Tier,
    User,
    UserActivityDay,
    UserArticleState,
    UserQuotaPeriod,
)
from ..roles import ROLE_OWNER, ROLE_USER, FinalOwnerError, change_role, change_status
from ..schemas import (
    ActivityRange,
    AdminOverviewOut,
    AdminRoleIn,
    AdminStatusIn,
    AdminTierIn,
    AdminTrendDayOut,
    AdminTrendsOut,
    AdminUserOut,
    AdminUsersPageOut,
    UsageFeatureOut,
    UsageModelOut,
)
from ..timewindow import window_bounds

router = APIRouter(prefix="/admin", tags=["admin"])

USERS_PAGE_MAX = 100
# Offset pagination is fine at instance-user scale; the bound keeps a hostile
# offset from walking the whole table.
USERS_OFFSET_MAX = 100_000

_llm_tokens = LLMUsage.prompt_tokens + LLMUsage.completion_tokens


async def _scalar(session, stmt) -> int:
    return (await session.scalar(stmt)) or 0


def _count(model_or_col) -> object:
    return select(func.count()).select_from(model_or_col)


@router.get("/overview", response_model=AdminOverviewOut)
async def overview(admin: AdminUser, session: DbSession):
    now = datetime.now(UTC)
    day_ago = now - timedelta(hours=24)
    week_ago_day = (now - timedelta(days=6)).date()
    month_ago_day = (now - timedelta(days=29)).date()

    llm_7d = LLMUsage.created_at >= now - timedelta(days=7)
    tokens_7d = select(func.coalesce(func.sum(_llm_tokens), 0)).where(llm_7d)

    return AdminOverviewOut(
        users_total=await _scalar(session, _count(User)),
        users_new_7d=await _scalar(
            session, _count(User).where(User.created_at >= now - timedelta(days=7))
        ),
        users_suspended=await _scalar(session, _count(User).where(User.status == "suspended")),
        active_today=await _scalar(
            session, _count(UserActivityDay).where(UserActivityDay.day == now.date())
        ),
        active_7d=await _scalar(
            session,
            select(func.count(func.distinct(UserActivityDay.user_id))).where(
                UserActivityDay.day >= week_ago_day
            ),
        ),
        active_30d=await _scalar(
            session,
            select(func.count(func.distinct(UserActivityDay.user_id))).where(
                UserActivityDay.day >= month_ago_day
            ),
        ),
        subscriptions_total=await _scalar(session, _count(Subscription)),
        articles_total=await _scalar(session, _count(Article)),
        articles_ingested_24h=await _scalar(
            session, _count(Article).where(Article.fetched_at >= day_ago)
        ),
        articles_summarized_24h=await _scalar(
            session, _count(Article).where(Article.summary_generated_at >= day_ago)
        ),
        articles_skipped_24h=await _scalar(
            session,
            _count(ArticleProcessingEvent).where(
                ArticleProcessingEvent.outcome == "skipped",
                ArticleProcessingEvent.created_at >= day_ago,
            ),
        ),
        articles_failed_24h=await _scalar(
            session,
            _count(ArticleProcessingEvent).where(
                ArticleProcessingEvent.outcome == "failed",
                ArticleProcessingEvent.created_at >= day_ago,
            ),
        ),
        llm_calls_7d=await _scalar(session, _count(LLMUsage).where(llm_7d)),
        llm_tokens_7d=await _scalar(session, tokens_7d),
        llm_errors_7d=await _scalar(
            session, _count(LLMUsage).where(llm_7d, LLMUsage.status == "error")
        ),
        llm_tokens_7d_user=await _scalar(
            session, tokens_7d.where(LLMUsage.billing_source == "user")
        ),
        llm_tokens_7d_system=await _scalar(
            session, tokens_7d.where(LLMUsage.billing_source == "system")
        ),
    )


async def _per_day(session, day_expr, where, *extra_cols) -> dict:
    """{day: (count, *extra)} for one grouped trend query."""
    rows = await session.execute(
        select(day_expr.label("day"), func.count(), *extra_cols).where(*where).group_by("day")
    )
    return {row[0]: row[1:] for row in rows.all()}


@router.get("/trends", response_model=AdminTrendsOut)
async def trends(
    admin: AdminUser,
    session: DbSession,
    range_: ActivityRange = Query("month", alias="range"),
):
    # UTC bucketing throughout (matching each source column's docs); the one
    # exception is reading_activity, whose `day` is the client-local date.
    today = datetime.now(UTC).date()
    window, start, _ = window_bounds(today, range_)
    # Filter on plain timestamp bounds so the created_at/fetched_at/read_at
    # indexes serve the range scans — wrapping the column in a cast would
    # force full scans. The UTC day conversion happens only in the grouped
    # expression, explicitly, so bucketing never depends on the DB session's
    # time zone.
    start_ts = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    end_ts = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=UTC)

    def span(col):
        return (col >= start_ts, col < end_ts)

    def utc_day(col):
        return cast(func.timezone("UTC", col), SADate)

    new_users = await _per_day(session, utc_day(User.created_at), span(User.created_at))
    actives = await _per_day(
        session, UserActivityDay.day, (UserActivityDay.day >= start, UserActivityDay.day <= today)
    )
    new_subs = await _per_day(
        session, utc_day(Subscription.created_at), span(Subscription.created_at)
    )
    ingested = await _per_day(session, utc_day(Article.fetched_at), span(Article.fetched_at))
    summarized = await _per_day(
        session, utc_day(Article.summary_generated_at), span(Article.summary_generated_at)
    )
    event_day = utc_day(ArticleProcessingEvent.created_at)
    skipped = await _per_day(
        session,
        event_day,
        (*span(ArticleProcessingEvent.created_at), ArticleProcessingEvent.outcome == "skipped"),
    )
    failed = await _per_day(
        session,
        event_day,
        (*span(ArticleProcessingEvent.created_at), ArticleProcessingEvent.outcome == "failed"),
    )
    read = await _per_day(
        session, utc_day(UserArticleState.read_at), span(UserArticleState.read_at)
    )
    reading = await _per_day(
        session,
        ReadingActivity.day,
        (ReadingActivity.day >= start, ReadingActivity.day <= today),
        func.coalesce(func.sum(ReadingActivity.seconds), 0),
    )
    llm = await _per_day(
        session,
        utc_day(LLMUsage.created_at),
        span(LLMUsage.created_at),
        func.coalesce(func.sum(_llm_tokens), 0),
        func.count().filter(LLMUsage.status == "error"),
    )

    days = []
    for offset in range(window):
        day = start + timedelta(days=offset)
        llm_row = llm.get(day)
        days.append(
            AdminTrendDayOut(
                day=day,
                new_users=new_users.get(day, (0,))[0],
                active_users=actives.get(day, (0,))[0],
                new_subscriptions=new_subs.get(day, (0,))[0],
                articles_ingested=ingested.get(day, (0,))[0],
                articles_summarized=summarized.get(day, (0,))[0],
                articles_skipped=skipped.get(day, (0,))[0],
                articles_failed=failed.get(day, (0,))[0],
                articles_read=read.get(day, (0,))[0],
                reading_seconds=reading.get(day, (0, 0))[1],
                llm_calls=llm_row[0] if llm_row else 0,
                llm_tokens=llm_row[1] if llm_row else 0,
                llm_errors=llm_row[2] if llm_row else 0,
            )
        )

    in_range = span(LLMUsage.created_at)
    tokens_sum = func.coalesce(func.sum(_llm_tokens), 0).label("tokens")
    feature_rows = await session.execute(
        select(LLMUsage.feature, func.count(), tokens_sum)
        .where(*in_range)
        .group_by(LLMUsage.feature)
        .order_by(desc("tokens"))
    )
    model_rows = await session.execute(
        select(LLMUsage.provider, LLMUsage.model, func.count(), tokens_sum)
        .where(*in_range)
        .group_by(LLMUsage.provider, LLMUsage.model)
        .order_by(desc("tokens"))
    )
    return AdminTrendsOut(
        range=range_,
        days=days,
        llm_by_feature=[
            UsageFeatureOut(feature=f, calls=c, tokens=t) for f, c, t in feature_rows.all()
        ],
        llm_by_model=[
            UsageModelOut(provider=p, model=m, calls=c, tokens=t) for p, m, c, t in model_rows.all()
        ],
        llm_tokens_user=await _scalar(
            session,
            select(func.coalesce(func.sum(_llm_tokens), 0)).where(
                *in_range, LLMUsage.billing_source == "user"
            ),
        ),
        llm_tokens_system=await _scalar(
            session,
            select(func.coalesce(func.sum(_llm_tokens), 0)).where(
                *in_range, LLMUsage.billing_source == "system"
            ),
        ),
    )


async def _user_aggregates(session, user_ids: list[int]) -> dict[int, dict]:
    """Per-user aggregate columns for one page of users, keyed by user id."""
    if not user_ids:
        return {}
    aggregates: dict[int, dict] = {uid: {} for uid in user_ids}

    async def fold(stmt, key, index=1):
        for row in (await session.execute(stmt)).all():
            aggregates[row[0]][key] = row[index] or 0

    await fold(
        select(UserActivityDay.user_id, func.max(UserActivityDay.day))
        .where(UserActivityDay.user_id.in_(user_ids))
        .group_by(UserActivityDay.user_id),
        "last_active_day",
    )
    await fold(
        select(Subscription.user_id, func.count())
        .where(Subscription.user_id.in_(user_ids))
        .group_by(Subscription.user_id),
        "subscription_count",
    )
    await fold(
        select(UserArticleState.user_id, func.count())
        .where(UserArticleState.user_id.in_(user_ids), UserArticleState.is_read.is_(True))
        .group_by(UserArticleState.user_id),
        "articles_read",
    )
    await fold(
        select(ReadingActivity.user_id, func.coalesce(func.sum(ReadingActivity.seconds), 0))
        .where(ReadingActivity.user_id.in_(user_ids))
        .group_by(ReadingActivity.user_id),
        "reading_seconds",
    )
    await fold(
        select(UserQuotaPeriod.user_id, UserQuotaPeriod.used).where(
            UserQuotaPeriod.user_id.in_(user_ids),
            UserQuotaPeriod.period == quota.current_period(),
        ),
        "quota_used",
    )
    llm_rows = await session.execute(
        select(
            LLMUsage.user_id,
            func.coalesce(func.sum(_llm_tokens), 0),
            func.coalesce(func.sum(_llm_tokens).filter(LLMUsage.billing_source == "system"), 0),
        )
        .where(LLMUsage.user_id.in_(user_ids))
        .group_by(LLMUsage.user_id)
    )
    for uid, tokens, system_tokens in llm_rows.all():
        aggregates[uid]["llm_tokens"] = tokens
        aggregates[uid]["llm_tokens_system"] = system_tokens
    return aggregates


async def _tier_context(session) -> tuple[dict[int, Tier], Tier | None]:
    tiers = (await session.scalars(select(Tier))).all()
    by_id = {tier.id: tier for tier in tiers}
    default = next((t for t in tiers if t.key == settings.default_tier), None)
    return by_id, default


def _user_out(
    user: User, aggregates: dict, tiers_by_id: dict[int, Tier], default: Tier | None
) -> AdminUserOut:
    effective = tiers_by_id.get(user.tier_id) if user.tier_id is not None else default
    return AdminUserOut(
        id=user.id,
        email=user.email,
        username=user.username,
        name=user.name,
        role=user.role,
        status=user.status,
        created_at=user.created_at,
        tier_key=effective.key if effective else "unlimited",
        tier_name=effective.name if effective else "Unlimited",
        tier_assigned=user.tier_id is not None,
        quota_allowance=effective.monthly_article_allowance if effective else None,
        **aggregates,
    )


UserSort = Literal["created_at", "-created_at", "username", "last_active", "-last_active"]


@router.get("/users", response_model=AdminUsersPageOut)
async def list_users(
    admin: AdminUser,
    session: DbSession,
    query: str | None = Query(None, max_length=120, description="matches email/username/name"),
    role: Literal["owner", "admin", "user"] | None = None,
    account_status: Literal["active", "suspended"] | None = Query(None, alias="status"),
    tier: str | None = Query(None, max_length=16, description="effective tiers.key"),
    sort: UserSort = "-created_at",
    limit: int = Query(25, ge=1, le=USERS_PAGE_MAX),
    offset: int = Query(0, ge=0, le=USERS_OFFSET_MAX),
):
    filters = []
    if query:
        needle = f"%{query.strip().lower()}%"
        filters.append(
            or_(
                func.lower(User.email).like(needle),
                func.lower(User.username).like(needle),
                func.lower(User.name).like(needle),
            )
        )
    if role is not None:
        filters.append(User.role == role)
    if account_status is not None:
        filters.append(User.status == account_status)
    tiers_by_id, default = await _tier_context(session)
    if tier is not None:
        row = next((t for t in tiers_by_id.values() if t.key == tier), None)
        if row is None:
            raise HTTPException(status_code=422, detail="Unknown tier")
        # Effective tier: an unassigned user rides the instance default.
        if default is not None and row.id == default.id:
            filters.append(or_(User.tier_id == row.id, User.tier_id.is_(None)))
        else:
            filters.append(User.tier_id == row.id)

    total = await _scalar(session, _count(User).where(*filters))

    last_active = (
        select(UserActivityDay.user_id, func.max(UserActivityDay.day).label("last_day"))
        .group_by(UserActivityDay.user_id)
        .subquery()
    )
    stmt = select(User).outerjoin(last_active, last_active.c.user_id == User.id).where(*filters)
    order = {
        "created_at": (User.created_at.asc(), User.id.asc()),
        "-created_at": (User.created_at.desc(), User.id.desc()),
        "username": (func.lower(User.username).asc(),),
        "last_active": (last_active.c.last_day.asc().nulls_first(), User.id.asc()),
        "-last_active": (last_active.c.last_day.desc().nulls_last(), User.id.desc()),
    }[sort]
    users = (await session.scalars(stmt.order_by(*order).limit(limit).offset(offset))).all()

    aggregates = await _user_aggregates(session, [u.id for u in users])
    return AdminUsersPageOut(
        total=total,
        users=[_user_out(u, aggregates.get(u.id, {}), tiers_by_id, default) for u in users],
    )


async def _target_user(session, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="No such user")
    return user


async def _detail(session, user: User) -> AdminUserOut:
    aggregates = await _user_aggregates(session, [user.id])
    tiers_by_id, default = await _tier_context(session)
    return _user_out(user, aggregates.get(user.id, {}), tiers_by_id, default)


@router.get("/users/{user_id}", response_model=AdminUserOut)
async def get_user(user_id: int, admin: AdminUser, session: DbSession):
    return await _detail(session, await _target_user(session, user_id))


def _audit(session, actor: User, target: User, action: str, before: str, after: str) -> None:
    session.add(
        AdminAuditLog(
            actor_id=actor.id,
            target_user_id=target.id,
            action=action,
            payload={"from": before, "to": after},
        )
    )


@router.patch("/users/{user_id}/role", response_model=AdminUserOut)
async def set_user_role(user_id: int, body: AdminRoleIn, owner: OwnerUser, session: DbSession):
    """Owner-only: promoting/demoting administrators is the owner's call."""
    target = await _target_user(session, user_id)
    before = target.role
    if before != body.role:
        try:
            await change_role(session, target, body.role)
        except FinalOwnerError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        _audit(session, owner, target, "role_change", before, body.role)
        await session.commit()
    return await _detail(session, target)


@router.patch("/users/{user_id}/status", response_model=AdminUserOut)
async def set_user_status(user_id: int, body: AdminStatusIn, admin: AdminUser, session: DbSession):
    target = await _target_user(session, user_id)
    if target.id == admin.id and body.status == "suspended":
        # Self-lockout guard; the final-owner guard below covers the sole
        # owner, this covers every admin acting on themselves.
        raise HTTPException(status_code=409, detail="You cannot suspend your own account")
    if target.role != ROLE_USER and admin.role != ROLE_OWNER:
        raise HTTPException(
            status_code=403, detail="Owner access required to change an administrator's status"
        )
    before = target.status
    if before != body.status:
        try:
            await change_status(session, target, body.status)
        except FinalOwnerError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        _audit(session, admin, target, "status_change", before, body.status)
        await session.commit()
    return await _detail(session, target)


@router.patch("/users/{user_id}/tier", response_model=AdminUserOut)
async def set_user_tier(user_id: int, body: AdminTierIn, admin: AdminUser, session: DbSession):
    """Manually assign a tier (tiers.key), or null to revert to the instance
    default. Takes effect immediately: the next charge uses the new
    allowance; nothing already used this month is clawed back, and a user
    moved below their current usage simply stops accruing new charges."""
    target = await _target_user(session, user_id)
    new_tier = None
    if body.tier is not None:
        new_tier = await session.scalar(select(Tier).where(Tier.key == body.tier))
        if new_tier is None:
            raise HTTPException(status_code=422, detail="Unknown tier")
    before_id = target.tier_id
    after_id = new_tier.id if new_tier else None
    if before_id != after_id:
        tiers_by_id, _ = await _tier_context(session)
        before_key = tiers_by_id[before_id].key if before_id in tiers_by_id else "default"
        _audit(
            session,
            admin,
            target,
            "tier_change",
            before_key,
            new_tier.key if new_tier else "default",
        )
        target.tier_id = after_id
        await session.commit()
    return await _detail(session, target)
