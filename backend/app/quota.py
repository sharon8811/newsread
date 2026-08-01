"""Monthly article allowances (#119).

The qualifying event is AI processing actually performed with the user as a
beneficiary: the batch worker charges a feed's active subscribers when it
summarizes an article for them, and the on-demand paths (summarize button,
URL import) charge the acting user before spending. A charge exists at most
once per (user, article) ever — user_article_charges' unique key makes
duplicate jobs, retries, regenerations, and cached/copied deliveries free by
construction. Subscribing to a feed later never charges for its backlog.

Counters are per UTC calendar month (user_quota_periods, one row per user
per period). Increments run under SELECT ... FOR UPDATE on the period row,
so concurrent workers and requests can't race a finite allowance past its
limit. The period row snapshots the tier key/allowance in force, keeping
historic months queryable after the tier table is retuned; a mid-month tier
change moves the snapshot forward on the next charge, and moving below the
already-used amount simply stops further charges — nothing retroactive.

Owners and admins are exempt from enforcement (operational access must not
be quota-blocked) but their usage is still recorded, like unlimited tiers.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .models import Article, Subscription, Tier, User, UserArticleCharge, UserQuotaPeriod
from .roles import ADMIN_ROLES, STATUS_ACTIVE

logger = logging.getLogger(__name__)

# Seed defaults; rows are data and stay editable (seeds.seed_tiers inserts
# only missing keys). Prices are informational metadata in this phase.
DEFAULT_TIERS = (
    {"key": "free", "name": "Free", "price_cents": 0, "monthly_article_allowance": 100},
    {"key": "paid", "name": "Paid", "price_cents": 500, "monthly_article_allowance": 1000},
    {
        "key": "unlimited",
        "name": "Unlimited",
        "price_cents": 2000,
        "monthly_article_allowance": None,
    },
)


def current_period(now: datetime | None = None) -> date:
    """First day of the current UTC calendar month."""
    today = (now or datetime.now(UTC)).date()
    return today.replace(day=1)


def next_reset(period: date) -> date:
    if period.month == 12:
        return date(period.year + 1, 1, 1)
    return date(period.year, period.month + 1, 1)


async def default_tier(session: AsyncSession) -> Tier | None:
    return await session.scalar(select(Tier).where(Tier.key == settings.default_tier))


async def resolve_tier(session: AsyncSession, user: User) -> Tier | None:
    """The tier governing this user: their assigned one, else the instance
    default. None (seed missing) reads as unlimited — a misconfigured tier
    table must never lock everyone out of AI processing."""
    if user.tier_id is not None:
        tier = await session.get(Tier, user.tier_id)
        if tier is not None:
            return tier
    return await default_tier(session)


@dataclass(frozen=True)
class QuotaStatus:
    tier_key: str
    tier_name: str
    allowance: int | None  # None = unlimited
    used: int
    period_start: date
    resets_on: date
    exempt: bool  # owner/admin: recorded but never enforced


async def status_for(session: AsyncSession, user: User) -> QuotaStatus:
    period = current_period()
    tier = await resolve_tier(session, user)
    used = (
        await session.scalar(
            select(UserQuotaPeriod.used).where(
                UserQuotaPeriod.user_id == user.id, UserQuotaPeriod.period == period
            )
        )
    ) or 0
    return QuotaStatus(
        tier_key=tier.key if tier else "unlimited",
        tier_name=tier.name if tier else "Unlimited",
        allowance=tier.monthly_article_allowance if tier else None,
        used=used,
        period_start=period,
        resets_on=next_reset(period),
        exempt=user.role in ADMIN_ROLES,
    )


async def _locked_period(session: AsyncSession, user: User, period: date) -> UserQuotaPeriod:
    await session.execute(
        pg_insert(UserQuotaPeriod)
        .values(user_id=user.id, period=period, used=0)
        .on_conflict_do_nothing(index_elements=["user_id", "period"])
    )
    return await session.scalar(
        select(UserQuotaPeriod)
        .where(UserQuotaPeriod.user_id == user.id, UserQuotaPeriod.period == period)
        .with_for_update()
    )


@dataclass(frozen=True)
class ChargeResult:
    allowed: bool  # False = finite allowance exhausted, nothing recorded
    charged: bool  # a new charge row was recorded (refundable this period)


DENIED = ChargeResult(allowed=False, charged=False)
FREE = ChargeResult(allowed=True, charged=False)
CHARGED = ChargeResult(allowed=True, charged=True)


async def try_charge(session: AsyncSession, user: User, article_id: int) -> ChargeResult:
    """Charge one article to the user for the current period, at most once
    per (user, article) ever. The caller commits; the period-row lock is
    held until then, serializing concurrent charges for the same user."""
    # Already charged (any period): regenerations, retries, translations and
    # re-deliveries of the same article are free, forever.
    existing = await session.scalar(
        select(UserArticleCharge.id).where(
            UserArticleCharge.user_id == user.id, UserArticleCharge.article_id == article_id
        )
    )
    if existing is not None:
        return FREE

    period = current_period()
    tier = await resolve_tier(session, user)
    allowance = tier.monthly_article_allowance if tier else None
    row = await _locked_period(session, user, period)

    enforced = user.role not in ADMIN_ROLES and allowance is not None
    if enforced and row.used >= allowance:
        return DENIED

    inserted = await session.execute(
        pg_insert(UserArticleCharge)
        .values(user_id=user.id, article_id=article_id, period=period)
        .on_conflict_do_nothing(index_elements=["user_id", "article_id"])
    )
    if not inserted.rowcount:
        return FREE  # lost a same-article race: the winner's charge stands
    row.used += 1
    row.tier_key_snapshot = tier.key if tier else "unlimited"
    row.allowance_snapshot = allowance
    return CHARGED


async def refund(session: AsyncSession, user: User, article_id: int) -> None:
    """Undo a reservation whose processing delivered nothing (no summary was
    stored): you are only charged for articles actually processed for you.
    The charge's own recorded period is decremented — a reservation made
    just before a UTC month boundary refunds correctly after it. Callers
    only refund charges they made (ChargeResult.charged), so the row found
    here is always this operation's reservation. Commits."""
    charge = await session.scalar(
        select(UserArticleCharge).where(
            UserArticleCharge.user_id == user.id,
            UserArticleCharge.article_id == article_id,
        )
    )
    if charge is not None:
        period = charge.period
        await session.delete(charge)
        await session.flush()
        row = await _locked_period(session, user, period)
        row.used = max(0, row.used - 1)
    await session.commit()


