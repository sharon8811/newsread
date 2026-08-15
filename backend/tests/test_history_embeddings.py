from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import history_embeddings, worker
from app.history_content import canonicalize_history_document_value
from app.models import (
    BrowserHistoryDocument,
    BrowserHistoryDocumentEmbedding,
    BrowserHistoryEmbeddingUsage,
    BrowserHistoryPage,
    BrowserHistoryPageDocument,
    BrowserHistorySettings,
)


def _page(user_id: int, index: int, *, visited_at: datetime) -> BrowserHistoryPage:
    return BrowserHistoryPage(
        user_id=user_id,
        url_hash=f"{index:064d}",
        url=f"https://page{index}.example.com/",
        title=f"Page {index}",
        hostname=f"page{index}.example.com",
        first_visited_at=visited_at,
        last_visited_at=visited_at,
        visit_count=1,
        captured_at=visited_at,
    )


def _document(user_id: int, content_hash: str) -> BrowserHistoryDocument:
    return BrowserHistoryDocument(
        user_id=user_id,
        content_hash=content_hash,
        object_key=f"users/{user_id}/history/documents/sha256/{content_hash[:2]}/{content_hash}",
        storage_status="ready",
        byte_size=100,
        character_count=100,
        text_excerpt="Document excerpt",
        extraction_version="history-dom-v2",
    )


def test_history_document_chunks_preserve_block_anchors(monkeypatch):
    monkeypatch.setattr(history_embeddings.settings, "embedding_input_max_chars", 6000)
    canonical = canonicalize_history_document_value(
        {
            "schema_version": 1,
            "extraction_version": "history-dom-v2",
            "content_type": "article",
            "language": "en",
            "blocks": [
                {"id": "b0001", "kind": "heading", "text": "A heading"},
                {"id": "b0002", "kind": "paragraph", "text": "x" * 7000},
                {"id": "b0003", "kind": "quote", "text": "A final quote"},
            ],
        }
    )
    document = _document(1, canonical.content_hash)

    chunks = history_embeddings.document_chunks(document, canonical.canonical_bytes)

    assert len(chunks) == 2
    assert all(len(chunk.text) <= history_embeddings.document_chunk_max_chars() for chunk in chunks)
    assert (chunks[0].block_start_id, chunks[0].block_end_id) == ("b0001", "b0002")
    assert (chunks[1].block_start_id, chunks[1].block_end_id) == ("b0002", "b0003")
    assert {chunk.input_hash for chunk in chunks} == {canonical.content_hash}


def test_history_document_chunks_follow_the_context_budget(monkeypatch):
    """Chunks are sized to what one embedding request carries: a chunk the
    provider would only see the head of would cite blocks its vector missed."""
    monkeypatch.setattr(history_embeddings.settings, "embedding_input_max_chars", 500)
    canonical = canonicalize_history_document_value(
        {
            "schema_version": 1,
            "extraction_version": "history-dom-v2",
            "content_type": "article",
            "language": "en",
            "blocks": [{"id": "b0001", "kind": "paragraph", "text": "x" * 1600}],
        }
    )

    chunks = history_embeddings.document_chunks(
        _document(1, canonical.content_hash), canonical.canonical_bytes
    )

    assert len(chunks) == 4
    assert all(len(chunk.text) <= 500 for chunk in chunks)


async def test_history_document_embedding_replaces_current_model_chunks(
    session,
    users,
    monkeypatch,
):
    user = await users.create()
    document = _document(user.id, "a" * 64)
    session.add(document)
    await session.commit()
    await session.refresh(document)
    chunks = [
        history_embeddings.HistoryDocumentChunk(
            index=0,
            text="first chunk",
            input_hash=document.content_hash,
            block_start_id="b0001",
            block_end_id="b0002",
        ),
        history_embeddings.HistoryDocumentChunk(
            index=1,
            text="second chunk",
            input_hash=document.content_hash,
            block_start_id="b0003",
            block_end_id="b0003",
        ),
    ]

    async def fake_load(*args, **kwargs):
        return chunks

    async def fake_embed_texts(texts, **_):
        assert texts in (["first chunk", "second chunk"], ["first chunk"])
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(history_embeddings, "load_document_chunks", fake_load)
    monkeypatch.setattr(history_embeddings.embeddings, "embed_texts", fake_embed_texts)

    assert await history_embeddings.embed_documents(session, [document]) == 1
    rows = list(
        await session.scalars(
            select(BrowserHistoryDocumentEmbedding).order_by(
                BrowserHistoryDocumentEmbedding.chunk_index
            )
        )
    )
    assert [(row.block_start_id, row.block_end_id) for row in rows] == [
        ("b0001", "b0002"),
        ("b0003", "b0003"),
    ]
    assert {row.input_hash for row in rows} == {document.content_hash}

    chunks.pop()
    assert await history_embeddings.embed_documents(session, [document]) == 1
    assert await session.scalar(select(BrowserHistoryDocumentEmbedding.chunk_index)) == 0


