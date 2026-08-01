from fastapi import APIRouter, HTTPException
from sqlalchemy import func, or_, select, text

from ..config import settings
from ..deps import CurrentUser, DbSession
from ..models import User
from ..roles import ROLE_OWNER, ROLE_USER, STATUS_SUSPENDED
from ..schemas import LoginIn, RegisterIn, TokenOut, UserOut
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# Arbitrary app-wide key serializing "is this the first account?" checks so
# two concurrent registrations on an empty instance can't both become owner
# (or both slip past closed signups). Held until the transaction commits.
_FIRST_ACCOUNT_LOCK_KEY = 0x52454749


async def _user_count(session: DbSession) -> int:
    return (await session.scalar(select(func.count()).select_from(User))) or 0


async def signup_open(session: DbSession) -> bool:
    """Whether /auth/register currently accepts new accounts. With signups
    disabled (single-user self-hosted default), a fresh instance still lets
    the first account through so the owner can be created from the normal
    register form."""
    if settings.allow_signup:
        return True
    return await _user_count(session) == 0


@router.post("/register", response_model=TokenOut, status_code=201)
async def register(body: RegisterIn, session: DbSession):
    user_count = await _user_count(session)
    if user_count == 0:
        # The empty-instance paths below (first-account signup exception,
        # first-account-becomes-owner) both decide on the count; serialize
        # them and recount under the lock.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": _FIRST_ACCOUNT_LOCK_KEY}
        )
        user_count = await _user_count(session)
    if not settings.allow_signup and user_count > 0:
        raise HTTPException(status_code=403, detail="Signups are disabled on this server")
    existing = await session.scalar(
        select(User).where(
            or_(
                func.lower(User.email) == body.email.lower(),
                func.lower(User.username) == body.username.lower(),
            )
        )
    )
    if existing:
        field = "email" if existing.email.lower() == body.email.lower() else "username"
        raise HTTPException(status_code=409, detail=f"That {field} is already taken")

    user = User(
        email=body.email.lower(),
        username=body.username,
        name=body.name,
        password_hash=hash_password(body.password),
        # Bootstrap: on self_hosted the first account is the operator and
        # becomes owner (config.first_account_owner). Hosted deployments keep
        # this off so public signup can never mint an owner.
        role=ROLE_OWNER if settings.first_account_owner and user_count == 0 else ROLE_USER,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return TokenOut(access_token=create_access_token(user.id), user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, session: DbSession):
    identifier = body.identifier.strip().lower()
    user = await session.scalar(
        select(User).where(
            or_(
                func.lower(User.email) == identifier,
                func.lower(User.username) == identifier,
            )
        )
    )
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if user.status == STATUS_SUSPENDED:
        raise HTTPException(status_code=403, detail="Account suspended")
    return TokenOut(access_token=create_access_token(user.id), user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return UserOut.model_validate(user)


@router.post("/refresh", response_model=TokenOut)
async def refresh(user: CurrentUser):
    """Sliding session: trade a still-valid token for a fresh one. Mobile
    clients call this on app launch so users never hit the 30-day expiry."""
    return TokenOut(access_token=create_access_token(user.id), user=UserOut.model_validate(user))
