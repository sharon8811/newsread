"""Messaging platform connections, saved quick-share targets, and external
sends. OAuth here is the reverse of auth.py's: NewsRead is the *client*
against Slack/Microsoft, and the resulting per-user tokens are stored
Fernet-encrypted (crypto.py)."""

import logging
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crypto
from ..access import accessible_article
from ..config import settings
from ..deps import CurrentUser, DbSession
from ..messaging import ADAPTERS, MessagingError
from ..models import ExternalShare, MessagingConnection, ShareTarget
from ..schemas import (
    AuthorizeUrlOut,
    ExternalShareIn,
    ExternalShareOut,
    IntegrationStatusOut,
    ShareTargetIn,
    ShareTargetOut,
    TargetOptionOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["integrations"])

# The state param carries the user through the OAuth redirect (the callback
# arrives from the browser with no Authorization header). Signed with a key
# derived from jwt_secret — NOT jwt_secret itself — so a state token can never
# pass security.get_current_user as an access token.
_STATE_TTL = timedelta(minutes=10)


def _state_key() -> str:
    return settings.jwt_secret + ":oauth-state"


def _make_state(user_id: int, platform: str) -> str:
    payload = {
        "sub": str(user_id),
        "platform": platform,
        "exp": datetime.now(UTC) + _STATE_TTL,
    }
    return jwt.encode(payload, _state_key(), algorithm="HS256")


def _read_state(state: str, platform: str) -> int:
    try:
        payload = jwt.decode(state, _state_key(), algorithms=["HS256"])
        if payload["platform"] != platform:
            raise ValueError("platform mismatch")
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state") from None


def _adapter_or_404(platform: str):
    adapter = ADAPTERS.get(platform)
    if adapter is None:
        raise HTTPException(status_code=404, detail="Unknown platform")
    return adapter


async def _connection_or_409(
    session: AsyncSession, user_id: int, platform: str
) -> MessagingConnection:
    connection = await session.scalar(
        select(MessagingConnection).where(
            MessagingConnection.user_id == user_id,
            MessagingConnection.platform == platform,
        )
    )
    if connection is None:
        raise HTTPException(status_code=409, detail=f"{platform} is not connected")
    return connection


async def _fresh_token(session: AsyncSession, connection: MessagingConnection) -> str:
    """Decrypted access token, refreshed first when it's near expiry (Teams).
    A failed refresh flips the connection to 'error' so the UI offers
    reconnect instead of retrying into the same wall."""
    if (
        connection.token_expires_at is not None
        and connection.token_expires_at <= datetime.now(UTC)
        and connection.refresh_token_enc
    ):
        adapter = ADAPTERS[connection.platform]
        try:
            access, refresh, expires_at = await adapter.refresh_tokens(
                crypto.decrypt_token(connection.refresh_token_enc)
            )
        except MessagingError as exc:
            if exc.reconnect:
                connection.status = "error"
                await session.commit()
            raise
        connection.access_token_enc = crypto.encrypt_token(access)
        if refresh:
            connection.refresh_token_enc = crypto.encrypt_token(refresh)
        connection.token_expires_at = expires_at
        connection.status = "active"
        await session.commit()
    return crypto.decrypt_token(connection.access_token_enc)


# --- connections ---


@router.get("/integrations", response_model=list[IntegrationStatusOut])
async def integration_status(
    user: CurrentUser,
    session: DbSession,
):
    connections = {
        c.platform: c
        for c in await session.scalars(
            select(MessagingConnection).where(MessagingConnection.user_id == user.id)
        )
    }
    out = []
    for platform, adapter in ADAPTERS.items():
        connection = connections.get(platform)
        out.append(
            IntegrationStatusOut(
                platform=platform,
                configured=settings.messaging_enabled
                and adapter.is_configured()
                and crypto.is_configured(),
                connected=connection is not None,
                status=connection.status if connection else None,
                workspace_name=connection.workspace_name if connection else None,
                account_name=connection.account_name if connection else None,
            )
        )
    return out


