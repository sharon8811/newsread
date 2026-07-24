"""Scoped authentication for paired browser-history extensions."""

import hashlib
import hmac
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_session
from .models import BrowserConnection

TOKEN_PREFIX = "nrh_"
_bearer = HTTPBearer(auto_error=False, scheme_name="BrowserHistoryToken")


def require_browser_history_enabled() -> None:
    if not settings.browser_history_enabled:
        raise HTTPException(status_code=404, detail="Browser history is not enabled")


def generate_browser_token() -> tuple[str, str, str]:
    """Return (raw token, lookup prefix, SHA-256 digest)."""
    prefix = f"{TOKEN_PREFIX}{secrets.token_urlsafe(9)}"
    token = f"{prefix}.{secrets.token_urlsafe(32)}"
    return token, prefix, hash_browser_token(token)


def hash_browser_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def browser_token_prefix(token: str) -> str | None:
    prefix, separator, secret = token.partition(".")
    if (
        separator != "."
        or not prefix.startswith(TOKEN_PREFIX)
        or len(prefix) > 24
        or not secret
        or len(token) > 128
    ):
        return None
    return prefix


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid browser connection token",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_browser_connection(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> BrowserConnection:
    if credentials is None:
        raise _unauthorized()
    prefix = browser_token_prefix(credentials.credentials)
    if prefix is None:
        raise _unauthorized()
    connection = await session.scalar(
        select(BrowserConnection).where(
            BrowserConnection.token_prefix == prefix,
            BrowserConnection.revoked_at.is_(None),
        )
    )
    if connection is None or not hmac.compare_digest(
        connection.token_hash,
        hash_browser_token(credentials.credentials),
    ):
        raise _unauthorized()
    return connection


BrowserConnectionAuth = Annotated[BrowserConnection, Depends(get_browser_connection)]
