"""Embedding maintenance for the curated feed catalog."""

import hashlib

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from . import embeddings
from .config import settings
from .models import CatalogEntry, CatalogEntryEmbedding


def text_for(entry: CatalogEntry) -> str:
    parts = [entry.title, entry.category, entry.description or "", entry.site_url or entry.url]
    return "\n".join(part.strip() for part in parts if part and part.strip())[:4000]


def content_hash(entry: CatalogEntry) -> str:
    return hashlib.sha256(text_for(entry).encode()).hexdigest()


async def embed_catalog_entries(session: AsyncSession, entries: list[CatalogEntry]) -> int:
    if not entries:
        return 0
    vectors = await embeddings.embed_texts([text_for(entry) for entry in entries])
    stmt = pg_insert(CatalogEntryEmbedding).values(
        [
            {
                "catalog_entry_id": entry.id,
                "model": settings.openai_embedding_model,
                "content_hash": content_hash(entry),
                "embedding": vector,
            }
            for entry, vector in zip(entries, vectors, strict=False)
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["catalog_entry_id"],
        set_={
            "model": stmt.excluded.model,
            "content_hash": stmt.excluded.content_hash,
            "embedding": stmt.excluded.embedding,
            "embedded_at": func.now(),
        },
    )
    await session.execute(stmt)
    await session.commit()
    return len(entries)


async def embed_catalog_batch(session: AsyncSession, limit: int = 100) -> int:
    """Embed active entries whose metadata or configured model changed."""
    if not embeddings.is_configured():
        return 0
    entries = list(
        (
            await session.scalars(
                select(CatalogEntry)
                .outerjoin(
                    CatalogEntryEmbedding,
                    CatalogEntryEmbedding.catalog_entry_id == CatalogEntry.id,
                )
                .where(CatalogEntry.is_active.is_(True))
                .where(
                    or_(
                        CatalogEntryEmbedding.catalog_entry_id.is_(None),
                        CatalogEntryEmbedding.model != settings.openai_embedding_model,
                    )
                )
                .order_by(CatalogEntry.id)
                .limit(limit)
            )
        ).all()
    )
    # Model matches can still have stale content. Check them separately without
    # forcing SQL to reproduce the application-side normalized text hash.
    if len(entries) < limit:
        rows = await session.execute(
            select(CatalogEntry, CatalogEntryEmbedding.content_hash)
            .join(CatalogEntryEmbedding)
            .where(
                CatalogEntry.is_active.is_(True),
                CatalogEntryEmbedding.model == settings.openai_embedding_model,
            )
            .order_by(CatalogEntry.id)
        )
        for entry, stored_hash in rows:
            if len(entries) >= limit:
                break
            if stored_hash != content_hash(entry):
                entries.append(entry)
    return await embed_catalog_entries(session, entries)