@router.get("/integrations/{platform}/authorize", response_model=AuthorizeUrlOut)
async def authorize(
    platform: str,
    user: CurrentUser,
):
    adapter = _adapter_or_404(platform)
    if not settings.messaging_enabled:
        raise HTTPException(
            status_code=503,
            detail="Messaging integrations are disabled on this server",
        )
    if not adapter.is_configured():
        raise HTTPException(
            status_code=503,
            detail=f"{platform} credentials are not configured on the server",
        )
    if not crypto.is_configured():
        raise HTTPException(
            status_code=503, detail="NEWSREAD_TOKEN_ENCRYPTION_KEY is not configured"
        )
    return AuthorizeUrlOut(url=adapter.authorize_url(_make_state(user.id, platform)))


@router.get("/integrations/{platform}/callback", include_in_schema=False)
async def oauth_callback(
    platform: str,
    session: DbSession,
    state: str = "",
    code: str = "",
    error: str = "",
):
    """Unauthenticated by design — the browser lands here from the provider;
    identity comes from the signed state. Always redirects to settings."""
    adapter = _adapter_or_404(platform)
    settings_url = f"{settings.frontend_base_url.rstrip('/')}/settings"
    if error:  # user hit "cancel" on the consent screen
        return RedirectResponse(f"{settings_url}?error={platform}:{error[:80]}")
    if not state or not code:
        return RedirectResponse(f"{settings_url}?error={platform}:missing_code")
    user_id = _read_state(state, platform)

    try:
        result = await adapter.exchange_code(code)
    except MessagingError as exc:
        logger.warning("%s code exchange failed for user %s: %s", platform, user_id, exc)
        return RedirectResponse(f"{settings_url}?error={platform}:exchange_failed")

    connection = await session.scalar(
        select(MessagingConnection).where(
            MessagingConnection.user_id == user_id,
            MessagingConnection.platform == platform,
        )
    )
    if connection is None:
        connection = MessagingConnection(user_id=user_id, platform=platform)
        session.add(connection)
    connection.external_account_id = result.external_account_id
    connection.account_name = result.account_name
    connection.workspace_id = result.workspace_id
    connection.workspace_name = result.workspace_name
    connection.access_token_enc = crypto.encrypt_token(result.access_token)
    connection.refresh_token_enc = (
        crypto.encrypt_token(result.refresh_token) if result.refresh_token else None
    )
    connection.token_expires_at = result.token_expires_at
    connection.scopes = result.scopes
    connection.status = "active"
    await session.commit()
    return RedirectResponse(f"{settings_url}?connected={platform}")


@router.delete("/integrations/{platform}", status_code=204)
async def disconnect(
    platform: str,
    user: CurrentUser,
    session: DbSession,
):
    _adapter_or_404(platform)
    connection = await _connection_or_409(session, user.id, platform)
    await session.delete(connection)  # saved targets cascade
    await session.commit()


@router.get("/integrations/{platform}/targets", response_model=list[TargetOptionOut])
async def search_targets(
    platform: str,
    user: CurrentUser,
    session: DbSession,
    q: str = "",
):
    """Live channel/chat list proxied from the platform, with saved ones marked."""
    adapter = _adapter_or_404(platform)
    connection = await _connection_or_409(session, user.id, platform)
    token = await _fresh_token(session, connection)
    try:
        options = await adapter.list_targets(token, connection.external_account_id, q.strip())
    except MessagingError as exc:
        if exc.reconnect:
            connection.status = "error"
            await session.commit()
        raise HTTPException(
            status_code=502, detail={"message": str(exc), "reconnect": exc.reconnect}
        ) from exc
    saved = {
        t.external_id: t.id
        for t in await session.scalars(
            select(ShareTarget).where(ShareTarget.connection_id == connection.id)
        )
    }
    return [
        TargetOptionOut(
            external_id=o.external_id,
            display_name=o.display_name,
            target_type=o.target_type,
            meta=o.meta,
            saved_id=saved.get(o.external_id),
        )
        for o in options
    ]


# --- saved quick-share targets ---


