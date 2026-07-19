import base64
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response
from sqlalchemy import and_, exists, func, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crypto, embeddings, image_gen, ranking
from ..access import accessible_article
from ..config import settings
from ..deps import CurrentUser, DbSession
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
    Subscription,
    User,
    UserArticleState,
    UserReadingPosition,
)
from ..ner import NER_KINDS
from ..schemas import (
    ArticleDetail,
    ArticleListItem,
    ArticleStateBatchIn,
    ArticleStateIn,
    EntityBadge,
    EntityFull,
    EntitySnapshotOut,
    MarkAllReadIn,
    RelatedArticleItem,
)
from .feeds import retention_visible

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/articles", tags=["articles"])

# Which numeric field in entity.data carries the "trend" per kind.
DELTA_METRICS = {"github": "stargazers_count", "hf_model": "downloads", "hf_dataset": "downloads"}
SNAPSHOT_CAP = 30

# Hybrid search: candidates per leg (vector / keyword) and the standard
# reciprocal-rank-fusion constant.
# Related-articles KNN. The distance cutoffs share the calibration documented
# on interests.STORY_THRESHOLD / TOPIC_THRESHOLD (same-story pairs land well
# under 0.35, same-topic under ~0.6, unrelated above 0.85). They live here,
# not there, because interests.py imports from this module.
RELATED_SAME_STORY = 0.35
RELATED_MAX_DISTANCE = 0.70
RELATED_LIMIT = 5
# Ranking boost per shared LLM name entity (person/org/product), capped at 3.
# Calibrated on the live corpus (2026-07): pairs sharing 1 entity have median
# distance 0.646, 2 -> 0.376, 3 -> 0.056, vs 0.762 for none — so each shared
# name is worth roughly a tenth of distance. The boost only reorders inside
# the RELATED_MAX_DISTANCE pool; it never admits candidates past the cutoff
# (both articles mentioning one company is not, by itself, related coverage).
RELATED_NER_BOOST = 0.08
# The list is RELATED_LIMIT at most, not always: boosted score must clear
# this bar, so generic neighbors need distance < 0.60 (the same-topic edge
# of the calibration above) while name-sharers may run up to the ceiling.
# Padding every list to five made ~30% of rank-4/5 slots weak fillers.
RELATED_DISPLAY_SCORE = 0.60
# News-recency window: an old article at a close distance is rarely what
# "related coverage" means; it also bounds the entity-overlap leg.
RELATED_WINDOW = timedelta(days=90)


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
    cutoff = datetime.now(UTC) - timedelta(days=7)
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
        raise HTTPException(status_code=422, detail="Invalid cursor") from None


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


def _cursor_filter_before(published: datetime | None, article_id: int, *, oldest: bool):
    """Mirror of _cursor_filter: rows strictly before the cursor in list
    order, for paging backward through read history. From inside the
    null-published tail everything non-null lies before the cursor; from a
    non-null cursor nothing in the tail does."""
    if oldest:
        if published is None:
            return or_(
                Article.published_at.is_not(None),
                and_(Article.published_at.is_(None), Article.id < article_id),
            )
        return or_(
            Article.published_at < published,
            and_(Article.published_at == published, Article.id < article_id),
        )
    if published is None:
        return or_(
            Article.published_at.is_not(None),
            and_(Article.published_at.is_(None), Article.id > article_id),
        )
    return or_(
        Article.published_at > published,
        and_(Article.published_at == published, Article.id > article_id),
    )


def _cursor_filter_at_or_after(published: datetime | None, article_id: int, *, oldest: bool):
    """Rows at or after the cursor in list order — anchors a page that starts
    exactly at a known article (the resume point at the first unread)."""
    at = and_(
        Article.published_at.is_(None) if published is None else Article.published_at == published,
        Article.id == article_id,
    )
    return or_(at, _cursor_filter(published, article_id, oldest=oldest))


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


async def current_embedding(session: AsyncSession, article_id: int) -> ArticleEmbedding | None:
    """The article's vector under the currently configured model, or None."""
    if not embeddings.is_configured():
        return None
    return await session.scalar(
        select(ArticleEmbedding).where(
            ArticleEmbedding.article_id == article_id,
            ArticleEmbedding.model == settings.openai_embedding_model,
        )
    )


@dataclass
class RelatedRow:
    article: Article
    feed_title: str
    state: UserArticleState | None
    tier: str  # 'same_story' | 'related'


