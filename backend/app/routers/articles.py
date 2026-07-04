from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..enrichers import badge_for
from ..models import (
    Article,
    ArticleEntity,
    Entity,
    EntitySnapshot,
    Feed,
    Share,
    ShareRecipient,
    Subscription,
    User,
    UserArticleState,
)
from ..schemas import (
    ArticleDetail,
    ArticleListItem,
    ArticleStateIn,
    EntityBadge,
    EntityFull,
    EntitySnapshotOut,
    MarkAllReadIn,
)
from ..security import get_current_user

router = APIRouter(prefix="/articles", tags=["articles"])

# Which numeric field in entity.data carries the "trend" per kind.
DELTA_METRICS = {"github": "stargazers_count", "hf_model": "downloads", "hf_dataset": "downloads"}
SNAPSHOT_CAP = 30


def to_list_item(
    article: Article,
    feed_title: str,
    state: UserArticleState | None,
    entities: list[EntityBadge] | None = None,
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
        summary_short=article.summary_short,
        summary_medium=article.summary_medium,
        entities=entities or [],
    )


def _to_badge(link: ArticleEntity, entity: Entity) -> EntityBadge:
    return EntityBadge(
        id=entity.id,
        kind=entity.kind,
        key=entity.canonical_key,
        url=entity.url,
        source=link.source,
        badge=badge_for(entity.kind, entity.data or {}),
    )


async def _entities_for_articles(
    session: AsyncSession, article_ids: list[int]
) -> dict[int, list[tuple[ArticleEntity, Entity]]]:
    """One query for a whole page of articles; grouped, primary-first."""
    if not article_ids:
        return {}
    rows = await session.execute(
        select(ArticleEntity, Entity)
        .join(Entity, Entity.id == ArticleEntity.entity_id)
        .where(ArticleEntity.article_id.in_(article_ids))
    )
    grouped: dict[int, list[tuple[ArticleEntity, Entity]]] = {}
    for link, entity in rows:
        grouped.setdefault(link.article_id, []).append((link, entity))
    for pairs in grouped.values():
        pairs.sort(key=lambda pair: (pair[0].source != "primary", pair[0].position))
    return grouped


def _compute_deltas(entity: Entity, snapshots: list[EntitySnapshot]) -> dict:
    metric = DELTA_METRICS.get(entity.kind)
    if not metric or not snapshots:
        return {}
    current = (entity.data or {}).get(metric)
    if not isinstance(current, (int, float)):
        return {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    baseline = None
    for snapshot in snapshots:  # newest-first
        if snapshot.captured_at <= cutoff:
            baseline = (snapshot.data or {}).get(metric)
            break
    if baseline is None and entity.created_at <= cutoff:
        baseline = (snapshots[-1].data or {}).get(metric)
    if not isinstance(baseline, (int, float)) or baseline == current:
        return {}
    return {f"{metric}_delta_7d": current - baseline}


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

    rows = (await session.execute(stmt)).all()
    entity_map = await _entities_for_articles(session, [a.id for a, _, _ in rows])
    return [
        to_list_item(
            article,
            feed_title,
            state,
            [_to_badge(link, entity) for link, entity in entity_map.get(article.id, [])],
        )
        for article, feed_title, state in rows
    ]


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
    pairs = (await _entities_for_articles(session, [article.id])).get(article.id, [])
    full_entities: list[EntityFull] = []
    if pairs:
        snapshot_rows = await session.scalars(
            select(EntitySnapshot)
            .where(EntitySnapshot.entity_id.in_([e.id for _, e in pairs]))
            .order_by(EntitySnapshot.entity_id, EntitySnapshot.captured_at.desc())
        )
        by_entity: dict[int, list[EntitySnapshot]] = {}
        for snapshot in snapshot_rows:
            bucket = by_entity.setdefault(snapshot.entity_id, [])
            if len(bucket) < SNAPSHOT_CAP:
                bucket.append(snapshot)
        for link, entity in pairs:
            snapshots = by_entity.get(entity.id, [])
            full_entities.append(
                EntityFull(
                    **_to_badge(link, entity).model_dump(),
                    data=entity.data or {},
                    fetched_at=entity.fetched_at,
                    deltas=_compute_deltas(entity, snapshots),
                    snapshots=[EntitySnapshotOut.model_validate(s) for s in snapshots],
                )
            )

    item = to_list_item(article, feed.title or feed.url, state)
    return ArticleDetail(
        **item.model_dump(exclude={"entities"}),
        content_html=article.content_html,
        summary_model=article.summary_model,
        entities=full_entities,
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
