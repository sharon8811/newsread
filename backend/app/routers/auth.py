from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import User
from ..schemas import LoginIn, RegisterIn, TokenOut, UserOut
from ..security import create_access_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=201)
async def register(body: RegisterIn, session: AsyncSession = Depends(get_session)):
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
async def login(body: LoginIn, session: AsyncSession = Depends(get_session)):
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
async def me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)


@router.post("/refresh", response_model=TokenOut)
async def refresh(user: User = Depends(get_current_user)):
    """Sliding session: trade a still-valid token for a fresh one. Mobile
    clients call this on app launch so users never hit the 30-day expiry."""
    return TokenOut(access_token=create_access_token(user.id), user=UserOut.model_validate(user))
