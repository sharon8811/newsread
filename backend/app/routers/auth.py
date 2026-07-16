from fastapi import APIRouter, HTTPException
from sqlalchemy import func, or_, select

from ..config import settings
from ..deps import CurrentUser, DbSession
from ..models import User
from ..schemas import LoginIn, RegisterIn, TokenOut, UserOut
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


async def signup_open(session: DbSession) -> bool:
    """Whether /auth/register currently accepts new accounts. With signups
    disabled (single-user self-hosted default), a fresh instance still lets
    the first account through so the owner can be created from the normal
    register form."""
    if settings.allow_signup:
        return True
    return (await session.scalar(select(func.count()).select_from(User))) == 0


@router.post("/register", response_model=TokenOut, status_code=201)
async def register(body: RegisterIn, session: DbSession):
    if not await signup_open(session):
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
    return TokenOut(access_token=create_access_token(user.id), user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return UserOut.model_validate(user)


@router.post("/refresh", response_model=TokenOut)
async def refresh(user: CurrentUser):
    """Sliding session: trade a still-valid token for a fresh one. Mobile
    clients call this on app launch so users never hit the 30-day expiry."""
    return TokenOut(access_token=create_access_token(user.id), user=UserOut.model_validate(user))
