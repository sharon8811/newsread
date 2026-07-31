"""Runtime-managed HNSW indexes (app/ann.py): creation, reconciliation on a
model switch, capability gating, and index-served ordering matching exact."""

import math

import pytest_asyncio
from sqlalchemy import select, text

from app import ann
from app.models import (
    ArticleEmbedding,
    BrowserHistoryDocument,
    BrowserHistoryDocumentEmbedding,
    CatalogEntry,
    CatalogEntryEmbedding,
)

DIM = 8


def _vector(index: int) -> list[float]:
    """Unit vectors at distinct angles: pairwise cosine distances differ by
    far more than the fp16 epsilon, so halfvec ordering equals exact."""
    angle = index * 0.15
    return [math.cos(angle), math.sin(angle)] + [0.0] * (DIM - 2)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    return 1 - dot / norm


async def _seed_articles(data, session, *, model="emb", count=12) -> list[int]:
    feed = await data.feed()
    ids = []
    for i in range(count):
        article = await data.article(feed)
        session.add(ArticleEmbedding(article_id=article.id, model=model, embedding=_vector(i)))
        ids.append(article.id)
    await session.commit()
    return ids


def _configure(monkeypatch, model="emb"):
    monkeypatch.setattr(ann.embeddings, "is_configured", lambda: True)
    monkeypatch.setattr(ann.settings, "openai_embedding_model", model)


async def _index_names(session, table: str) -> set[str]:
    return set(
        (
            await session.scalars(
                text(
                    "SELECT indexname FROM pg_indexes WHERE tablename = :table "
                    "AND indexname LIKE 'hnsw#_%' ESCAPE '#'"
                ),
                {"table": table},
            )
        ).all()
    )


@pytest_asyncio.fixture(autouse=True)
async def _clean_hnsw_indexes(session):
    """Truncation between tests clears rows but not indexes; start clean."""
    for table in ann.INDEXED_TABLES:
        for name in await _index_names(session, table):
            await session.execute(text(f'DROP INDEX IF EXISTS "{name}"'))
    await session.commit()


async def test_ensure_builds_partial_halfvec_index(data, session, monkeypatch):
    await _seed_articles(data, session)
    _configure(monkeypatch)
    await ann.ensure_indexes()

    expected = ann.index_name("article_embeddings", "emb", DIM)
    definition = await session.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
        {"name": expected},
    )
    assert definition is not None
    assert "USING hnsw" in definition
    assert f"halfvec({DIM})" in definition
    assert "WHERE" in definition and "'emb'" in definition
    # Idempotent: a second pass changes nothing and does not error.
    await ann.ensure_indexes()
    assert await _index_names(session, "article_embeddings") == {expected}


async def test_indexed_ordering_matches_exact(data, session, monkeypatch):
    ids = await _seed_articles(data, session)
    _configure(monkeypatch)
    await ann.ensure_indexes()

    # Between vectors 3 and 4 but off-center, so every distance is distinct
    # (a query AT a seeded angle would tie its two neighbors).
    angle = 3 * 0.15 + 0.025
    query = [math.cos(angle), math.sin(angle)] + [0.0] * (DIM - 2)
    await ann.relax_scan(session)
    got = list(
        await session.scalars(
            select(ArticleEmbedding.article_id)
            .where(ArticleEmbedding.model == "emb")
            .order_by(ann.knn_distance(ArticleEmbedding.embedding, query))
            .limit(5)
        )
    )
    exact = sorted(range(len(ids)), key=lambda i: _cosine(_vector(i), query))
    assert got == [ids[i] for i in exact[:5]]


async def test_model_switch_replaces_index(data, session, monkeypatch):
    ids = await _seed_articles(data, session)
    _configure(monkeypatch)
    await ann.ensure_indexes()
    old = ann.index_name("article_embeddings", "emb", DIM)

    # The worker re-embeds under the new model (different dimension); the
    # next ensure pass builds the new index and drops the old model's.
    await session.execute(
        ArticleEmbedding.__table__.delete().where(ArticleEmbedding.article_id == ids[0])
    )
    session.add(ArticleEmbedding(article_id=ids[0], model="emb2", embedding=[1.0, 0.0]))
    await session.commit()
    _configure(monkeypatch, model="emb2")
    await ann.ensure_indexes()

    assert await _index_names(session, "article_embeddings") == {
        ann.index_name("article_embeddings", "emb2", 2)
    }
    assert old != ann.index_name("article_embeddings", "emb2", 2)


