import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from sqlalchemy import and_, exists, func, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crypto, embeddings, image_gen
from ..config import settings
from ..db import get_session
from ..enrichers import badge_for
from ..models import (
    Article,
    ArticleEmbedding,
    ArticleEntity,
    ArticleSuppression,
    Entity,
    EntitySnapshot,
    Feed,
    GeneratedImage,
    ProjectArticle,
    ProjectMember,
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
from .feeds import retention_visible

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/articles", tags=["articles"])

# Which numeric field in entity.data carries the "trend" per kind.
DELTA_METRICS = {"github": "stargazers_count", "hf_model": "downloads", "hf_dataset": "downloads"}
SNAPSHOT_CAP = 30

# Hybrid search: candidates per leg (vector / keyword) and the standard
# reciprocal-rank-fusion constant.
SEARCH_POOL = 60
RRF_K = 60


def to_list_item(
    article: Article,
    feed_title: str,
    state: UserArticleState | None,
    entities: list[EntityBadge] | None = None,
    *,
    image_pending: bool | None = None,
) -> ArticleListItem:
    if image_pending is None:
        image_pending = _image_pending(article)
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
        enriching=article.full_text_fetched_at is None
        and (article.full_text == "" or article.image_url is None),
        image_pending=image_pending,
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


def _encode_cursor(article: Article) -> str:
    """Opaque keyset cursor over (published_at, id) — the list's sort key."""
    published = article.published_at.isoformat() if article.published_at else ""
    return base64.urlsafe_b64encode(f"{published}|{article.id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime | None, int]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        published_raw, separator, id_raw = raw.rpartition("|")
        if not separator:
            raise ValueError(raw)
        published = datetime.fromisoformat(published_raw) if published_raw else None
        return published, int(id_raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=422, detail="Invalid cursor")


def _cursor_filter(published: datetime | None, article_id: int, *, oldest: bool):
    """Keyset condition for rows strictly after the cursor. Articles without a
    published_at sort last in both directions (nulls_last), so a non-null
    cursor still has the whole null tail ahead of it."""
    if oldest:
        if published is None:
            return and_(Article.published_at.is_(None), Article.id > article_id)
        return or_(
            Article.published_at > published,
            and_(Article.published_at == published, Article.id > article_id),
            Article.published_at.is_(None),
        )
    if published is None:
        return and_(Article.published_at.is_(None), Article.id < article_id)
    return or_(
        Article.published_at < published,
        and_(Article.published_at == published, Article.id < article_id),
        Article.published_at.is_(None),
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
    if shared is not None:
        return True
    # Pinned to a project the user belongs to (their own pin, or a shared one).
    # Function-level import: projects.py imports from this module at load time.
    from .projects import visible_pins

    pinned = await session.scalar(
        select(ProjectArticle.id)
        .join(ProjectMember, ProjectMember.project_id == ProjectArticle.project_id)
        .where(
            ProjectMember.user_id == user_id,
            ProjectArticle.article_id == article.id,
            visible_pins(user_id),
        )
    )
    return pinned is not None


def not_suppressed(user_id: int):
    """Listings hide articles matched by a dislike rule (soft-hide: the
    detail view, shares and projects still work — that's the escape hatch).
    Saved lists skip this predicate: an explicit save outranks a rule."""
    return ~exists(
        select(ArticleSuppression.id).where(
            ArticleSuppression.user_id == user_id,
            ArticleSuppression.article_id == Article.id,
        )
    )


def _scoped_article_ids(user_id: int, feed_id: int | None, filter: str):
    """Article.id select with the same subscription/filter scope as the list."""
    stmt = (
        select(Article.id)
        .join(
            Subscription,
            and_(Subscription.feed_id == Article.feed_id, Subscription.user_id == user_id),
        )
        .outerjoin(
            UserArticleState,
            and_(
                UserArticleState.article_id == Article.id,
                UserArticleState.user_id == user_id,
            ),
        )
    )
    stmt = stmt.where(retention_visible())
    if feed_id is not None:
        stmt = stmt.where(Article.feed_id == feed_id)
    else:
        # Muted feeds stay out of the aggregate inbox; their own page and the
        # saved list still show everything.
        if filter != "saved":
            stmt = stmt.where(Subscription.is_muted.is_(False))
    if filter == "unread":
        stmt = stmt.where(or_(UserArticleState.id.is_(None), UserArticleState.is_read.is_(False)))
    elif filter == "saved":
        stmt = stmt.where(UserArticleState.is_saved.is_(True))
    if filter != "saved":
        stmt = stmt.where(not_suppressed(user_id))
    return stmt


async def _hybrid_search_ids(
    session: AsyncSession, user_id: int, feed_id: int | None, filter: str, q: str
) -> list[int] | None:
    """Semantic + keyword search fused with RRF; article ids, best match first.
    None means embeddings are unavailable — caller falls back to ILIKE."""
    if not embeddings.is_configured():
        return None
    try:
        query_vector = await embeddings.embed_query(q)
    except Exception as exc:
        logger.warning("Query embedding failed, falling back to keyword search: %s", exc)
        return None

    vector_stmt = (
        _scoped_article_ids(user_id, feed_id, filter)
        .join(ArticleEmbedding, ArticleEmbedding.article_id == Article.id)
        # Model filter keeps dimensions consistent mid re-embed after a model switch.
        .where(ArticleEmbedding.model == settings.openai_embedding_model)
        .order_by(ArticleEmbedding.embedding.cosine_distance(query_vector))
        .limit(SEARCH_POOL)
    )
    # search_tsv is a generated column added by migration (see db.MIGRATIONS),
    # intentionally unmapped so create_all never emits a conflicting plain column.
    tsv = literal_column("articles.search_tsv")
    tsquery = func.websearch_to_tsquery("english", q)
    keyword_stmt = (
        _scoped_article_ids(user_id, feed_id, filter)
        .where(tsv.op("@@")(tsquery))
        .order_by(func.ts_rank(tsv, tsquery).desc())
        .limit(SEARCH_POOL)
    )
    vector_ids = list(await session.scalars(vector_stmt))
    keyword_ids = list(await session.scalars(keyword_stmt))

    scores: dict[int, float] = {}
    for leg in (vector_ids, keyword_ids):
        for rank, article_id in enumerate(leg):
            scores[article_id] = scores.get(article_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(scores, key=lambda article_id: (-scores[article_id], -article_id))


@router.get("", response_model=list[ArticleListItem])
async def list_articles(
    response: Response,
    background: BackgroundTasks,
    feed_id: int | None = None,
    filter: Literal["all", "unread", "saved"] = "all",
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    cursor: str | None = Query(default=None, max_length=200),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Chronological listings support keyset pagination: pass the previous
    response's `X-Next-Cursor` header as `cursor` (which then overrides
    `offset`). Cursors stay stable while new articles arrive, unlike offsets.
    Search results are ranked, not chronological, so `q` keeps using offsets."""
    if cursor is not None and q:
        raise HTTPException(status_code=422, detail="cursor cannot be combined with q")
    stmt = (
        select(Article, Feed.title, Feed.image_gen_enabled, UserArticleState)
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
    stmt = stmt.where(retention_visible())
    if feed_id is not None:
        stmt = stmt.where(Article.feed_id == feed_id)
    elif filter != "saved":
        stmt = stmt.where(Subscription.is_muted.is_(False))
    if filter == "unread":
        stmt = stmt.where(
            or_(UserArticleState.id.is_(None), UserArticleState.is_read.is_(False))
        )
    elif filter == "saved":
        stmt = stmt.where(UserArticleState.is_saved.is_(True))
    if filter != "saved":
        stmt = stmt.where(not_suppressed(user.id))

    ranked_ids = (
        await _hybrid_search_ids(session, user.id, feed_id, filter, q) if q else None
    )
    if ranked_ids is not None:
        page_ids = ranked_ids[offset : offset + limit]
        if not page_ids:
            return []
        stmt = stmt.where(Article.id.in_(page_ids))
        rows = (await session.execute(stmt)).all()
        position = {article_id: index for index, article_id in enumerate(page_ids)}
        rows.sort(key=lambda row: position[row[0].id])
    else:
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(
                or_(Article.title.ilike(pattern), Article.excerpt.ilike(pattern))
            )
        sort_order = None
        if feed_id is not None:
            sort_order = await session.scalar(
                select(Subscription.sort_order).where(
                    Subscription.user_id == user.id, Subscription.feed_id == feed_id
                )
            )
        oldest = sort_order == "oldest"
        if oldest:
            stmt = stmt.order_by(Article.published_at.asc().nulls_last(), Article.id.asc())
        else:
            stmt = stmt.order_by(Article.published_at.desc().nulls_last(), Article.id.desc())
        if cursor is not None:
            published, cursor_id = _decode_cursor(cursor)
            stmt = stmt.where(_cursor_filter(published, cursor_id, oldest=oldest))
        else:
            stmt = stmt.offset(offset)
        # One extra row tells us whether a next page exists.
        stmt = stmt.limit(limit + 1)
        rows = (await session.execute(stmt)).all()
        if len(rows) > limit:
            rows = rows[:limit]
            # Ranked/ILIKE search stays offset-based, so no cursor for q.
            if not q:
                response.headers["X-Next-Cursor"] = _encode_cursor(rows[-1][0])
    just_claimed = await _generate_listed_images(session, background, user, rows)
    entity_map = await _entities_for_articles(session, [a.id for a, _, _, _ in rows])
    return [
        to_list_item(
            article,
            feed_title,
            state,
            [_to_badge(link, entity) for link, entity in entity_map.get(article.id, [])],
            image_pending=article.id in just_claimed or _image_pending(article),
        )
        for article, feed_title, _, state in rows
    ]


# A recent claim with no image yet reads as "still rendering" to the client;
# anything older is a failed/abandoned attempt and stops reporting pending.
IMAGE_PENDING_WINDOW = timedelta(minutes=3)

# Generations one list response may start. Polling responses keep topping this
# up until the page is illustrated, so it bounds concurrent renders (and
# provider load), not the total.
LIST_GENERATION_BATCH = 4


def _image_pending(article: Article) -> bool:
    """An illustration is being rendered for this article right now."""
    return (
        article.image_url is None
        and article.image_gen_attempted_at is not None
        and datetime.now(timezone.utc) - article.image_gen_attempted_at
        < IMAGE_PENDING_WINDOW
    )


async def _resolve_image_config(session: AsyncSession, user: User):
    try:
        return await image_gen.resolve_config(session, user.id)
    except crypto.TokenCryptoError:
        return None  # broken stored key must not break reading


async def _claim_image_generation(
    session: AsyncSession, user: User, article: Article
) -> bool:
    """Atomically claim the once-ever generation attempt for an article,
    charging it to the user's monthly budget. False = someone else owns it."""
    claimed = await session.execute(
        update(Article)
        .where(Article.id == article.id, Article.image_gen_attempted_at.is_(None))
        .values(image_gen_attempted_at=func.now(), image_gen_user_id=user.id)
        # Don't sync the in-session object: the default strategy expires
        # image_gen_attempted_at (func.now() can't be evaluated in Python) and
        # the next read would lazy-load outside the greenlet. Callers track
        # freshly claimed articles explicitly instead.
        .execution_options(synchronize_session=False)
    )
    return claimed.rowcount == 1


def _generation_prompt(user: User, article: Article) -> str:
    return image_gen.render_prompt(
        user.image_prompt or image_gen.DEFAULT_IMAGE_PROMPT,
        title=article.title,
        excerpt=article.excerpt or "",
    )


async def _maybe_generate_image(
    session: AsyncSession,
    background: BackgroundTasks,
    user: User,
    article: Article,
    feed: Feed,
) -> bool:
    """Kick off lazy image generation on first view of an imageless article;
    returns whether an image is (now or already) being rendered."""
    if article.image_url is not None:
        return False
    if article.image_gen_attempted_at is not None:
        return _image_pending(article)
    if not feed.image_gen_enabled:
        return False
    config = await _resolve_image_config(session, user)
    if config is None:
        return False
    if await image_gen.remaining_budget(session, user) == 0:
        return False
    # The claim is the once-ever guard: whoever flips NULL owns the attempt.
    owned = await _claim_image_generation(session, user, article)
    prompt = _generation_prompt(user, article)
    await session.commit()
    if not owned:
        return True  # a concurrent view just claimed it
    background.add_task(
        image_gen.generate_for_article, article.id, user.id, config, prompt
    )
    return True


async def _generate_listed_images(
    session: AsyncSession,
    background: BackgroundTasks,
    user: User,
    rows: list,
) -> set[int]:
    """Start illustrations for the first few imageless articles on a list page
    (top of the page first), so cards fill in without the article being opened.
    Returns the ids claimed by this request — they're pending in the response
    even though the ORM rows predate the claim."""
    in_flight = sum(1 for article, _, _, _ in rows if _image_pending(article))
    slots = LIST_GENERATION_BATCH - in_flight
    candidates = [
        article
        for article, _, gen_enabled, _ in rows
        if gen_enabled
        and article.image_url is None
        and article.image_gen_attempted_at is None
    ]
    if slots <= 0 or not candidates:
        return set()
    config = await _resolve_image_config(session, user)
    if config is None:
        return set()
    remaining = await image_gen.remaining_budget(session, user)
    if remaining is not None:
        slots = min(slots, remaining)
    claimed: list[tuple[Article, str]] = []
    for article in candidates[:slots]:
        if await _claim_image_generation(session, user, article):
            claimed.append((article, _generation_prompt(user, article)))
    # Commit before scheduling: a task must never run for an unclaimed article.
    await session.commit()
    for article, prompt in claimed:
        background.add_task(
            image_gen.generate_for_article, article.id, user.id, config, prompt
        )
    return {article.id for article, _ in claimed}


@router.get("/{article_id}", response_model=ArticleDetail)
async def get_article(
    article_id: int,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    article = await session.get(Article, article_id)
    if article is None or not await user_can_access(session, user.id, article):
        raise HTTPException(status_code=404, detail="Article not found")
    feed = await session.get(Feed, article.feed_id)
    image_pending = await _maybe_generate_image(session, background, user, article, feed)
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
        **item.model_dump(exclude={"entities", "image_pending"}),
        content_html=article.content_html,
        summary_model=article.summary_model,
        entities=full_entities,
        image_pending=image_pending,
    )


@router.get("/{article_id}/generated-image")
async def get_generated_image(
    article_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Serves AI-generated article images. Unauthenticated on purpose: <img>
    tags can't send Authorization headers, and these illustrate public news
    articles just like the og:images we scrape."""
    image = await session.get(GeneratedImage, article_id)
    if image is None:
        raise HTTPException(status_code=404, detail="No generated image")
    return Response(
        content=image.data,
        media_type=image.content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
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
        # Don't mark invisible (suppressed) articles read behind the user's back.
        .where(not_suppressed(user.id))
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
