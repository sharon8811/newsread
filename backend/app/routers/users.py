from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import or_, select

from ..deps import CurrentUser, DbSession
from ..models import User
from ..schemas import UserOut, UserPublic, UserUpdateIn
from ..translation import language_for

router = APIRouter(prefix="/users", tags=["users"])


@router.patch("/me", response_model=UserOut)
async def update_me(
    body: UserUpdateIn,
    user: CurrentUser,
    session: DbSession,
):
    if body.default_view is not None:
        user.default_view = body.default_view
    if body.assisted_scroll is not None:
        user.assisted_scroll = body.assisted_scroll
    if body.image_prompt is not None:
        user.image_prompt = body.image_prompt.strip() or None
    # Presence-based PATCH: an explicit null means "back to unlimited".
    if "image_gen_monthly_limit" in body.model_fields_set:
        user.image_gen_monthly_limit = body.image_gen_monthly_limit
    # Same: an explicit null clears the saved translation language, so the next
    # translate asks again.
    if "translation_language" in body.model_fields_set:
        if (
            body.translation_language is not None
            and language_for(body.translation_language) is None
        ):
            raise HTTPException(status_code=422, detail="That language isn't available.")
        user.translation_language = body.translation_language
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)


@router.get("/search", response_model=list[UserPublic])
async def search_users(
    user: CurrentUser,
    session: DbSession,
    q: str = Query(min_length=1, max_length=60),
):
    pattern = f"%{q}%"
    rows = await session.scalars(
        select(User)
        .where(or_(User.username.ilike(pattern), User.name.ilike(pattern)), User.id != user.id)
        .order_by(User.username)
        .limit(8)
    )
    return [UserPublic.model_validate(u) for u in rows]