def _related_scope(user_id: int, exclude_id: int | None = None):
    """Base select with the inbox scope: subscribed, unmuted, retention-
    visible, not suppressed, optionally excluding one article (the one
    related coverage is computed for). retention_visible() references
    Subscription and UserArticleState, so both joins are load-bearing even
    where the caller doesn't read them."""
    stmt = (
        select(Article, Feed.title, Feed.url, UserArticleState)
        .join(Feed, Article.feed_id == Feed.id)
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
        .where(
            Subscription.is_muted.is_(False),
            retention_visible(),
            not_suppressed(user_id),
        )
    )
    if exclude_id is not None:
        stmt = stmt.where(Article.id != exclude_id)
    return stmt


async def _entity_related(
    session: AsyncSession, user_id: int, article: Article, cutoff: datetime
) -> list[RelatedRow]:
    """Articles linking the same external resource (repo, paper, video…).
    Sparse but exact: a shared canonical identifier is stronger same-story
    evidence than any embedding distance, so these rank by how many
    resources they share (primary links over inline mentions), not by
    vector proximity. LLM name entities are excluded — two articles both
    mentioning one company are not the same story."""
    link_entity_ids = (
        select(ArticleEntity.entity_id)
        .join(Entity, Entity.id == ArticleEntity.entity_id)
        .where(
            ArticleEntity.article_id == article.id,
            Entity.kind.notin_(NER_KINDS),
        )
    )
    overlap = (
        select(
            ArticleEntity.article_id.label("article_id"),
            func.count().label("shared"),
            func.count().filter(ArticleEntity.source == "primary").label("shared_primary"),
        )
        .where(ArticleEntity.entity_id.in_(link_entity_ids))
        .group_by(ArticleEntity.article_id)
        .subquery()
    )
    stmt = (
        _related_scope(user_id, article.id)
        .join(overlap, overlap.c.article_id == Article.id)
        .where(Article.fetched_at >= cutoff)
        .order_by(
            overlap.c.shared.desc(),
            overlap.c.shared_primary.desc(),
            Article.published_at.desc().nulls_last(),
            Article.id.desc(),
        )
        .limit(RELATED_LIMIT)
    )
    rows = (await session.execute(stmt)).all()
    return [
        RelatedRow(candidate, title or url, state, "same_story")
        for candidate, title, url, state in rows
    ]


async def related_articles(
    session: AsyncSession, user_id: int, article: Article
) -> list[RelatedRow]:
    """Entity-first hybrid: shared-resource matches lead the list, then
    embedding KNN fills the remaining slots, with candidates sharing LLM
    name entities (person/org/product) boosted within the distance pool.
    The entity leg also carries installs with no embeddings at all."""
    cutoff = datetime.now(UTC) - RELATED_WINDOW
    rows = await _entity_related(session, user_id, article, cutoff)
    remaining = RELATED_LIMIT - len(rows)
    if remaining <= 0:
        return rows
    source = await current_embedding(session, article.id)
    if source is None:
        return rows
    seen = [row.article.id for row in rows]
    distance = ArticleEmbedding.embedding.cosine_distance(source.embedding)
    shared_ner = (
        select(
            ArticleEntity.article_id.label("article_id"),
            func.least(func.count(), 3).label("shared"),
        )
        .join(Entity, Entity.id == ArticleEntity.entity_id)
        .where(
            Entity.kind.in_(NER_KINDS),
            ArticleEntity.entity_id.in_(
                select(ArticleEntity.entity_id).where(ArticleEntity.article_id == article.id)
            ),
        )
        .group_by(ArticleEntity.article_id)
        .subquery()
    )
    score = distance - func.coalesce(shared_ner.c.shared, 0) * RELATED_NER_BOOST
    stmt = (
        _related_scope(user_id, article.id)
        .add_columns(distance.label("distance"))
        .join(ArticleEmbedding, ArticleEmbedding.article_id == Article.id)
        .outerjoin(shared_ner, shared_ner.c.article_id == Article.id)
        .where(
            ArticleEmbedding.model == settings.openai_embedding_model,
            Article.fetched_at >= cutoff,
            Article.id.notin_(seen),
            distance < RELATED_MAX_DISTANCE,
            score < RELATED_DISPLAY_SCORE,
        )
        .order_by(score)
        .limit(remaining)
    )
    vector_rows = (await session.execute(stmt)).all()
    return rows + [
        RelatedRow(
            candidate,
            title or url,
            state,
            "same_story" if dist < RELATED_SAME_STORY else "related",
        )
        for candidate, title, url, state, dist in vector_rows
    ]


