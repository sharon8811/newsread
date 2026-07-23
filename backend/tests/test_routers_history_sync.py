from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app import embeddings, history_embeddings
from app.config import settings
from app.history_policy import MAX_HISTORY_VISIT_COUNT, validate_normalized_history_url
from app.models import (
    BrowserHistoryDeletion,
    BrowserHistoryEmbedding,
    BrowserHistoryPage,
    BrowserHistoryPageConnection,
    BrowserHistorySettings,
)
from app.routers import history as history_router


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


async def test_history_summary_list_search_filters_and_sources(client, users):
    user = await users.create()
    first = await _pair(client, users, user, "Chrome")
    second = await _pair(client, users, user, "Chromium")
    alpha = _capture(
        "alpha",
        url="https://news.example.com/alpha",
        title="Alpha 100% guide",
        text="A practical guide to local models",
        visit_count=2,
    )
    beta = _capture(
        "beta",
        url="https://other.example.net/beta",
        title="Beta_notes",
        text="Different subject",
        visit_count=1,
    )
    await _sync(client, first["token"], [alpha, beta])
    await _sync(client, second["token"], [{**alpha, "visit_count": 3}])
    headers = users.auth(user)

    summary = (await client.get("/api/history/summary", headers=headers)).json()
    assert summary == {
        "active_connection_count": 2,
        "total_connection_count": 2,
        "history_count": 2,
        "has_active_connection": True,
        "has_history": True,
    }

    percent = await client.get(
        "/api/history",
        params={"q": "100%", "sort": "relevance"},
        headers=headers,
    )
    assert [page["title"] for page in percent.json()] == ["Alpha 100% guide"]
    assert percent.json()[0]["visit_count"] == 5
    assert percent.json()[0]["source_browsers"] == ["Chrome", "Chromium"]

    underscore = await client.get(
        "/api/history",
        params={"q": "_"},
        headers=headers,
    )
    assert [page["title"] for page in underscore.json()] == ["Beta_notes"]

    domain = await client.get(
        "/api/history",
        params={"hostname": "example.com"},
        headers=headers,
    )
    assert [page["hostname"] for page in domain.json()] == ["news.example.com"]

    future = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    empty = await client.get(
        "/api/history",
        params={"date_from": future},
        headers=headers,
    )
    assert empty.json() == []


async def test_history_search_uses_tsvector_and_current_model_vectors(
    client,
    users,
    session,
    monkeypatch,
):
    alice = await users.create(username="alice")
    bob = await users.create(username="bob")
    alice_pairing = await _pair(client, users, alice)
    bob_pairing = await _pair(client, users, bob)
    first = _capture(
        "first",
        url="https://alpha.example.com/page",
        title="Database indexing notes",
        text="PostgreSQL weighted document retrieval",
    )
    second = _capture(
        "second",
        url="https://beta.example.com/page",
        title="Rendering notes",
        text="Component update lifecycle",
    )
    alice_sync = await _sync(client, alice_pairing["token"], [first, second])
    bob_sync = await _sync(
        client,
        bob_pairing["token"],
        [_capture("bob", url="https://private.example.net/page")],
    )
    first_id, second_id = [item["page_id"] for item in alice_sync.json()["accepted"]]
    bob_id = bob_sync.json()["accepted"][0]["page_id"]
    session.add_all(
        [
            BrowserHistoryEmbedding(
                page_id=first_id,
                model=settings.openai_embedding_model,
                embedding=[1.0, 0.0],
                input_hash="first",
            ),
            BrowserHistoryEmbedding(
                page_id=second_id,
                model=settings.openai_embedding_model,
                embedding=[0.0, 1.0],
                input_hash="second",
            ),
            BrowserHistoryEmbedding(
                page_id=bob_id,
                model=settings.openai_embedding_model,
                embedding=[1.0, 0.0],
                input_hash="bob",
            ),
        ]
    )
    await session.commit()

    keyword = await client.get(
        "/api/history",
        params={"q": "weighted retrieval", "sort": "relevance"},
        headers=users.auth(alice),
    )
    assert [page["id"] for page in keyword.json()] == [first_id]

    async def fake_embed_query(query):
        assert query in {"concept without keywords", "component lifecycle"}
        return [1.0, 0.0]

    monkeypatch.setattr(history_embeddings, "is_configured", lambda: True)
    monkeypatch.setattr(embeddings, "embed_query", fake_embed_query)
    semantic = await client.get(
        "/api/history",
        params={"q": "concept without keywords", "sort": "relevance"},
        headers=users.auth(alice),
    )
    assert [page["id"] for page in semantic.json()] == [first_id, second_id]
    assert bob_id not in {page["id"] for page in semantic.json()}

    hybrid = await client.get(
        "/api/history",
        params={"q": "component lifecycle", "sort": "relevance"},
        headers=users.auth(alice),
    )
    assert [page["id"] for page in hybrid.json()] == [second_id, first_id]