def subscriber_with_quota_exists(default: Tier | None, period: date):
    """EXISTS (correlated on Article.feed_id): the feed has an active
    subscriber who could still be charged this period. The batch worker adds
    this to its summarize-eligibility query so it never spends LLM tokens on
    an article no subscriber can pay for. Mirrors try_charge's decision:
    exempt roles, unlimited tiers, or used < allowance."""
    used = func.coalesce(
        select(UserQuotaPeriod.used)
        .where(UserQuotaPeriod.user_id == User.id, UserQuotaPeriod.period == period)
        .correlate(User)
        .scalar_subquery(),
        0,
    )
    assigned_ok = and_(
        User.tier_id.is_not(None),
        or_(Tier.monthly_article_allowance.is_(None), used < Tier.monthly_article_allowance),
    )
    if default is None or default.monthly_article_allowance is None:
        default_ok = User.tier_id.is_(None)
    else:
        default_ok = and_(User.tier_id.is_(None), used < default.monthly_article_allowance)
    return (
        select(Subscription.id)
        .join(User, User.id == Subscription.user_id)
        .join(Tier, Tier.id == User.tier_id, isouter=True)
        .where(
            Subscription.feed_id == Article.feed_id,
            User.status == STATUS_ACTIVE,
            or_(User.role.in_(tuple(ADMIN_ROLES)), assigned_ok, default_ok),
        )
        .exists()
    )


async def charge_subscribers(session: AsyncSession, article: Article) -> int:
    """Charge every active subscriber of the article's feed — the batch
    worker's post-summary step. Exhausted subscribers simply aren't charged:
    the article row is shared, so they still see the stored summary (their
    quota only stops NewsRead spending *because of them*). Later subscribers
    are never back-charged — charges happen only at processing time.
    Commits per subscriber so the period-row lock is held briefly."""
    subscribers = (
        await session.scalars(
            select(User)
            .join(Subscription, Subscription.user_id == User.id)
            .where(Subscription.feed_id == article.feed_id, User.status == STATUS_ACTIVE)
            .order_by(User.id)
        )
    ).all()
    charged = 0
    for user in subscribers:
        result = await try_charge(session, user, article.id)
        if result.charged:
            charged += 1
        await session.commit()
    return charged