def _scoped_article_ids(user_id: int, feed_id: int | None, filter: str):
    """Article.id select with the same subscription/filter scope as the list."""
    stmt = (
        select(Article.id)
        .join(Feed, Feed.id == Article.feed_id)
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
        # Muted feeds and the hidden "Imported" feed stay out of the aggregate
        # inbox; their own pages and the saved list still show everything.
        if filter != "saved":
            stmt = stmt.where(Subscription.is_muted.is_(False), Feed.owner_user_id.is_(None))
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
        .limit(ranking.SEARCH_POOL)
    )
    # search_tsv is a generated column owned by the Alembic baseline,
    # intentionally unmapped: the ORM can't express GENERATED ALWAYS AS here.
    tsv = literal_column("articles.search_tsv")
    tsquery = func.websearch_to_tsquery("english", q)
    keyword_stmt = (
        _scoped_article_ids(user_id, feed_id, filter)
        .where(tsv.op("@@")(tsquery))
        .order_by(func.ts_rank(tsv, tsquery).desc())
        .limit(ranking.SEARCH_POOL)
    )
    vector_ids = list(await session.scalars(vector_stmt))
    keyword_ids = list(await session.scalars(keyword_stmt))

    return ranking.rrf_fuse(vector_ids, keyword_ids)


