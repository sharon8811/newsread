from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import history_embeddings, worker
from app.config import settings
from app.history_content import canonicalize_history_document_value
from app.models import (
    BrowserHistoryDocument,
    BrowserHistoryDocumentEmbedding,
    BrowserHistoryEmbedding,
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
        text=f"Visible history text {index}",
        text_excerpt=f"Visible history text {index}",
        content_hash=f"{index + 1:064d}",
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


def test_history_document_chunks_preserve_block_anchors():
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
    assert all(len(chunk.text) <= history_embeddings.DOCUMENT_CHUNK_MAX_CHARS for chunk in chunks)
    assert (chunks[0].block_start_id, chunks[0].block_end_id) == ("b0001", "b0002")
    assert (chunks[1].block_start_id, chunks[1].block_end_id) == ("b0002", "b0003")
    assert {chunk.input_hash for chunk in chunks} == {canonical.content_hash}


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

    async def fake_embed_texts(texts):
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


async def test_history_embedding_text_hash_and_upsert(session, users, monkeypatch):
    user = await users.create()
    page = _page(user.id, 1, visited_at=datetime.now(UTC))
    session.add(page)
    await session.commit()
    await session.refresh(page)

    captured = {}

    async def fake_embed_texts(texts):
        captured["texts"] = texts
        return [[0.1, 0.2]]

    monkeypatch.setattr(history_embeddings.embeddings, "embed_texts", fake_embed_texts)
    assert await history_embeddings.embed_pages(session, [page]) == 1
    row = await session.get(BrowserHistoryEmbedding, page.id)
    assert captured["texts"] == [history_embeddings.text_for(page)]
    assert row.input_hash == history_embeddings.input_hash_for(page)
    assert row.model == settings.openai_embedding_model

    page.title = "Changed title"
    page.content_hash = history_embeddings.input_hash_for(page)
    await session.commit()
    assert await history_embeddings.embed_pages(session, [page]) == 1
    await session.refresh(row)
    assert row.input_hash == page.content_hash


async def test_history_embedding_worker_retries_failures_and_reembeds_stale_rows(
    session,
    users,
    monkeypatch,
):
    user = await users.create()
    page = _page(user.id, 2, visited_at=datetime.now(UTC))
    metadata_only = _page(user.id, 21, visited_at=datetime.now(UTC))
    metadata_only.text = ""
    document = _document(user.id, "f" * 64)
    v2_page = _page(user.id, 22, visited_at=datetime.now(UTC))
    session.add_all([page, metadata_only, document, v2_page])
    await session.flush()
    v2_page.current_document_id = document.id
    await session.commit()
    await session.refresh(page)
    session.add(
        BrowserHistoryEmbedding(
            page_id=page.id,
            model="old-model",
            embedding=[0.0, 1.0],
            input_hash="stale",
        )
    )
    await session.commit()

    monkeypatch.setattr(history_embeddings, "is_configured", lambda: True)
    seen = []

    async def fake_embed_pages(worker_session, pages):
        seen.extend(item.id for item in pages)
        return len(pages)

    monkeypatch.setattr(history_embeddings, "embed_pages", fake_embed_pages)
    assert await worker.embed_history_pages_batch() == 1
    assert seen == [page.id]

    async def fail_embed_pages(worker_session, pages):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(history_embeddings, "embed_pages", fail_embed_pages)
    assert await worker.embed_history_pages_batch() == 0


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
