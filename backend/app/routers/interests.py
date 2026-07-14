"""'Not interested' rules: reason options, rule CRUD, and creation-time
backfill. Ongoing matching happens in the worker's suppression stage
(suppressions.py) — this router only pays the one-off costs: one small LLM
call to suggest topic phrases, one embedding call when a phrase is chosen."""

import logging
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crypto, embeddings, llm, suppressions
from ..config import settings
from ..db import get_session
from ..enrichers import badge_for
from ..models import (
    Article,
    ArticleEmbedding,
    ArticleEntity,
    ArticleSuppression,
    DislikeRuleEmbedding,
    Entity,
    User,
    UserDislikeRule,
)
from ..schemas import (
    DislikeOptionEntity,
    DislikeOptionsOut,
    DislikeRuleCreateOut,
    DislikeRuleIn,
    DislikeRuleOut,
    DislikeRulePreviewItem,
)
from ..security import get_current_user
from .articles import current_embedding, user_can_access

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/interests", tags=["interests"])

# Cosine-distance cutoffs (1 - similarity; smaller = stricter), calibrated
# against text-embedding-3-small: phrase-vs-article lands ~0.5-0.6 on topic
# and ~0.85+ off topic; article-vs-article is ~0.4 for same-sector-but-
# different-story and well below 0.35 for true follow-ups. Stored per rule,
# so tuning later is a data change, not a schema change.
STORY_THRESHOLD = 0.35
TOPIC_THRESHOLD = 0.70
STORY_TTL = timedelta(days=14)
PREVIEW_LIMIT = 5


async def _accessible_article(session: AsyncSession, user: User, article_id: int) -> Article:
    article = await session.get(Article, article_id)
    if article is None or not await user_can_access(session, user.id, article):
        raise HTTPException(status_code=404, detail="Article not found")
    return article


