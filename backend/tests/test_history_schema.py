from datetime import UTC, datetime

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.models import (
    BrowserHistoryDocument,
    BrowserHistoryDocumentEmbedding,
    BrowserHistoryImage,
    BrowserHistoryObjectDeletion,
    BrowserHistoryPage,
    BrowserHistoryPageDocument,
    BrowserHistorySummary,
    Conversation,
)


@pytest.mark.asyncio
async def test_deleting_document_cascades_dependents_and_clears_current_page(session, users):
    user = await users.create()
    document = BrowserHistoryDocument(
        user_id=user.id,
        content_hash="a" * 64,
        object_key=f"users/{user.id}/history/documents/sha256/aa/{'a' * 64}",
        storage_status="ready",
        byte_size=100,
        character_count=80,
        extraction_version="history-dom-v2",
    )
    session.add(document)
    await session.flush()

    now = datetime.now(UTC)
    page = BrowserHistoryPage(
        user_id=user.id,
        url_hash="b" * 64,
        url="https://example.com/article",
        hostname="example.com",
        current_document_id=document.id,
        first_visited_at=now,
        last_visited_at=now,
        visit_count=1,
    )
    session.add(page)
    await session.flush()
    session.add_all(
        [
            BrowserHistoryPageDocument(
                page_id=page.id,
                document_id=document.id,
                first_seen_at=now,
                last_seen_at=now,
            ),
            BrowserHistoryDocumentEmbedding(
                document_id=document.id,
                chunk_index=0,
                model="test-embedding",
                embedding=[0.1, 0.2],
                input_hash="c" * 64,
            ),
            BrowserHistorySummary(
                document_id=document.id,
                model="test-summary",
                prompt_version="v1",
                input_hash="d" * 64,
                status="ready",
                markdown="Summary",
                citations=[],
            ),
            Conversation(
                history_document_id=document.id,
                user_id=user.id,
                kind="history",
            ),
        ]
    )
    await session.commit()

    await session.delete(document)
    await session.commit()

    await session.refresh(page)
    assert page.current_document_id is None
    deletion = await session.scalar(select(BrowserHistoryObjectDeletion))
    assert deletion is not None
    assert (
        deletion.owner_user_id,
        deletion.object_type,
        deletion.object_hash,
        deletion.object_key,
    ) == (
        user.id,
        "document",
        document.content_hash,
        document.object_key,
    )
    for model in (
        BrowserHistoryPageDocument,
        BrowserHistoryDocumentEmbedding,
        BrowserHistorySummary,
        Conversation,
    ):
        assert await session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_summary_status_constraint_matches_worker_state_machine(session, users):
    """Runs against the Alembic-upgraded test schema, not just ORM validation."""
    user = await users.create()
    document = BrowserHistoryDocument(
        user_id=user.id,
        content_hash="e" * 64,
        object_key=f"users/{user.id}/history/documents/sha256/ee/{'e' * 64}",
        storage_status="ready",
        byte_size=100,
        character_count=80,
        extraction_version="history-dom-v2",
    )
    session.add(document)
    await session.flush()
    summary = BrowserHistorySummary(
        document_id=document.id,
        model="test-summary",
        prompt_version="v1",
        input_hash="f" * 64,
        status="queued",
        citations=[],
    )
    session.add(summary)
    await session.commit()

    for status in ("running", "ready", "error", "too_short", "queued"):
        summary.status = status
        await session.commit()

    summary.status = "generating"
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_finalized_schema_removes_legacy_page_content(session):
    """Protect the Alembic cutover from drifting back to the ORM-only schema."""

    def schema_state(sync_session):
        inspector = inspect(sync_session.connection())
        page_columns = {column["name"] for column in inspector.get_columns("browser_history_pages")}
        return page_columns, set(inspector.get_table_names())

    page_columns, tables = await session.run_sync(schema_state)
    assert {"text", "text_excerpt", "content_hash", "search_tsv"}.isdisjoint(page_columns)
    assert "browser_history_embeddings" not in tables
    assert "browser_history_embedding_usage" in tables
    assert "browser_history_gc_state" in tables


@pytest.mark.asyncio
async def test_account_deletion_enqueues_all_private_objects(session, users):
    user = await users.create()
    document_hash = "1" * 64
    image_hash = "2" * 64
    document_key = f"users/{user.id}/history/documents/sha256/11/{document_hash}"
    image_key = f"users/{user.id}/history/images/sha256/22/{image_hash}"
    session.add_all(
        [
            BrowserHistoryDocument(
                user_id=user.id,
                content_hash=document_hash,
                object_key=document_key,
                storage_status="ready",
                byte_size=100,
                character_count=80,
                extraction_version="history-dom-v2",
            ),
            BrowserHistoryImage(
                user_id=user.id,
                image_hash=image_hash,
                object_key=image_key,
                storage_status="ready",
                format="png",
                width=1,
                height=1,
                byte_size=50,
            ),
        ]
    )
    await session.commit()

    # Use SQL to exercise the database's account-cascade path directly.
    await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
    await session.commit()

    rows = list(
        await session.scalars(
            select(BrowserHistoryObjectDeletion).order_by(BrowserHistoryObjectDeletion.object_type)
        )
    )
    assert [(row.object_type, row.object_hash, row.object_key) for row in rows] == [
        ("document", document_hash, document_key),
        ("image", image_hash, image_key),
    ]