def _target_out(target: ShareTarget, platform: str) -> ShareTargetOut:
    return ShareTargetOut(
        id=target.id,
        platform=platform,
        external_id=target.external_id,
        display_name=target.display_name,
        target_type=target.target_type,
        meta=target.meta or {},
        last_used_at=target.last_used_at,
    )


@router.get("/share-targets", response_model=list[ShareTargetOut])
async def list_share_targets(
    user: CurrentUser,
    session: DbSession,
):
    rows = (
        await session.execute(
            select(ShareTarget, MessagingConnection.platform)
            .join(MessagingConnection, MessagingConnection.id == ShareTarget.connection_id)
            .where(ShareTarget.user_id == user.id)
            .order_by(ShareTarget.sort_order, ShareTarget.id)
        )
    ).all()
    return [_target_out(target, platform) for target, platform in rows]


@router.post("/share-targets", response_model=ShareTargetOut, status_code=201)
async def save_share_target(
    body: ShareTargetIn,
    user: CurrentUser,
    session: DbSession,
):
    connection = await _connection_or_409(session, user.id, body.platform)
    existing = await session.scalar(
        select(ShareTarget).where(
            ShareTarget.connection_id == connection.id,
            ShareTarget.external_id == body.external_id,
        )
    )
    if existing is not None:
        return _target_out(existing, body.platform)
    max_order = (
        await session.scalar(
            select(func.max(ShareTarget.sort_order)).where(
                ShareTarget.connection_id == connection.id
            )
        )
        or 0
    )
    target = ShareTarget(
        user_id=user.id,
        connection_id=connection.id,
        target_type=body.target_type,
        external_id=body.external_id,
        display_name=body.display_name,
        meta=body.meta or {},
        sort_order=max_order + 1,
    )
    session.add(target)
    await session.commit()
    await session.refresh(target)
    return _target_out(target, body.platform)


@router.delete("/share-targets/{target_id}", status_code=204)
async def delete_share_target(
    target_id: int,
    user: CurrentUser,
    session: DbSession,
):
    target = await session.get(ShareTarget, target_id)
    if target is None or target.user_id != user.id:
        raise HTTPException(status_code=404, detail="Target not found")
    await session.delete(target)
    await session.commit()


# --- sending ---


@router.post("/shares/external", response_model=ExternalShareOut, status_code=201)
async def send_external_share(
    body: ExternalShareIn,
    user: CurrentUser,
    session: DbSession,
):
    """Send one article to one target, synchronously — the user is waiting in
    the composer and deserves a real success/failure, not a queue ticket."""
    article = await accessible_article(session, user.id, body.article_id)

    saved_target: ShareTarget | None = None
    if body.target_id is not None:
        saved_target = await session.get(ShareTarget, body.target_id)
        if saved_target is None or saved_target.user_id != user.id:
            raise HTTPException(status_code=404, detail="Target not found")
        connection = await session.get(MessagingConnection, saved_target.connection_id)
        target_type = saved_target.target_type
        external_id = saved_target.external_id
        meta = saved_target.meta or {}
        display = saved_target.display_name
    elif body.target is not None:
        connection = await _connection_or_409(session, user.id, body.target.platform)
        target_type = body.target.target_type
        external_id = body.target.external_id
        meta = body.target.meta or {}
        display = body.target.display_name
    else:
        raise HTTPException(status_code=422, detail="Provide target_id or target")

    adapter = ADAPTERS[connection.platform]
    token = await _fresh_token(session, connection)

    record = ExternalShare(
        user_id=user.id,
        article_id=article.id,
        platform=connection.platform,
        target_display=display,
        message=body.message.strip(),
        status="sent",
    )
    try:
        await adapter.send_message(
            token,
            target_type,
            external_id,
            meta,
            body.message,
            article.url,
            article.title,
        )
    except MessagingError as exc:
        if exc.reconnect:
            connection.status = "error"
        record.status = "failed"
        record.error = str(exc)
        session.add(record)
        await session.commit()
        raise HTTPException(
            status_code=502, detail={"message": str(exc), "reconnect": exc.reconnect}
        ) from exc

    if saved_target is not None:
        saved_target.last_used_at = datetime.now(UTC)
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record
