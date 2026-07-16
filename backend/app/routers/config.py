"""Runtime server configuration for clients. Unauthenticated by design:
login/register screens need these flags before any token exists. The flags
themselves live in Settings (derived from NEWSREAD_DEPLOYMENT, individually
overridable) — this endpoint is their single source of truth for web and
mobile, so a flag flip is a backend restart, never a frontend rebuild."""

from fastapi import APIRouter

from ..config import settings
from ..deps import DbSession
from ..schemas import ServerConfigOut
from .auth import signup_open

router = APIRouter(tags=["config"])


@router.get("/config", response_model=ServerConfigOut)
async def server_config(session: DbSession):
    return ServerConfigOut(
        allow_signup=await signup_open(session),
        messaging_enabled=settings.messaging_enabled,
    )
