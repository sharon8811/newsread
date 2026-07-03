from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Article, Feed, Share, ShareRecipient, Subscription, User, UserArticleState
from ..schemas import ArticleDetail, ArticleListItem, ArticleStateIn, MarkAllReadIn
from ..security import get_current_user

router = APIRouter(prefix="/articles", tags=["articles"])


def to_list_item(
    article: Article, feed_title: str, state: UserArticleState | None
) -> ArticleListItem:
    return ArticleListItem(
        id=article.id,
        feed_id=article.feed_id,
        feed_title=feed_title,
        title=article.title,
        url=article.url,
        comments_url=article.comments_url,
        author=article.author,
        published_at=article.published_at,
        excerpt=article.excerpt,
        image_url=article.image_url,
        is_read=bool(state and state.is_read),
        is_saved=bool(state and state.is_saved),
        summary=article.summary,
    )


async def user_can_access(session: AsyncSession, user_id: int, article: Article) -> bool:
    subscribed = await session.scalar(
        select(Subscription.id).where(
            Subscription.user_id == user_id, Subscription.feed_id == article.feed_id
        )
    )
    if subscribed:
        return True
    shared = await session.scalar(
        select(ShareRecipient.id)
        .join(Share, Share.id == ShareRecipient.share_id)
        .where(ShareRecipient.to_user_id == user_id, Share.article_id == article.id)
    )
    return shared is not None


@router.get("", response_model=list[ArticleListItem])
async def list_articles(
    feed_id: int | None = None,
    filter: Literal["all", "unread", "saved"] = "all",
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Article, Feed.title, UserArticleState)
        .join(Feed, Article.feed_id == Feed.id)
        .join(
            Subscription,
            and_(Subscription.feed_id == Article.feed_id, Subscription.user_id == user.id),
        )
        .outerjoin(
            UserArticleState,
            and_(
                UserArticleState.article_id == Article.id,
                UserArticleState.user_id == user.id,
            ),
        )
    )
    if feed_id is not None:
        stmt = stmt.where(Article.feed_id == feed_id)
    if filter == "unread":
        stmt = stmt.where(
            or_(UserArticleState.id.is_(None), UserArticleState.is_read.is_(False))
        )
    elif filter == "saved":
        stmt = stmt.where(UserArticleState.is_saved.is_(True))
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(Article.title.ilike(pattern), Article.excerpt.ilike(pattern)))

    stmt = stmt.order_by(
        Article.published_at.desc().nulls_last(), Article.id.desc()
    ).limit(limit).offset(offset)

    rows = await session.execute(stmt)
    return [to_list_item(article, feed_title, state) for article, feed_title, state in rows]


@router.get("/{article_id}", response_model=ArticleDetail)
async def get_article(
    article_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    article = await session.get(Article, article_id)
    if article is None or not await user_can_access(session, user.id, article):
        raise HTTPException(status_code=404, detail="Article not found")
    feed = await session.get(Feed, article.feed_id)
    state = await session.scalar(
        select(UserArticleState).where(
            UserArticleState.user_id == user.id, UserArticleState.article_id == article.id
        )
    )
    item = to_list_item(article, feed.title or feed.url, state)
    return ArticleDetail(
        **item.model_dump(),
        content_html=article.content_html,
        summary_model=article.summary_model,
    )


@router.post("/{article_id}/state", response_model=ArticleListItem)
async def set_state(
    article_id: int,
    body: ArticleStateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    article = await session.get(Article, article_id)
    if article is None or not await user_can_access(session, user.id, article):
        raise HTTPException(status_code=404, detail="Article not found")

    values: dict = {}
    if body.is_read is not None:
        values["is_read"] = body.is_read
    if body.is_saved is not None:
        values["is_saved"] = body.is_saved
    if not values:
        raise HTTPException(status_code=422, detail="Nothing to update")

    stmt = (
        pg_insert(UserArticleState)
        .values(user_id=user.id, article_id=article.id, **values)
        .on_conflict_do_update(
            index_elements=["user_id", "article_id"],
            set_=values,
        )
    )
    await session.execute(stmt)
    await session.commit()

    feed = await session.get(Feed, article.feed_id)
    state = await session.scalar(
        select(UserArticleState).where(
            UserArticleState.user_id == user.id, UserArticleState.article_id == article.id
        )
    )
    return to_list_item(article, feed.title or feed.url, state)


@router.post("/mark-all-read", status_code=204)
async def mark_all_read(
    body: MarkAllReadIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Article.id)
        .join(
            Subscription,
            and_(Subscription.feed_id == Article.feed_id, Subscription.user_id == user.id),
        )
        .outerjoin(
            UserArticleState,
            and_(
                UserArticleState.article_id == Article.id,
                UserArticleState.user_id == user.id,
            ),
        )
        .where(or_(UserArticleState.id.is_(None), UserArticleState.is_read.is_(False)))
    )
    if body.feed_id is not None:
        stmt = stmt.where(Article.feed_id == body.feed_id)

    article_ids = (await session.scalars(stmt)).all()
    if article_ids:
        insert_stmt = (
            pg_insert(UserArticleState)
            .values([{"user_id": user.id, "article_id": aid, "is_read": True} for aid in article_ids])
            .on_conflict_do_update(
                index_elements=["user_id", "article_id"],
                set_={"is_read": True},
            )
        )
        await session.execute(insert_stmt)
        await session.commit()
