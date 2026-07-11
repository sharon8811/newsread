from sqlalchemy import select

from app import catalog_embeddings
from app.models import CatalogEntry, CatalogEntryEmbedding


async def test_catalog_embedding_text_and_upsert(session, monkeypatch):
    entry = CatalogEntry(
        url="https://example.com/rss",
        title="Signal",
        description="Independent technology analysis",
        site_url="https://example.com",
        category="Tech",
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    monkeypatch.setattr(catalog_embeddings.settings, "openai_embedding_model", "emb")

    calls = []

    async def fake_embed(texts):
        calls.extend(texts)
        return [[0.2, 0.8]]

    monkeypatch.setattr(catalog_embeddings.embeddings, "embed_texts", fake_embed)
    assert await catalog_embeddings.embed_catalog_entries(session, [entry]) == 1
    stored = await session.scalar(
        select(CatalogEntryEmbedding).where(CatalogEntryEmbedding.catalog_entry_id == entry.id)
    )
    assert stored.model == "emb"
    assert "Independent technology analysis" in calls[0]
    assert stored.content_hash == catalog_embeddings.content_hash(entry)

    entry.description = "Changed description"
    await session.commit()
    await catalog_embeddings.embed_catalog_entries(session, [entry])
    await session.refresh(stored)
    assert stored.content_hash == catalog_embeddings.content_hash(entry)
