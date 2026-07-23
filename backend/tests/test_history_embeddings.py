from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import history_embeddings, worker
from app.config import settings
from app.models import (
    BrowserHistoryEmbedding,
    BrowserHistoryPage,
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
    session.add(page)
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
