"""LLM-extracted named entities (people, companies, products) for
cross-article linking. Unlike the link-based enrichers, there is no external
API behind these: the Entity row is just a normalized name (canonical_key =
casefolded, data.name = display form). They deliberately stay out of the
same-story leg of related coverage and out of UI badges (badge_for returns
{} for unknown kinds); their consumers are ranking signals and the
"not interested" rule chips.
"""

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from . import llm
from .models import Article, ArticleEntity, Entity

logger = logging.getLogger(__name__)

NER_KINDS = ("person", "org", "product")
MAX_NER_PER_ARTICLE = 8
# Same clipping rationale as embeddings.text_for: the summary is the best
# extraction input, then the fetched body; the feed excerpt as a last resort.
_MAX_BODY_CHARS = 4000


def body_for(article: Article) -> str:
    return article.summary_medium or article.full_text[:_MAX_BODY_CHARS] or article.excerpt


async def extract_named(
    session: AsyncSession,
    article: Article,
    *,
    config: llm.LLMConfig | None = None,
    usage: llm.TokenUsage | None = None,
) -> int:
    """Tag one article; returns how many entities were linked. Re-running is
    idempotent (name upsert + link on_conflict_do_nothing)."""
    pairs = await llm.named_entities(article.title, body_for(article), config=config, usage=usage)
    linked = 0
    for kind, name in pairs[:MAX_NER_PER_ARTICLE]:
        if kind not in NER_KINDS:
            continue
        key = name.casefold()[:512]
        # Insert-then-select instead of select-then-insert: concurrent
        # extractions of the same name must not race the unique constraint.
        await session.execute(
            pg_insert(Entity)
            .values(kind=kind, canonical_key=key, url="", data={"name": name})
            .on_conflict_do_nothing(index_elements=["kind", "canonical_key"])
        )
        entity_id = await session.scalar(
            select(Entity.id).where(Entity.kind == kind, Entity.canonical_key == key)
        )
        await session.execute(
            pg_insert(ArticleEntity)
            .values(
                article_id=article.id,
                entity_id=entity_id,
                source="ner",
                position=linked,
            )
            .on_conflict_do_nothing(index_elements=["article_id", "entity_id"])
        )
        linked += 1
    return linked