@router.get("", response_model=list[ArticleListItem])
async def list_articles(
    response: Response,
    background: BackgroundTasks,
    user: CurrentUser,
    session: DbSession,
    feed_id: int | None = None,
    filter: Literal["all", "unread", "saved"] = "all",
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    cursor: str | None = Query(default=None, max_length=200),
    anchor: Literal["resume"] | None = None,
    direction: Literal["after", "before"] = "after",
    reading_window: bool = False,
):
    """Chronological listings support keyset pagination: pass the previous
    response's `X-Next-Cursor` header as `cursor` (which then overrides
    `offset`). Cursors stay stable while new articles arrive, unlike offsets.
    Search results are ranked, not chronological, so `q` keeps using offsets.

    `anchor=resume` starts the page at the reading frontier: the first
    unread at or after the stored reading position (so newer arrivals above
    never teleport the resume point back to the top), falling back to the
    first unread anywhere, then to the top of the list. It also reports the
    scope's total unread in `X-Unread-Count` and how many unread sit above
    the resume point in `X-New-Above-Count`. `direction=before` pages
    backward from `cursor` through read history. `reading_window=true`
    keeps that backward context available even when `filter=unread`, while
    forward pages remain unread-only. Both hand back an
    `X-Prev-Cursor` usable for the next backward page whenever earlier rows
    exist. A reading window without an anchor starts at the ordinary top page
    and still reports `X-Unread-Count` plus a zero `X-New-Above-Count`."""
    if cursor is not None and q:
        raise HTTPException(status_code=422, detail="cursor cannot be combined with q")
    if anchor is not None and (cursor is not None or q):
        raise HTTPException(status_code=422, detail="anchor cannot be combined with cursor or q")
    if direction == "before" and cursor is None:
        raise HTTPException(status_code=422, detail="direction=before requires a cursor")
    base_stmt = (
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
    base_stmt = base_stmt.where(retention_visible())
    if feed_id is not None:
        base_stmt = base_stmt.where(Article.feed_id == feed_id)
    elif filter != "saved":
        # Muted feeds and the hidden "Imported" feed stay out of the aggregate
        # inbox (mirrors _scoped_article_ids).
        base_stmt = base_stmt.where(
            Subscription.is_muted.is_(False), Feed.owner_user_id.is_(None)
        )
    if filter != "saved":
        base_stmt = base_stmt.where(not_suppressed(user.id))

    stmt = base_stmt
    if filter == "unread":
        stmt = stmt.where(or_(UserArticleState.id.is_(None), UserArticleState.is_read.is_(False)))
    elif filter == "saved":
        stmt = stmt.where(UserArticleState.is_saved.is_(True))

    ranked_ids = await _hybrid_search_ids(session, user.id, feed_id, filter, q) if q else None
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
            stmt = stmt.where(or_(Article.title.ilike(pattern), Article.excerpt.ilike(pattern)))
        sort_order = None
        if feed_id is not None:
            sort_order = await session.scalar(
                select(Subscription.sort_order).where(
                    Subscription.user_id == user.id, Subscription.feed_id == feed_id
                )
            )
        oldest = sort_order == "oldest"
        if oldest:
            list_order = (Article.published_at.asc().nulls_last(), Article.id.asc())
            reverse_order = (Article.published_at.desc().nulls_first(), Article.id.desc())
        else:
            list_order = (Article.published_at.desc().nulls_last(), Article.id.desc())
            reverse_order = (Article.published_at.asc().nulls_first(), Article.id.asc())
        if direction == "before":
            # Backward page: mirror the keyset, query in reverse order, then
            # flip the block back to list order for the client to prepend. A
            # reading window requests all prior rows as stable context even
            # when its forward-facing filter is unread-only.
            published, cursor_id = _decode_cursor(cursor)
            history_stmt = base_stmt if reading_window and filter == "unread" else stmt
            stmt = (
                history_stmt.where(_cursor_filter_before(published, cursor_id, oldest=oldest))
                .order_by(*reverse_order)
                .limit(limit + 1)
            )
            rows = (await session.execute(stmt)).all()
            has_earlier = len(rows) > limit
            rows = rows[:limit]
            rows.reverse()
            if has_earlier and rows:
                response.headers["X-Prev-Cursor"] = _encode_cursor(rows[0][0])
        else:
            scope_stmt = stmt  # pre-pagination scope, reused for anchor lookups
            stmt = stmt.order_by(*list_order)
            anchor_article: Article | None = None
            unread = or_(UserArticleState.id.is_(None), UserArticleState.is_read.is_(False))
            if anchor is not None:
                scope = f"feed:{feed_id}" if feed_id is not None else "inbox"
                position = await session.scalar(
                    select(UserReadingPosition).where(
                        UserReadingPosition.user_id == user.id,
                        UserReadingPosition.scope == scope,
                    )
                )
                if position is not None:
                    anchor_row = (
                        await session.execute(
                            scope_stmt.where(
                                unread,
                                _cursor_filter_at_or_after(
                                    position.published_at, position.article_id, oldest=oldest
                                ),
                            )
                            .order_by(*list_order)
                            .limit(1)
                        )
                    ).first()
                    if anchor_row is not None:
                        anchor_article = anchor_row[0]
                if anchor_article is None:
                    # No frontier yet, or everything at/after it is read:
                    # resume at the first unread anywhere.
                    anchor_row = (
                        await session.execute(
                            scope_stmt.where(unread).order_by(*list_order).limit(1)
                        )
                    ).first()
                    if anchor_row is not None:
                        anchor_article = anchor_row[0]
            if anchor is not None or reading_window:
                unread_total = await session.scalar(
                    select(func.count()).select_from(scope_stmt.where(unread).subquery())
                )
                response.headers["X-Unread-Count"] = str(unread_total or 0)
            if anchor is not None:
                new_above = 0
                if anchor_article is not None:
                    new_above = await session.scalar(
                        select(func.count()).select_from(
                            scope_stmt.where(
                                unread,
                                _cursor_filter_before(
                                    anchor_article.published_at,
                                    anchor_article.id,
                                    oldest=oldest,
                                ),
                            ).subquery()
                        )
                    )
                response.headers["X-New-Above-Count"] = str(new_above or 0)
            elif reading_window:
                # A top-anchored reading window has nothing newer above it.
                response.headers["X-New-Above-Count"] = "0"
            if anchor_article is not None:
                anchor_published = anchor_article.published_at
                stmt = stmt.where(
                    _cursor_filter_at_or_after(anchor_published, anchor_article.id, oldest=oldest)
                )
                history_scope_stmt = (
                    base_stmt if reading_window and filter == "unread" else scope_stmt
                )
                earlier_row = (
                    await session.execute(
                        history_scope_stmt.where(
                            _cursor_filter_before(
                                anchor_published, anchor_article.id, oldest=oldest
                            )
                        ).limit(1)
                    )
                ).first()
                if earlier_row is not None:
                    response.headers["X-Prev-Cursor"] = _encode_cursor(anchor_article)
            elif cursor is not None:
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
        and datetime.now(UTC) - article.image_gen_attempted_at < IMAGE_PENDING_WINDOW
    )


async def _resolve_image_config(session: AsyncSession, user: User):
    try:
        return await image_gen.resolve_config(session, user.id)
    except crypto.TokenCryptoError:
        return None  # broken stored key must not break reading


async def _claim_image_generation(session: AsyncSession, user: User, article: Article) -> bool:
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
    background.add_task(image_gen.generate_for_article, article.id, user.id, config, prompt)
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
        if gen_enabled and article.image_url is None and article.image_gen_attempted_at is None
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
        background.add_task(image_gen.generate_for_article, article.id, user.id, config, prompt)
    return {article.id for article, _ in claimed}


