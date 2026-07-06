from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_session
from ..models import Article, Feed, Share, ShareRecipient, User, UserArticleState
from ..queue import enqueue
from ..schemas import ShareCreateIn, ShareOut, UnseenCountOut, UserPublic
from ..security import get_current_user
from .articles import to_list_item, user_can_access

router = APIRouter(prefix="/shares", tags=["shares"])


def _share_out(share: Share, feed_title: str, state: UserArticleState | None, me: User) -> ShareOut:
    my_recipient = next((r for r in share.recipients if r.to_user_id == me.id), None)
    return ShareOut(
        id=share.id,
        article=to_list_item(share.article, feed_title, state),
        from_user=UserPublic.model_validate(share.from_user),
        to_users=[UserPublic.model_validate(r.to_user) for r in share.recipients],
        note=share.note,
        created_at=share.created_at,
        seen_at=my_recipient.seen_at if my_recipient else None,
    )


_share_load_options = (
    selectinload(Share.recipients).selectinload(ShareRecipient.to_user),
    selectinload(Share.from_user),
    selectinload(Share.article).selectinload(Article.feed),
)


async def _states_for(session: AsyncSession, user_id: int, article_ids: list[int]):
    if not article_ids:
        return {}
    rows = await session.scalars(
        select(UserArticleState).where(
            UserArticleState.user_id == user_id,
            UserArticleState.article_id.in_(article_ids),
        )
    )
    return {s.article_id: s for s in rows}


@router.post("", response_model=ShareOut, status_code=201)
async def create_share(
    body: ShareCreateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    article = await session.get(Article, body.article_id)
    if article is None or not await user_can_access(session, user.id, article):
        raise HTTPException(status_code=404, detail="Article not found")

    usernames = {u.strip().lstrip("@") for u in body.recipients if u.strip().lstrip("@")}
    usernames.discard(user.username)
    if not usernames:
        raise HTTPException(status_code=422, detail="Add at least one recipient (not yourself)")

    recipients = (
        await session.scalars(
            select(User).where(func.lower(User.username).in_({u.lower() for u in usernames}))
        )
    ).all()
    found = {r.username.lower() for r in recipients}
    missing = [u for u in usernames if u.lower() not in found]
    if missing:
        raise HTTPException(status_code=404, detail=f"No such user: {', '.join(sorted(missing))}")

    note = body.note.strip() if body.note else None
    share = Share(from_user_id=user.id, article_id=article.id, note=note or None)
    share.recipients = [ShareRecipient(to_user_id=r.id) for r in recipients]
    session.add(share)
    await session.commit()
    await enqueue("send_share_push", share.id)

    share = await session.scalar(
        select(Share).where(Share.id == share.id).options(*_share_load_options)
    )
    states = await _states_for(session, user.id, [article.id])
    return _share_out(share, share.article.feed.title or share.article.feed.url, states.get(article.id), user)


@router.get("/received", response_model=list[ShareOut])
async def received_shares(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    shares = (
        await session.scalars(
            select(Share)
            .join(
                ShareRecipient,
                and_(ShareRecipient.share_id == Share.id, ShareRecipient.to_user_id == user.id),
            )
            .options(*_share_load_options)
            .order_by(Share.created_at.desc())
            .limit(200)
        )
    ).all()
    states = await _states_for(session, user.id, [s.article_id for s in shares])
    return [
        _share_out(s, s.article.feed.title or s.article.feed.url, states.get(s.article_id), user)
        for s in shares
    ]


@router.get("/sent", response_model=list[ShareOut])
async def sent_shares(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    shares = (
        await session.scalars(
            select(Share)
            .where(Share.from_user_id == user.id)
            .options(*_share_load_options)
            .order_by(Share.created_at.desc())
            .limit(200)
        )
    ).all()
    states = await _states_for(session, user.id, [s.article_id for s in shares])
    return [
        _share_out(s, s.article.feed.title or s.article.feed.url, states.get(s.article_id), user)
        for s in shares
    ]


@router.post("/{share_id}/seen", status_code=204)
async def mark_seen(
    share_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    recipient = await session.scalar(
        select(ShareRecipient).where(
            ShareRecipient.share_id == share_id, ShareRecipient.to_user_id == user.id
        )
    )
    if recipient is None:
        raise HTTPException(status_code=404, detail="Share not found")
    if recipient.seen_at is None:
        recipient.seen_at = datetime.now(timezone.utc)
        await session.commit()


@router.get("/unseen-count", response_model=UnseenCountOut)
async def unseen_count(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    count = await session.scalar(
        select(func.count(ShareRecipient.id)).where(
            ShareRecipient.to_user_id == user.id, ShareRecipient.seen_at.is_(None)
        )
    )
    return UnseenCountOut(count=count or 0)
