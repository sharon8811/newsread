"""Browser-history connections and synchronized capture policy."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..deps import CurrentUser, DbSession
from ..history_auth import (
    BrowserConnectionAuth,
    generate_browser_token,
    require_browser_history_enabled,
)
from ..models import (
    BrowserConnection,
    BrowserHistoryDomainRule,
    BrowserHistorySettings,
    User,
)
from ..schemas import (
    BrowserConnectionCreatedOut,
    BrowserConnectionCreateIn,
    BrowserConnectionOut,
    BrowserHistoryDomainRuleIn,
    BrowserHistoryDomainRuleOut,
    BrowserHistorySettingsIn,
    BrowserHistorySettingsOut,
    BrowserHistorySyncStatusOut,
)

router = APIRouter(
    prefix="/history",
    tags=["browser-history"],
    dependencies=[Depends(require_browser_history_enabled)],
)

TOKEN_CREATION_LIMIT = 10
TOKEN_CREATION_WINDOW = timedelta(hours=1)


async def _settings_for(session: DbSession, user_id: int) -> BrowserHistorySettings:
    await session.execute(
        pg_insert(BrowserHistorySettings)
        .values(user_id=user_id)
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    return await session.get(BrowserHistorySettings, user_id)


def _connection_out(connection: BrowserConnection) -> BrowserConnectionOut:
    return BrowserConnectionOut.model_validate(connection)


def _settings_out(history_settings: BrowserHistorySettings) -> BrowserHistorySettingsOut:
    return BrowserHistorySettingsOut(
        retention_days=history_settings.retention_days,
        sync_revision=history_settings.sync_revision,
    )


@router.post(
    "/connections",
    response_model=BrowserConnectionCreatedOut,
    status_code=201,
)
async def create_connection(
    body: BrowserConnectionCreateIn,
    response: Response,
    user: CurrentUser,
    session: DbSession,
):
    window_start = datetime.now(UTC) - TOKEN_CREATION_WINDOW
    recent_tokens = await session.scalar(
        select(func.count())
        .select_from(BrowserConnection)
        .where(
            BrowserConnection.user_id == user.id,
            BrowserConnection.created_at >= window_start,
        )
    )
    if recent_tokens >= TOKEN_CREATION_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Too many browser connections created; try again later",
            headers={"Retry-After": str(int(TOKEN_CREATION_WINDOW.total_seconds()))},
        )

    await _settings_for(session, user.id)
    for _ in range(3):
        token, prefix, token_hash = generate_browser_token()
        exists = await session.scalar(
            select(BrowserConnection.id).where(BrowserConnection.token_prefix == prefix)
        )
        if exists is None:
            break
    else:  # pragma: no cover - cryptographically implausible without monkeypatching
        raise HTTPException(status_code=503, detail="Could not create a browser connection")

    connection = BrowserConnection(
        user_id=user.id,
        name=body.name,
        token_prefix=prefix,
        token_hash=token_hash,
    )
    session.add(connection)
    await session.commit()
    await session.refresh(connection)
    response.headers["Cache-Control"] = "no-store"
    return BrowserConnectionCreatedOut(
        **_connection_out(connection).model_dump(),
        token=token,
    )


@router.get("/connections", response_model=list[BrowserConnectionOut])
async def list_connections(user: CurrentUser, session: DbSession):
    connections = (
        await session.scalars(
            select(BrowserConnection)
            .where(BrowserConnection.user_id == user.id)
            .order_by(BrowserConnection.created_at.desc(), BrowserConnection.id.desc())
        )
    ).all()
    return [_connection_out(connection) for connection in connections]


@router.delete("/connections/{connection_id}", status_code=204)
async def revoke_connection(
    connection_id: int,
    user: CurrentUser,
    session: DbSession,
):
    connection = await session.scalar(
        select(BrowserConnection).where(
            BrowserConnection.id == connection_id,
            BrowserConnection.user_id == user.id,
        )
    )
    if connection is None:
        raise HTTPException(status_code=404, detail="Browser connection not found")
    if connection.revoked_at is None:
        connection.revoked_at = datetime.now(UTC)
        await session.commit()


@router.get("/settings", response_model=BrowserHistorySettingsOut)
async def get_history_settings(user: CurrentUser, session: DbSession):
    history_settings = await _settings_for(session, user.id)
    await session.commit()
    return _settings_out(history_settings)


@router.patch("/settings", response_model=BrowserHistorySettingsOut)
async def update_history_settings(
    body: BrowserHistorySettingsIn,
    user: CurrentUser,
    session: DbSession,
):
    history_settings = await _settings_for(session, user.id)
    if "retention_days" in body.model_fields_set:
        history_settings.retention_days = body.retention_days
    await session.commit()
    await session.refresh(history_settings)
    return _settings_out(history_settings)


@router.get("/domain-rules", response_model=list[BrowserHistoryDomainRuleOut])
async def list_domain_rules(user: CurrentUser, session: DbSession):
    return (
        await session.scalars(
            select(BrowserHistoryDomainRule)
            .where(BrowserHistoryDomainRule.user_id == user.id)
            .order_by(
                BrowserHistoryDomainRule.hostname,
                BrowserHistoryDomainRule.match_subdomains,
            )
        )
    ).all()


@router.post(
    "/domain-rules",
    response_model=BrowserHistoryDomainRuleOut,
    status_code=201,
)
async def upsert_domain_rule(
    body: BrowserHistoryDomainRuleIn,
    user: CurrentUser,
    session: DbSession,
):
    history_settings = await _settings_for(session, user.id)
    rule = await session.scalar(
        select(BrowserHistoryDomainRule).where(
            BrowserHistoryDomainRule.user_id == user.id,
            BrowserHistoryDomainRule.hostname == body.hostname,
            BrowserHistoryDomainRule.match_subdomains == body.match_subdomains,
        )
    )
    if rule is None:
        rule = BrowserHistoryDomainRule(
            user_id=user.id,
            hostname=body.hostname,
            match_subdomains=body.match_subdomains,
            mode=body.mode,
        )
        session.add(rule)
    else:
        rule.mode = body.mode
    history_settings.sync_revision += 1
    await session.commit()
    await session.refresh(rule)
    return rule


@router.delete("/domain-rules/{rule_id}", status_code=204)
async def delete_domain_rule(
    rule_id: int,
    user: CurrentUser,
    session: DbSession,
):
    rule = await session.scalar(
        select(BrowserHistoryDomainRule).where(
            BrowserHistoryDomainRule.id == rule_id,
            BrowserHistoryDomainRule.user_id == user.id,
        )
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="Domain rule not found")
    history_settings = await _settings_for(session, user.id)
    history_settings.sync_revision += 1
    await session.delete(rule)
    await session.commit()


@router.get("/sync/status", response_model=BrowserHistorySyncStatusOut)
async def sync_status(
    connection: BrowserConnectionAuth,
    session: DbSession,
):
    history_settings = await _settings_for(session, connection.user_id)
    user_name = await session.scalar(select(User.name).where(User.id == connection.user_id))
    rules = (
        await session.scalars(
            select(BrowserHistoryDomainRule)
            .where(BrowserHistoryDomainRule.user_id == connection.user_id)
            .order_by(
                BrowserHistoryDomainRule.hostname,
                BrowserHistoryDomainRule.match_subdomains,
            )
        )
    ).all()
    connection.last_seen_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(connection)
    return BrowserHistorySyncStatusOut(
        connection=_connection_out(connection),
        user_name=user_name,
        settings=_settings_out(history_settings),
        domain_rules=[BrowserHistoryDomainRuleOut.model_validate(rule) for rule in rules],
    )