async def test_ensure_covers_catalog_and_history_tables(users, session, monkeypatch):
    user = await users.create()
    entry = CatalogEntry(
        url="https://example.com/rss",
        title="Signal",
        description="d",
        site_url="https://example.com",
        category="Tech",
    )
    session.add(entry)
    await session.commit()
    session.add(
        CatalogEntryEmbedding(
            catalog_entry_id=entry.id, model="emb", content_hash="x", embedding=_vector(0)
        )
    )
    document = BrowserHistoryDocument(
        user_id=user.id,
        content_hash="a" * 64,
        object_key="users/1/history/documents/sha256/aa/a",
        storage_status="ready",
        byte_size=100,
        character_count=100,
        text_excerpt="excerpt",
        extraction_version="history-dom-v2",
    )
    session.add(document)
    await session.commit()
    session.add(
        BrowserHistoryDocumentEmbedding(
            document_id=document.id,
            chunk_index=0,
            model="emb",
            embedding=_vector(1),
            input_hash="0" * 64,
        )
    )
    await session.commit()
    _configure(monkeypatch)
    await ann.ensure_indexes()

    for table in ("catalog_entry_embeddings", "browser_history_document_embeddings"):
        assert await _index_names(session, table) == {ann.index_name(table, "emb", DIM)}


async def test_ensure_skips_unconfigured_and_oversized(data, session, monkeypatch):
    await _seed_articles(data, session, count=1)

    monkeypatch.setattr(ann.embeddings, "is_configured", lambda: False)
    await ann.ensure_indexes()
    assert await _index_names(session, "article_embeddings") == set()

    # Beyond the HNSW dimension ceiling: no index, no error.
    _configure(monkeypatch)
    monkeypatch.setattr(ann, "HNSW_MAX_DIM", DIM - 1)
    await ann.ensure_indexes()
    assert await _index_names(session, "article_embeddings") == set()


async def test_ensure_drops_index_once_rows_disappear(data, session, monkeypatch):
    await _seed_articles(data, session, count=1)
    _configure(monkeypatch)
    await ann.ensure_indexes()
    assert await _index_names(session, "article_embeddings")

    await session.execute(ArticleEmbedding.__table__.delete())
    await session.commit()
    await ann.ensure_indexes()
    assert await _index_names(session, "article_embeddings") == set()


async def test_ensure_survives_a_failing_table(data, session, monkeypatch):
    await _seed_articles(data, session, count=1)
    _configure(monkeypatch)
    monkeypatch.setattr(ann, "INDEXED_TABLES", ("missing_table", "article_embeddings"))
    await ann.ensure_indexes()
    # The bad table logs a warning; the good one still gets its index.
    assert await _index_names(session, "article_embeddings") == {
        ann.index_name("article_embeddings", "emb", DIM)
    }


class _FakeSession:
    def __init__(self, version):
        self.version = version
        self.executed = []

    async def scalar(self, *_args, **_kwargs):
        return self.version

    async def execute(self, statement, *_args, **_kwargs):
        self.executed.append(str(statement))


async def test_capability_probe_parses_versions(monkeypatch):
    for version, capable in (
        ("0.8.4", True),
        ("1.0", True),
        ("0.7.4", False),
        ("junk", False),
        (None, False),
    ):
        monkeypatch.setattr(ann, "_capable", None)
        assert await ann._iterative_scan_available(_FakeSession(version)) is capable


async def test_relax_scan_gated_on_capability(monkeypatch):
    monkeypatch.setattr(ann, "_capable", None)
    old = _FakeSession("0.7.0")
    await ann.relax_scan(old)
    assert old.executed == []

    monkeypatch.setattr(ann, "_capable", None)
    new = _FakeSession("0.8.0")
    await ann.relax_scan(new)
    assert len(new.executed) == 1 and "iterative_scan" in new.executed[0]

    # Incapable servers also skip index management entirely.
    monkeypatch.setattr(ann, "_capable", False)
    monkeypatch.setattr(ann.embeddings, "is_configured", lambda: True)
    await ann.ensure_indexes()


def test_knn_distance_falls_back_before_capability(monkeypatch):
    """Servers before pgvector 0.7 don't define halfvec: until the probe
    confirms >= 0.8, the expression must stay the plain exact-scan one."""
    monkeypatch.setattr(ann, "_capable", None)
    assert "halfvec" not in str(ann.knn_distance(ArticleEmbedding.embedding, [1.0, 0.0])).lower()
    monkeypatch.setattr(ann, "_capable", False)
    assert "halfvec" not in str(ann.knn_distance(ArticleEmbedding.embedding, [1.0, 0.0])).lower()
    monkeypatch.setattr(ann, "_capable", True)
    assert "halfvec" in str(ann.knn_distance(ArticleEmbedding.embedding, [1.0, 0.0])).lower()


def test_model_filter_inlines_literal(monkeypatch):
    """The partial-index predicate is only provable with the model inlined:
    a bound parameter goes opaque once asyncpg flips to a generic plan and
    the index silently stops being used."""
    from sqlalchemy.dialects import postgresql

    monkeypatch.setattr(ann.settings, "openai_embedding_model", "emb")
    compiled = (
        select(ArticleEmbedding.article_id)
        .where(ann.model_filter(ArticleEmbedding.model))
        .compile(dialect=postgresql.dialect(), compile_kwargs={"render_postcompile": True})
    )
    assert "'emb'" in str(compiled)


def test_index_names_fit_postgres_limit():
    for table in ann.INDEXED_TABLES:
        assert len(ann.index_name(table, "m" * 120, 4000)) <= 63
