from datetime import UTC, datetime, timedelta

import pytest

from app.history_sync import SyncRejection, persist_capture
from app.models import BrowserConnection, BrowserHistoryDeletion
from app.schemas import BrowserHistoryCaptureIn


async def _connection(users, session):
    user = await users.create()
    connection = BrowserConnection(
        user_id=user.id,
        name="Test Chrome",
        token_prefix="nrh_directtest",
        token_hash="a" * 64,
    )
    session.add(connection)
    await session.commit()
    await session.refresh(connection)
    return connection


def _capture(*, url="https://sub.example.com/page", known_revision=0, excerpt=""):
    now = datetime.now(UTC)
    return BrowserHistoryCaptureIn.model_validate(
        {
            "record_id": "direct",
            "url": url,
            "title": "Direct capture",
            "text": "Body text for excerpt fallback",
            "text_excerpt": excerpt,
            "first_visited_at": now - timedelta(hours=1),
            "last_visited_at": now,
            "captured_at": now,
            "visit_count": 1,
            "known_revision": known_revision,
        }
    )


@pytest.mark.parametrize(
    ("scope", "scope_key"),
    [
        ("domain", "example.com"),
        ("all", ""),
    ],
)
async def test_persist_capture_rejects_domain_and_all_tombstones(
    users,
    session,
    scope,
    scope_key,
):
    connection = await _connection(users, session)
    deletion = BrowserHistoryDeletion(
        user_id=connection.user_id,
        scope=scope,
        scope_key=scope_key,
        revision=3,
    )

    with pytest.raises(SyncRejection) as rejected:
        await persist_capture(
            session,
            connection,
            _capture(known_revision=2),
            rules=[],
            deletions=[deletion],
            now=datetime.now(UTC),
        )
    assert rejected.value.code == "stale_revision"


async def test_persist_capture_excludes_configured_newsread_host(
    users,
    session,
    monkeypatch,
):
    connection = await _connection(users, session)
    monkeypatch.setattr(
        "app.history_sync.settings.frontend_base_url",
        "https://news.example.com",
    )

    with pytest.raises(SyncRejection) as rejected:
        await persist_capture(
            session,
            connection,
            _capture(url="https://news.example.com/history"),
            rules=[],
            deletions=[],
            now=datetime.now(UTC),
        )
    assert rejected.value.code == "excluded"


async def test_persist_capture_derives_excerpt_from_text(users, session):
    connection = await _connection(users, session)
    page, _ = await persist_capture(
        session,
        connection,
        _capture(excerpt=""),
        rules=[],
        deletions=[],
        now=datetime.now(UTC),
    )
    await session.flush()

    assert page.text_excerpt == "Body text for excerpt fallback"
