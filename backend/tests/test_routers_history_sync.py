from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.history_policy import MAX_HISTORY_VISIT_COUNT, validate_normalized_history_url
from app.models import (
    BrowserHistoryDeletion,
    BrowserHistoryPage,
    BrowserHistoryPageConnection,
    BrowserHistorySettings,
)


async def _pair(client, users, user, name="Chrome"):
    response = await client.post(
        "/api/history/connections",
        json={"name": name},
        headers=users.auth(user),
    )
    assert response.status_code == 201
    return response.json()


def _capture(
    record_id="capture-1",
    *,
    url="https://example.com/article",
    title="Example",
    text="Captured text",
    text_excerpt="Captured text",
    first=None,
    last=None,
    captured=None,
    visit_count=1,
    known_revision=0,
):
    now = datetime.now(UTC)
    first = first or now - timedelta(hours=1)
    last = last or now - timedelta(minutes=1)
    captured = captured or last
    return {
        "record_id": record_id,
        "url": url,
        "title": title,
        "text": text,
        "text_excerpt": text_excerpt,
        "first_visited_at": first.isoformat(),
        "last_visited_at": last.isoformat(),
        "captured_at": captured.isoformat(),
        "visit_count": visit_count,
        "known_revision": known_revision,
    }


async def _sync(client, token, records):
    return await client.post(
        "/api/history/sync",
        json={"records": records},
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_sync_stores_sanitized_capture_and_clamps_future_timestamps(client, users, session):
    user = await users.create()
    pairing = await _pair(client, users, user)
    future = datetime.now(UTC) + timedelta(days=10)
    response = await _sync(
        client,
        pairing["token"],
        [
            _capture(
                title="A\u202e <script>alert(1)</script>",
                text="hello\u200b\nworld",
                text_excerpt="hello\u202e world",
                first=future,
                last=future + timedelta(hours=1),
                captured=future + timedelta(hours=1),
                visit_count=MAX_HISTORY_VISIT_COUNT + 500,
            )
        ],
    )

    assert response.status_code == 200
    assert len(response.json()["accepted"]) == 1
    page = await session.scalar(select(BrowserHistoryPage))
    assert page.title == "A <script>alert(1)</script>"
    assert page.text == "hello world"
    assert page.text_excerpt == "hello world"
    assert page.visit_count == MAX_HISTORY_VISIT_COUNT
    assert page.last_visited_at <= datetime.now(UTC)
    assert len(page.content_hash) == 64


async def test_sync_returns_per_item_validation_errors_without_losing_valid_items(
    client, users, session
):
    user = await users.create()
    pairing = await _pair(client, users, user)
    response = await _sync(
        client,
        pairing["token"],
        [
            _capture("valid"),
            _capture("private", url="http://127.0.0.1/"),
            _capture("secret", url="https://example.com/?token=secret"),
            _capture("scheme", url="javascript:alert(1)"),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["record_id"] for item in body["accepted"]] == ["valid"]
    assert {item["record_id"] for item in body["rejected"]} == {
        "private",
        "secret",
        "scheme",
    }
    assert {item["code"] for item in body["rejected"]} == {"invalid"}
    assert await session.scalar(select(func.count()).select_from(BrowserHistoryPage)) == 1


async def test_sync_retry_out_of_order_and_counter_regression_are_idempotent(
    client, users, session
):
    user = await users.create()
    pairing = await _pair(client, users, user)
    base = datetime.now(UTC) - timedelta(days=2)
    newest = _capture(
        first=base + timedelta(hours=2),
        last=base + timedelta(hours=5),
        captured=base + timedelta(hours=5),
        visit_count=5,
        text="new content",
    )
    assert (await _sync(client, pairing["token"], [newest])).status_code == 200
    assert (await _sync(client, pairing["token"], [newest])).status_code == 200

    older = _capture(
        "older",
        first=base,
        last=base + timedelta(hours=3),
        captured=base + timedelta(hours=3),
        visit_count=2,
        text="stale content",
    )
    assert (await _sync(client, pairing["token"], [older])).status_code == 200

    page = await session.scalar(select(BrowserHistoryPage))
    aggregate = await session.scalar(select(BrowserHistoryPageConnection))
    assert page.visit_count == aggregate.visit_count == 5
    assert page.first_visited_at == base
    assert page.last_visited_at == base + timedelta(hours=5)
    assert page.text == "new content"


async def test_two_connections_contribute_absolute_counts(client, users, session):
    user = await users.create()
    first = await _pair(client, users, user, "Chrome")
    second = await _pair(client, users, user, "Chromium")
    assert (
        await _sync(client, first["token"], [_capture("first", visit_count=2)])
    ).status_code == 200
    assert (
        await _sync(client, second["token"], [_capture("second", visit_count=3)])
    ).status_code == 200

    page = await session.scalar(select(BrowserHistoryPage))
    assert page.visit_count == 5
    assert await session.scalar(select(func.count()).select_from(BrowserHistoryPageConnection)) == 2


async def test_sync_enforces_exclude_and_metadata_only_domain_rules(client, users, session):
    user = await users.create()
    pairing = await _pair(client, users, user)
    headers = users.auth(user)
    await client.post(
        "/api/history/domain-rules",
        json={
            "hostname": "private.example.com",
            "match_subdomains": True,
            "mode": "exclude",
        },
        headers=headers,
    )
    await client.post(
        "/api/history/domain-rules",
        json={"hostname": "mail.example.com", "mode": "metadata_only"},
        headers=headers,
    )

    response = await _sync(
        client,
        pairing["token"],
        [
            _capture("excluded", url="https://sub.private.example.com/page"),
            _capture("metadata", url="https://mail.example.com/inbox", text="secret body"),
        ],
    )
    body = response.json()
    assert body["sync_revision"] == 2
    assert body["rejected"][0]["code"] == "excluded"
    assert body["accepted"][0]["record_id"] == "metadata"
    page = await session.scalar(select(BrowserHistoryPage))
    assert page.hostname == "mail.example.com"
    assert page.text == ""


async def test_deletion_revision_rejects_stale_queue_but_allows_acknowledged_revisit(
    client, users, session
):
    user = await users.create()
    pairing = await _pair(client, users, user)
    normalized = validate_normalized_history_url("https://example.com/article")
    history_settings = await session.get(BrowserHistorySettings, user.id)
    history_settings.sync_revision = 4
    session.add(
        BrowserHistoryDeletion(
            user_id=user.id,
            scope="page",
            scope_key=normalized.url_hash,
            revision=4,
        )
    )
    await session.commit()

    stale = await _sync(
        client,
        pairing["token"],
        [_capture("stale", known_revision=3)],
    )
    assert stale.json()["rejected"][0]["code"] == "stale_revision"
    assert await session.scalar(select(BrowserHistoryPage.id)) is None

    current = await _sync(
        client,
        pairing["token"],
        [_capture("current", known_revision=4)],
    )
    assert current.json()["accepted"][0]["record_id"] == "current"
    assert await session.scalar(select(BrowserHistoryPage.id)) is not None


async def test_sync_rejects_oversized_body_and_revoked_token(client, users):
    user = await users.create()
    pairing = await _pair(client, users, user)
    oversized = _capture()
    oversized["padding"] = "x" * (1024 * 1024)
    response = await _sync(client, pairing["token"], [oversized])
    assert response.status_code == 413

    await client.delete(
        f"/api/history/connections/{pairing['id']}",
        headers=users.auth(user),
    )
    rejected = await _sync(client, pairing["token"], [_capture()])
    assert rejected.status_code == 401


async def test_content_length_rejects_oversized_invalid_json_before_parsing(client, users):
    user = await users.create()
    pairing = await _pair(client, users, user)
    response = await client.post(
        "/api/history/sync",
        content=b"{" + b"x" * (1024 * 1024),
        headers={
            "Authorization": f"Bearer {pairing['token']}",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 413