@router.get("/{article_id}/related", response_model=list[RelatedArticleItem])
async def get_related(
    article_id: int,
    user: CurrentUser,
    session: DbSession,
):
    article = await accessible_article(session, user.id, article_id)
    return [
        RelatedArticleItem(
            id=row.article.id,
            title=row.article.title,
            feed_title=row.feed_title,
            published_at=row.article.published_at,
            is_read=bool(row.state and row.state.is_read),
            tier=row.tier,
        )
        for row in await related_articles(session, user.id, article)
    ]


@router.get("/{article_id}", response_model=ArticleDetail)
async def get_article(
    article_id: int,
    background: BackgroundTasks,
    user: CurrentUser,
    session: DbSession,
):
    article = await accessible_article(session, user.id, article_id)
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

    item = to_list_item(article, feed.display_title, state)
    return ArticleDetail(
        **item.model_dump(exclude={"entities", "image_pending"}),
        content_html=article.content_html,
        summary_model=article.summary_model,
        summary_skipped_reason=article.summary_skipped_reason,
        entities=full_entities,
        image_pending=image_pending,
    )


@router.get("/{article_id}/generated-image")
async def get_generated_image(
    article_id: int,
    session: DbSession,
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


def _read_state_values(is_read: bool, source: str) -> dict:
    """Column updates for a read-state flip; unreading clears provenance."""
    if is_read:
        return {"is_read": True, "read_at": func.now(), "read_source": source}
    return {"is_read": False, "read_at": None, "read_source": None}


@router.post("/state/batch", status_code=204)
async def set_state_batch(
    body: ArticleStateBatchIn,
    user: CurrentUser,
    session: DbSession,
):
    """Bulk read flips from scroll auto-read. Ids outside the user's
    subscriptions are silently ignored, and rows already in the requested
    state are left untouched — a re-mark never overwrites when/how an
    article was first read, so duplicate flushes are harmless."""
    accessible = (
        await session.scalars(
            select(Article.id)
            .join(
                Subscription,
                and_(
                    Subscription.feed_id == Article.feed_id,
                    Subscription.user_id == user.id,
                ),
            )
            .where(Article.id.in_(body.article_ids))
        )
    ).all()
    if accessible:
        values = _read_state_values(body.is_read, body.read_source)
        stmt = (
            pg_insert(UserArticleState)
            .values([{"user_id": user.id, "article_id": aid, **values} for aid in accessible])
            .on_conflict_do_update(
                index_elements=["user_id", "article_id"],
                set_=values,
                where=UserArticleState.is_read.is_not(values["is_read"]),
            )
        )
        await session.execute(stmt)
    # The frontier may predate this flush (deepest article of the session),
    # so check its access on its own rather than against this batch's ids.
    frontier = None
    if body.frontier_article_id is not None:
        frontier = await session.scalar(
            select(Article)
            .join(
                Subscription,
                and_(
                    Subscription.feed_id == Article.feed_id,
                    Subscription.user_id == user.id,
                ),
            )
            .where(Article.id == body.frontier_article_id)
        )
    if frontier is not None:
        scope = f"feed:{body.frontier_feed_id}" if body.frontier_feed_id is not None else "inbox"
        position = {
            "user_id": user.id,
            "scope": scope,
            "published_at": frontier.published_at,
            "article_id": frontier.id,
        }
        await session.execute(
            pg_insert(UserReadingPosition)
            .values(**position)
            .on_conflict_do_update(
                index_elements=["user_id", "scope"],
                set_={"published_at": frontier.published_at, "article_id": frontier.id},
            )
        )
    await session.commit()


@router.post("/{article_id}/state", response_model=ArticleListItem)
async def set_state(
    article_id: int,
    body: ArticleStateIn,
    user: CurrentUser,
    session: DbSession,
):
    article = await accessible_article(session, user.id, article_id)

    values: dict = {}
    if body.is_read is not None:
        # Unlike the batch route this overwrites provenance on purpose:
        # actually opening an article upgrades a passive 'scrolled' mark.
        values.update(_read_state_values(body.is_read, body.read_source or "opened"))
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
    return to_list_item(article, feed.display_title, state)


@router.post("/mark-all-read", status_code=204)
async def mark_all_read(
    body: MarkAllReadIn,
    user: CurrentUser,
    session: DbSession,
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
        values = _read_state_values(True, "mark_all")
        insert_stmt = (
            pg_insert(UserArticleState)
            .values([{"user_id": user.id, "article_id": aid, **values} for aid in article_ids])
            .on_conflict_do_update(
                index_elements=["user_id", "article_id"],
                set_=values,
            )
        )
        await session.execute(insert_stmt)
        await session.commit()