async def test_history_document_embedding_rechunks_when_the_provider_says_too_long(
    session,
    users,
    monkeypatch,
):
    """A shortened payload alone would leave a chunk holding the block range of
    text its vector never saw, so search could cite what it never read. The
    document is cut into smaller chunks and embedded again instead."""
    user = await users.create()
    document = _document(user.id, "a" * 64)
    session.add(document)
    await session.commit()
    await session.refresh(document)
    monkeypatch.setattr(history_embeddings.settings, "embedding_input_max_chars", 1000)

    async def fake_load(active_session, doc, *, storage=None, max_chars=None):
        return [
            history_embeddings.HistoryDocumentChunk(
                index=index,
                text="x" * max_chars,
                input_hash=doc.content_hash,
                block_start_id=f"b{index:04d}",
                block_end_id=f"b{index:04d}",
            )
            for index in range(1000 // max_chars)
        ]

    embedded_lengths = []

    async def fake_embed_texts(texts, *, shrink=True):
        assert shrink is False
        embedded_lengths.append([len(text) for text in texts])
        if any(len(text) > 500 for text in texts):
            raise RuntimeError("Error code: 400 - the input length exceeds the context length")
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(history_embeddings, "load_document_chunks", fake_load)
    monkeypatch.setattr(history_embeddings.embeddings, "embed_texts", fake_embed_texts)

    assert await history_embeddings.embed_documents(session, [document]) == 1
    assert embedded_lengths == [[1000], [500, 500]]
    rows = list(
        await session.scalars(
            select(BrowserHistoryDocumentEmbedding).order_by(
                BrowserHistoryDocumentEmbedding.chunk_index
            )
        )
    )
    # Two rows, each anchored to the block range its own vector covers.
    assert [(row.chunk_index, row.block_start_id) for row in rows] == [(0, "b0000"), (1, "b0001")]


async def test_history_document_embedding_reraises_a_transient_failure(
    session,
    users,
    monkeypatch,
):
    user = await users.create()
    document = _document(user.id, "a" * 64)
    session.add(document)
    await session.commit()
    await session.refresh(document)
    attempts = []

    async def fake_load(*args, **kwargs):
        return [
            history_embeddings.HistoryDocumentChunk(
                index=0,
                text="chunk",
                input_hash=document.content_hash,
                block_start_id="b0001",
                block_end_id="b0001",
            )
        ]

    async def fake_embed_texts(texts, **_):
        attempts.append(texts)
        raise RuntimeError("Error code: 429 - rate limit exceeded")

    monkeypatch.setattr(history_embeddings, "load_document_chunks", fake_load)
    monkeypatch.setattr(history_embeddings.embeddings, "embed_texts", fake_embed_texts)

    with pytest.raises(RuntimeError):
        await history_embeddings.embed_documents(session, [document])
    assert len(attempts) == 1


async def test_history_document_worker_only_catches_up_linked_v2_documents(
    session,
    users,
    monkeypatch,
):
    user = await users.create()
    now = datetime.now(UTC)
    linked = _document(user.id, "b" * 64)
    linked_with_failure = _document(user.id, "e" * 64)
    unlinked = _document(user.id, "c" * 64)
    legacy = _document(user.id, "d" * 64)
    legacy.extraction_version = "history-inline-v1"
    page = _page(user.id, 20, visited_at=now)
    session.add_all([linked, linked_with_failure, unlinked, legacy, page])
    await session.flush()
    session.add(
        BrowserHistoryPageDocument(
            page_id=page.id,
            document_id=linked.id,
            first_seen_at=now,
            last_seen_at=now,
            captured_at=now,
        )
    )
    session.add(
        BrowserHistoryPageDocument(
            page_id=page.id,
            document_id=linked_with_failure.id,
            first_seen_at=now,
            last_seen_at=now,
            captured_at=now,
        )
    )
    await session.commit()

    monkeypatch.setattr(history_embeddings, "is_configured", lambda: True)
    seen: list[int] = []

    async def fake_embed_document(document_id):
        seen.append(document_id)
        if document_id == linked_with_failure.id:
            raise RuntimeError("corrupt object")
        return 1

    monkeypatch.setattr(history_embeddings, "embed_document", fake_embed_document)
    assert await worker.embed_history_documents_batch() == 1
    assert seen == [linked_with_failure.id, linked.id]


async def test_daily_embedding_quota_is_reserved_per_user(
    session,
    users,
    monkeypatch,
):
    user = await users.create()
    first = _document(user.id, "f" * 64)
    second = _document(user.id, "9" * 64)
    session.add_all([first, second])
    await session.commit()

    monkeypatch.setattr(history_embeddings, "is_configured", lambda: True)
    monkeypatch.setattr(
        history_embeddings.settings,
        "history_embedding_daily_limit",
        1,
    )
    embedded: list[int] = []

    async def fake_embed_documents(active_session, documents, **kwargs):
        embedded.append(documents[0].id)
        await active_session.commit()
        return 1

    monkeypatch.setattr(
        history_embeddings,
        "embed_documents",
        fake_embed_documents,
    )

    assert await history_embeddings.embed_document(first.id) == 1
    assert await history_embeddings.embed_document(second.id) == 0
    assert embedded == [first.id]
    usage = await session.scalar(select(BrowserHistoryEmbeddingUsage))
    assert usage is not None
    assert usage.document_count == 1


async def test_daily_history_retention_deletes_expired_rows_only(
    session,
    users,
):
    now = datetime.now(UTC)
    expiring_user = await users.create(username="expiring")
    forever_user = await users.create(username="forever")
    expiring_settings = BrowserHistorySettings(
        user_id=expiring_user.id,
        retention_days=30,
    )
    forever_settings = BrowserHistorySettings(user_id=forever_user.id)
    session.add_all([expiring_settings, forever_settings])
    await session.flush()
    forever_settings.retention_days = None
    expired = _page(
        expiring_user.id,
        3,
        visited_at=now - timedelta(days=31),
    )
    current = _page(
        expiring_user.id,
        4,
        visited_at=now - timedelta(days=30),
    )
    forever = _page(
        forever_user.id,
        5,
        visited_at=now - timedelta(days=3650),
    )
    session.add_all([expired, current, forever])
    await session.commit()

    assert await worker.cleanup_history_retention(now=now) == 1
    remaining = set(await session.scalars(select(BrowserHistoryPage.id)))
    assert remaining == {current.id, forever.id}