@router.get("/dislike-options/{article_id}", response_model=DislikeOptionsOut)
async def dislike_options(
    article_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Reason chips for the 'not interested' popover. Every leg degrades to
    less choice, never an error: no LLM -> no topics, no embeddings -> no
    story mute, entities always work."""
    article = await _accessible_article(session, user, article_id)

    rows = await session.execute(
        select(ArticleEntity, Entity)
        .join(Entity, Entity.id == ArticleEntity.entity_id)
        .where(ArticleEntity.article_id == article.id)
        .order_by(ArticleEntity.source != "primary", ArticleEntity.position)
    )
    entities = [
        DislikeOptionEntity(
            entity_id=entity.id,
            kind=entity.kind,
            key=entity.canonical_key,
            label=str(badge_for(entity.kind, entity.data or {}).get("label") or (entity.data or {}).get("name") or entity.canonical_key),
        )
        for _, entity in rows
    ]

    story_available = await current_embedding(session, article.id) is not None

    topics: list[str] = []
    if embeddings.is_configured():  # a phrase rule is useless without a vector
        try:
            config = await llm.resolve_config(session, user.id)
        except crypto.TokenCryptoError:
            config = llm.system_config()
        if config is not None:
            user_id = user.id
            usage = llm.TokenUsage()
            started = time.monotonic()
            try:
                topics = await llm.dislike_topics(
                    article.title,
                    article.summary_medium or article.excerpt or "",
                    config=config,
                    usage=usage,
                )
            except Exception as exc:
                logger.warning("Topic suggestion failed for article %s: %s", article.id, exc)
                await session.rollback()
                await llm.record_usage(
                    session, user_id=user_id, feature="topics", config=config, usage=usage,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    status="error", error=str(exc),
                )
            else:
                await llm.record_usage(
                    session, user_id=user_id, feature="topics", config=config, usage=usage,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

    return DislikeOptionsOut(entities=entities, topics=topics, story_available=story_available)


async def _existing_rule(
    session: AsyncSession, user_id: int, body: DislikeRuleIn
) -> UserDislikeRule | None:
    """Clicking the same chip twice must not stack duplicate rules."""
    stmt = select(UserDislikeRule).where(
        UserDislikeRule.user_id == user_id, UserDislikeRule.kind == body.kind
    )
    if body.kind == "entity":
        stmt = stmt.where(UserDislikeRule.entity_id == body.entity_id)
    elif body.kind == "topic":
        stmt = stmt.where(func.lower(UserDislikeRule.phrase) == (body.phrase or "").casefold())
    else:  # article | story
        stmt = stmt.where(UserDislikeRule.article_id == body.article_id)
    return await session.scalar(stmt.limit(1))


async def _rule_out(session: AsyncSession, rule: UserDislikeRule) -> DislikeRuleOut:
    hidden = await session.scalar(
        select(func.count())
        .select_from(ArticleSuppression)
        .where(ArticleSuppression.rule_id == rule.id)
    )
    return DislikeRuleOut(
        id=rule.id,
        kind=rule.kind,
        label=rule.label,
        phrase=rule.phrase,
        entity_id=rule.entity_id,
        article_id=rule.article_id,
        expires_at=rule.expires_at,
        hidden_count=hidden or 0,
        created_at=rule.created_at,
    )


async def _preview(session: AsyncSession, rule_id: int, limit: int) -> list[DislikeRulePreviewItem]:
    rows = await session.execute(
        select(Article.id, Article.title)
        .join(ArticleSuppression, ArticleSuppression.article_id == Article.id)
        .where(ArticleSuppression.rule_id == rule_id)
        .order_by(Article.id.desc())
        .limit(limit)
    )
    return [DislikeRulePreviewItem(id=aid, title=title) for aid, title in rows]


@router.post("/dislikes", response_model=DislikeRuleCreateOut)
async def create_dislike(
    body: DislikeRuleIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if body.kind in ("article", "story"):
        if body.article_id is None:
            raise HTTPException(status_code=422, detail="article_id is required")
    elif body.kind == "entity":
        if body.entity_id is None:
            raise HTTPException(status_code=422, detail="entity_id is required")
    elif not (body.phrase or "").strip():
        raise HTTPException(status_code=422, detail="phrase is required")

    existing = await _existing_rule(session, user.id, body)
    if existing is not None:
        return DislikeRuleCreateOut(
            rule=await _rule_out(session, existing),
            preview=await _preview(session, existing.id, PREVIEW_LIMIT),
        )

    user_id = user.id  # survives the rollback in the race handler below
    rule = UserDislikeRule(user_id=user_id, kind=body.kind)
    cutoff = datetime.now(timezone.utc) - suppressions.BACKFILL_WINDOW

    try:
        if body.kind in ("article", "story"):
            article = await _accessible_article(session, user, body.article_id)
            rule.article_id = article.id
            rule.label = article.title[:512]
            if body.kind == "story":
                source = await current_embedding(session, article.id)
                if source is None:
                    raise HTTPException(
                        status_code=422,
                        detail="This article has no embedding yet — try again in a few minutes.",
                    )
                rule.threshold = STORY_THRESHOLD
                rule.expires_at = datetime.now(timezone.utc) + STORY_TTL
                session.add(rule)
                await session.flush()
                # Snapshot copy, deliberately not a live reference: the rule mutes
                # "this story as the user saw it" even if the article re-embeds.
                session.add(DislikeRuleEmbedding(
                    rule_id=rule.id, model=source.model, embedding=source.embedding
                ))
                await session.flush()
                await suppressions.apply_vector_rules(session, cutoff=cutoff, rule_id=rule.id)
            else:
                session.add(rule)
                await session.flush()
                await session.execute(
                    ArticleSuppression.__table__.insert().values(
                        user_id=user_id, article_id=article.id, rule_id=rule.id
                    )
                )
        elif body.kind == "entity":
            entity = await session.get(Entity, body.entity_id)
            if entity is None:
                raise HTTPException(status_code=404, detail="Entity not found")
            rule.entity_id = entity.id
            rule.label = str(badge_for(entity.kind, entity.data or {}).get("label") or (entity.data or {}).get("name") or entity.canonical_key)[:512]
            session.add(rule)
            await session.flush()
            await suppressions.apply_entity_rules(session, cutoff=cutoff, rule_id=rule.id)
        else:  # topic
            if not embeddings.is_configured():
                raise HTTPException(
                    status_code=422, detail="Topic muting needs embeddings configured on the server."
                )
            phrase = " ".join(body.phrase.split())
            try:
                [vector] = await embeddings.embed_texts([phrase])
            except Exception as exc:
                logger.warning("Embedding dislike phrase failed: %s", exc)
                raise HTTPException(status_code=502, detail="The embedding request failed")
            rule.phrase = phrase
            rule.label = phrase[:512]
            rule.threshold = TOPIC_THRESHOLD
            session.add(rule)
            await session.flush()
            session.add(DislikeRuleEmbedding(
                rule_id=rule.id, model=settings.openai_embedding_model, embedding=vector
            ))
            await session.flush()
            await suppressions.apply_vector_rules(session, cutoff=cutoff, rule_id=rule.id)
        await session.commit()
    except IntegrityError:
        # Lost a duplicate-create race (unique partial indexes; e.g. a double
        # click firing two POSTs) — the winner's rule is the one wanted.
        await session.rollback()
        rule = await _existing_rule(session, user_id, body)
        if rule is None:
            raise HTTPException(status_code=409, detail="Rule creation conflicted — retry")
    return DislikeRuleCreateOut(
        rule=await _rule_out(session, rule),
        preview=await _preview(session, rule.id, PREVIEW_LIMIT),
    )


@router.get("/dislikes", response_model=list[DislikeRuleOut])
async def list_dislikes(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    counts = (
        select(ArticleSuppression.rule_id, func.count().label("n"))
        .group_by(ArticleSuppression.rule_id)
        .subquery()
    )
    rows = await session.execute(
        select(UserDislikeRule, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.rule_id == UserDislikeRule.id)
        .where(UserDislikeRule.user_id == user.id)
        .order_by(UserDislikeRule.id.desc())
    )
    return [
        DislikeRuleOut(
            id=rule.id,
            kind=rule.kind,
            label=rule.label,
            phrase=rule.phrase,
            entity_id=rule.entity_id,
            article_id=rule.article_id,
            expires_at=rule.expires_at,
            hidden_count=hidden,
            created_at=rule.created_at,
        )
        for rule, hidden in rows
    ]


@router.get("/dislikes/{rule_id}/articles", response_model=list[DislikeRulePreviewItem])
async def dislike_articles(
    rule_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    rule = await session.get(UserDislikeRule, rule_id)
    if rule is None or rule.user_id != user.id:
        raise HTTPException(status_code=404, detail="Rule not found")
    return await _preview(session, rule.id, 20)


@router.delete("/dislikes/{rule_id}", status_code=204)
async def delete_dislike(
    rule_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    rule = await session.get(UserDislikeRule, rule_id)
    if rule is None or rule.user_id != user.id:
        raise HTTPException(status_code=404, detail="Rule not found")
    # The FK cascade takes the rule's suppressions with it — that IS the undo.
    await session.delete(rule)
    await session.commit()
    return Response(status_code=204)