async def test_history_recent_and_ranked_cursor_pagination(client, users):
    user = await users.create()
    pairing = await _pair(client, users, user)
    now = datetime.now(UTC)
    records = [
        _capture(
            f"page-{index}",
            url=f"https://page{index}.example.com/item",
            title=f"Cursor result {index}",
            text="cursor pagination",
            first=now - timedelta(days=index + 1),
            last=now - timedelta(hours=index),
            captured=now - timedelta(hours=index),
        )
        for index in range(3)
    ]
    await _sync(client, pairing["token"], records)
    headers = users.auth(user)

    first = await client.get(
        "/api/history",
        params={"limit": 1},
        headers=headers,
    )
    first_cursor = first.headers["x-next-cursor"]
    second = await client.get(
        "/api/history",
        params={"limit": 1, "cursor": first_cursor},
        headers=headers,
    )
    assert first.json()[0]["id"] != second.json()[0]["id"]

    ranked = await client.get(
        "/api/history",
        params={"q": "cursor pagination", "sort": "relevance", "limit": 1},
        headers=headers,
    )
    ranked_cursor = ranked.headers["x-next-cursor"]
    ranked_next = await client.get(
        "/api/history",
        params={
            "q": "cursor pagination",
            "sort": "relevance",
            "limit": 1,
            "cursor": ranked_cursor,
        },
        headers=headers,
    )
    assert ranked.json()[0]["id"] != ranked_next.json()[0]["id"]

    mismatched = await client.get(
        "/api/history",
        params={"hostname": "example.com", "cursor": first_cursor},
        headers=headers,
    )
    assert mismatched.status_code == 422


async def test_sync_rate_limit_returns_retry_after(client, users, monkeypatch):
    user = await users.create()
    pairing = await _pair(client, users, user)
    monkeypatch.setattr(history_router, "SYNC_RATE_LIMIT", 1)
    assert (await _sync(client, pairing["token"], [_capture("first")])).status_code == 200
    limited = await _sync(client, pairing["token"], [_capture("second")])
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1


async def test_delete_page_is_owner_scoped_and_writes_tombstone(client, users, session):
    alice = await users.create(username="alice")
    bob = await users.create(username="bob")
    pairing = await _pair(client, users, alice)
    page_id = (await _sync(client, pairing["token"], [_capture()])).json()["accepted"][0]["page_id"]

    denied = await client.delete(
        f"/api/history/{page_id}",
        headers=users.auth(bob),
    )
    assert denied.status_code == 404
    deleted = await client.delete(
        f"/api/history/{page_id}",
        headers=users.auth(alice),
    )
    assert deleted.status_code == 204
    tombstone = await session.scalar(select(BrowserHistoryDeletion))
    assert tombstone.scope == "page"
    assert tombstone.revision == 1
    assert await session.get(BrowserHistoryPage, page_id) is None


async def test_clear_history_supports_domain_then_all(client, users, session):
    user = await users.create()
    pairing = await _pair(client, users, user)
    await _sync(
        client,
        pairing["token"],
        [
            _capture("domain", url="https://sub.example.com/page"),
            _capture("other", url="https://other.example.net/page"),
        ],
    )
    headers = users.auth(user)

    domain = await client.request(
        "DELETE",
        "/api/history",
        json={"confirm": "DELETE", "hostname": "example.com"},
        headers=headers,
    )
    assert domain.json() == {"deleted_count": 1, "sync_revision": 1}
    remaining = list(await session.scalars(select(BrowserHistoryPage)))
    assert [page.hostname for page in remaining] == ["other.example.net"]

    all_history = await client.request(
        "DELETE",
        "/api/history",
        json={"confirm": "DELETE"},
        headers=headers,
    )
    assert all_history.json() == {"deleted_count": 1, "sync_revision": 2}
    assert list(await session.scalars(select(BrowserHistoryPage))) == []
    tombstones = list(
        await session.scalars(
            select(BrowserHistoryDeletion).order_by(BrowserHistoryDeletion.revision)
        )
    )
    assert [(row.scope, row.scope_key) for row in tombstones] == [
        ("domain", "example.com"),
        ("all", ""),
    ]


async def test_exclude_rule_can_delete_existing_domain_history(client, users, session):
    user = await users.create()
    pairing = await _pair(client, users, user)
    await _sync(
        client,
        pairing["token"],
        [_capture(url="https://mail.example.com/inbox", text="private")],
    )

    response = await client.post(
        "/api/history/domain-rules",
        json={
            "hostname": "example.com",
            "match_subdomains": True,
            "mode": "exclude",
            "delete_existing": True,
        },
        headers=users.auth(user),
    )
    assert response.status_code == 201
    assert await session.scalar(select(BrowserHistoryPage.id)) is None
    tombstone = await session.scalar(select(BrowserHistoryDeletion))
    assert (tombstone.scope, tombstone.scope_key, tombstone.revision) == (
        "domain",
        "example.com",
        1,
    )
