from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import User
from ..schemas import UserOut, UserPublic, UserUpdateIn
from ..security import get_current_user

router = APIRouter(prefix="/users", tags=["users"])


@router.patch("/me", response_model=UserOut)
async def update_me(
    body: UserUpdateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if body.default_view is not None:
        user.default_view = body.default_view
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)


@router.get("/search", response_model=list[UserPublic])
async def search_users(
    q: str = Query(min_length=1, max_length=60),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    pattern = f"%{q}%"
    rows = await session.scalars(
        select(User)
        .where(or_(User.username.ilike(pattern), User.name.ilike(pattern)), User.id != user.id)
        .order_by(User.username)
        .limit(8)
    )
    return [UserPublic.model_validate(u) for u in rows]
