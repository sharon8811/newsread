from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from . import user_activity
from .config import settings
from .db import get_session
from .models import User
from .roles import ADMIN_ROLES, ROLE_OWNER, STATUS_SUSPENDED

bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(settings.bcrypt_rounds)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_access_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(UTC) + timedelta(days=settings.jwt_expires_days),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized from None
    user = await session.get(User, user_id)
    if user is None:
        raise unauthorized
    # The user row is loaded fresh on every request, so suspension takes
    # effect immediately — a previously issued, still-valid JWT is rejected.
    if user.status == STATUS_SUSPENDED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account suspended")
    # Daily-active tracking: a dict lookup on all but ~one request per user
    # per hour (user_activity.record throttles and never raises). Runs on
    # this request's session — a second connection here could starve the
    # pool when many cold users authenticate at once.
    await user_activity.record(session, user.id)
    return user


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    """Admin-or-owner gate for administration routes. Authorization lives on
    users.role; the deployment mode never grants access."""
    if user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


async def get_current_owner(user: User = Depends(get_current_user)) -> User:
    """Owner-only gate (admin promotion/demotion, instance settings)."""
    if user.role != ROLE_OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required")
    return user
