import base64
import gzip
import hashlib
import io
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from PIL import Image
from sqlalchemy import func, select

from app import llm, qa_agent
from app.history_content import canonicalize_history_document_value
from app.history_crypto import MasterKeyring
from app.history_ingest import (
    HistoryIngestService,
    get_history_ingest_service,
    get_optional_history_ingest_service,
)
from app.history_storage import EncryptedHistoryStorage, HistoryKeyService, InMemoryObjectStore
from app.main import app
from app.models import (
    BrowserHistoryDocument,
    BrowserHistoryImage,
    BrowserHistoryPage,
    BrowserHistoryPageDocument,
    BrowserHistorySummary,
    Conversation,
    Message,
)


def _ingest_service():
    key = base64.b64encode(bytes(range(32))).decode()
    object_store = InMemoryObjectStore()
    storage = EncryptedHistoryStorage(
        object_store,
        HistoryKeyService(MasterKeyring.from_config(current_key=key, current_version=1)),
    )
    return HistoryIngestService(storage), object_store


@contextmanager
def _content_enabled(monkeypatch):
    ingest, object_store = _ingest_service()
    monkeypatch.setattr(
        "app.routers.history.settings.browser_history_content_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.routers.history.CONTENT_CAPABILITY_REVISION",
        2,
    )
    app.dependency_overrides[get_history_ingest_service] = lambda: ingest
    app.dependency_overrides[get_optional_history_ingest_service] = lambda: ingest
    try:
        yield ingest, object_store
    finally:
        app.dependency_overrides.pop(get_history_ingest_service, None)
        app.dependency_overrides.pop(get_optional_history_ingest_service, None)


async def _pair(client, users, user, name="Chrome"):
    response = await client.post(
        "/api/history/connections",
        json={"name": name},
        headers=users.auth(user),
    )
    assert response.status_code == 201
    return response.json()


def _document(text="A private article body with enough useful content."):
    return canonicalize_history_document_value(
        {
            "schema_version": 1,
            "extraction_version": "history-dom-v2",
            "content_type": "article",
            "language": "en",
            "blocks": [{"id": "b0001", "kind": "paragraph", "text": text}],
        }
    )


def _capture(record_id, url, content_hash=None, text=""):
    now = datetime.now(UTC)
    return {
        "record_id": record_id,
        "url": url,
        "title": "Captured article",
        "text": text,
        "text_excerpt": "",
        "first_visited_at": (now - timedelta(hours=1)).isoformat(),
        "last_visited_at": now.isoformat(),
        "captured_at": now.isoformat(),
        "visit_count": 1,
        "known_revision": 0,
        **({"content_hash": content_hash} if content_hash else {}),
    }


async def test_content_endpoints_stay_hidden_until_capability_is_enabled(
    client,
    users,
):
    user = await users.create()
    pairing = await _pair(client, users, user)
    response = await client.post(
        "/api/history/sync/content-status",
        json={"documents": ["a" * 64]},
        headers={"Authorization": f"Bearer {pairing['token']}"},
    )

    assert response.status_code == 404


async def test_content_status_and_uploads_share_extension_rate_limit(
    client,
    users,
    monkeypatch,
):
    user = await users.create()
    pairing = await _pair(client, users, user)
    headers = {"Authorization": f"Bearer {pairing['token']}"}
    document = _document()

    with _content_enabled(monkeypatch) as (_, object_store):
        monkeypatch.setattr(
            "app.routers.history.EXTENSION_RATE_LIMIT",
            1,
        )
        first = await client.post(
            "/api/history/sync/content-status",
            json={"documents": [document.content_hash], "images": []},
            headers=headers,
        )
        document_upload = await client.put(
            f"/api/history/sync/content/{document.content_hash}",
            content=document.canonical_bytes,
            headers={**headers, "Content-Type": "application/json"},
        )
        image_upload = await client.put(
            f"/api/history/sync/image/{'a' * 64}",
            content=b"not evaluated",
            headers={**headers, "Content-Type": "image/png"},
        )
        metadata_sync = await client.post(
            "/api/history/sync",
            json={
                "records": [
                    _capture(
                        "rate-limited",
                        "https://limited.example.com/article",
                    )
                ]
            },
            headers=headers,
        )

    assert first.status_code == 200
    for response in (document_upload, image_upload, metadata_sync):
        assert response.status_code == 429
        assert int(response.headers["retry-after"]) >= 1
    assert object_store.objects == {}


async def test_finalized_server_advertises_revision_three_consistently(
    client,
    users,
    monkeypatch,
):
    user = await users.create()
    with _content_enabled(monkeypatch):
        monkeypatch.setattr(
            "app.routers.history.settings.browser_history_finalize_enabled",
            True,
        )
        pairing = await _pair(client, users, user)
        headers = {"Authorization": f"Bearer {pairing['token']}"}
        sync = await client.post(
            "/api/history/sync",
            json={
                "records": [
                    _capture(
                        "finalized",
                        "https://finalized.example.com/article",
                    )
                ]
            },
            headers=headers,
        )
        status = await client.get("/api/history/sync/status", headers=headers)
        content_status = await client.post(
            "/api/history/sync/content-status",
            json={"documents": ["a" * 64], "images": []},
            headers=headers,
        )

    assert sync.status_code == 200
    assert status.status_code == 200
    assert content_status.status_code == 200
    assert sync.json()["content_capability_revision"] == 3
    assert status.json()["content_capability_revision"] == 3
    assert content_status.json()["content_capability_revision"] == 3


async def test_document_detail_and_content_are_owner_scoped_and_side_effect_free(
    client,
    users,
    session,
    monkeypatch,
):
    owner = await users.create()
    other = await users.create()
    pairing = await _pair(client, users, owner)
    document = _document("A private saved article body. " * 20)

    async def unexpected_llm(*args, **kwargs):
        raise AssertionError("detail reads must not resolve or call an LLM")

    monkeypatch.setattr(llm, "resolve_config", unexpected_llm)
    with _content_enabled(monkeypatch):
        extension_headers = {"Authorization": f"Bearer {pairing['token']}"}
        uploaded = await client.put(
            f"/api/history/sync/content/{document.content_hash}",
            content=document.canonical_bytes,
            headers={**extension_headers, "Content-Type": "application/json"},
        )
        document_id = uploaded.json()["document_id"]
        synced = await client.post(
            "/api/history/sync",
            json={
                "records": [
                    _capture(
                        "detail",
                        "https://detail.example.com/private",
                        document.content_hash,
                    )
                ]
            },
            headers=extension_headers,
        )
        assert synced.status_code == 200
        assert len(synced.json()["accepted"]) == 1, synced.json()

        detail = await client.get(
            f"/api/history/documents/{document_id}",
            headers=users.auth(owner),
        )
        content = await client.get(
            f"/api/history/documents/{document_id}/content",
            headers=users.auth(owner),
        )
        empty_qa = await client.get(
            f"/api/history/documents/{document_id}/qa",
            headers=users.auth(owner),
        )
        other_detail = await client.get(
            f"/api/history/documents/{document_id}",
            headers=users.auth(other),
        )

    assert detail.status_code == 200, detail.text
    assert detail.json()["document_id"] == document_id
    assert detail.json()["locations"][0]["url"] == "https://detail.example.com/private"
    assert detail.json()["summary_state"] == "not_requested"
    assert content.status_code == 200
    assert content.json()["blocks"][0]["id"] == "b0001"
    assert empty_qa.json() == []
    assert other_detail.status_code == 404
    assert await session.scalar(select(func.count()).select_from(BrowserHistorySummary)) == 0
    assert await session.scalar(select(func.count()).select_from(Conversation)) == 0


async def test_summary_request_is_lazy_idempotent_and_queued_once(
    client,
    users,
    session,
    monkeypatch,
):
    user = await users.create()
    pairing = await _pair(client, users, user)
    document = _document("Enough private content to summarize. " * 20)
    enqueued: list[tuple] = []

    async def configured(session, user_id):
        return llm.LLMConfig(
            provider="system",
            api_key="test",
            base_url=None,
            model="summary-model",
        )

    async def capture_enqueue(*args):
        enqueued.append(args)

    monkeypatch.setattr(llm, "resolve_config", configured)
    monkeypatch.setattr("app.routers.history.queue.enqueue", capture_enqueue)
    with _content_enabled(monkeypatch):
        extension_headers = {"Authorization": f"Bearer {pairing['token']}"}
        uploaded = await client.put(
            f"/api/history/sync/content/{document.content_hash}",
            content=document.canonical_bytes,
            headers={**extension_headers, "Content-Type": "application/json"},
        )
        document_id = uploaded.json()["document_id"]
        synced = await client.post(
            "/api/history/sync",
            json={
                "records": [
                    _capture(
                        "summary",
                        "https://summary.example.com/private",
                        document.content_hash,
                    )
                ]
            },
            headers=extension_headers,
        )
        assert len(synced.json()["accepted"]) == 1, synced.json()
        before = await client.get(
            f"/api/history/documents/{document_id}/summary",
            headers=users.auth(user),
        )
        first = await client.post(
            f"/api/history/documents/{document_id}/summarize",
            headers=users.auth(user),
        )
        second = await client.post(
            f"/api/history/documents/{document_id}/summarize",
            headers=users.auth(user),
        )

    assert before.status_code == 200, before.text
    assert before.json()["state"] == "not_requested"
    assert first.json()["state"] == second.json()["state"] == "queued"
    summary_jobs = [job for job in enqueued if job[0] == "generate_history_summary"]
    assert summary_jobs == [("generate_history_summary", 1)]
    assert await session.scalar(select(func.count()).select_from(BrowserHistorySummary)) == 1


async def test_history_qa_is_explicit_document_bound_and_metered(
    client,
    users,
    session,
    monkeypatch,
):
    user = await users.create()
    pairing = await _pair(client, users, user)
    document = _document("Stored evidence for private history Q and A. " * 20)
    usage_features: list[str] = []

    async def configured(session, user_id):
        return llm.LLMConfig(
            provider="system",
            api_key="test",
            base_url=None,
            model="qa-model",
        )

    async def answer(**kwargs):
        assert '"id":"b0001"' in kwargs["corpus"]
        yield {
            "type": "result",
            "content": "A grounded answer.",
            "tool_events": [],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        }

    async def record_usage(session, **kwargs):
        usage_features.append(kwargs["feature"])

    monkeypatch.setattr(llm, "resolve_config", configured)
    monkeypatch.setattr(llm, "record_usage", record_usage)
    monkeypatch.setattr(qa_agent, "stream_history_answer", answer)
    with _content_enabled(monkeypatch):
        extension_headers = {"Authorization": f"Bearer {pairing['token']}"}
        uploaded = await client.put(
            f"/api/history/sync/content/{document.content_hash}",
            content=document.canonical_bytes,
            headers={**extension_headers, "Content-Type": "application/json"},
        )
        document_id = uploaded.json()["document_id"]
        await client.post(
            "/api/history/sync",
            json={
                "records": [
                    _capture(
                        "qa",
                        "https://qa.example.com/private",
                        document.content_hash,
                    )
                ]
            },
            headers=extension_headers,
        )
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 0
        response = await client.post(
            f"/api/history/documents/{document_id}/qa/stream",
            json={"content": "What does the saved page say?"},
            headers=users.auth(user),
        )

    assert response.status_code == 200
    assert '"type": "done"' in response.text
    conversation = await session.scalar(select(Conversation))
    assert conversation.history_document_id == document_id
    assert await session.scalar(select(func.count()).select_from(Message)) == 2
    assert usage_features == ["history_qa"]


async def test_owner_scoped_content_status_upload_and_private_dedup(
    client,
    users,
    session,
    monkeypatch,
):
    owner = await users.create()
    other = await users.create()
    owner_pairing = await _pair(client, users, owner)
    other_pairing = await _pair(client, users, other)
    document = _document()

    with _content_enabled(monkeypatch) as (_, object_store):
        owner_headers = {"Authorization": f"Bearer {owner_pairing['token']}"}
        other_headers = {"Authorization": f"Bearer {other_pairing['token']}"}
        missing = await client.post(
            "/api/history/sync/content-status",
            json={"documents": [document.content_hash]},
            headers=owner_headers,
        )
        assert missing.status_code == 200
        assert missing.json()["documents"] == {document.content_hash: False}

        uploaded = await client.put(
            f"/api/history/sync/content/{document.content_hash}",
            content=gzip.compress(document.canonical_bytes),
            headers={
                **owner_headers,
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
            },
        )
        assert uploaded.status_code == 200
        uploaded_again = await client.put(
            f"/api/history/sync/content/{document.content_hash}",
            content=document.canonical_bytes,
            headers={**owner_headers, "Content-Type": "application/json"},
        )
        assert uploaded_again.json()["document_id"] == uploaded.json()["document_id"]
        assert len(object_store.objects) == 1

        owner_status = await client.post(
            "/api/history/sync/content-status",
            json={"documents": [document.content_hash]},
            headers=owner_headers,
        )
        other_status = await client.post(
            "/api/history/sync/content-status",
            json={"documents": [document.content_hash]},
            headers=other_headers,
        )
        assert owner_status.json()["documents"][document.content_hash] is True
        assert other_status.json()["documents"][document.content_hash] is False

        synced = await client.post(
            "/api/history/sync",
            json={
                "records": [
                    _capture(
                        "first",
                        "https://one.example.com/article",
                        document.content_hash,
                    ),
                    _capture(
                        "second",
                        "https://two.example.com/reprint",
                        document.content_hash,
                    ),
                ]
            },
            headers=owner_headers,
        )
        assert synced.status_code == 200
        assert len(synced.json()["accepted"]) == 2, synced.json()

    assert await session.scalar(select(func.count()).select_from(BrowserHistoryDocument)) == 1
    assert await session.scalar(select(func.count()).select_from(BrowserHistoryPage)) == 2
    assert await session.scalar(select(func.count()).select_from(BrowserHistoryPageDocument)) == 2
    pages = (await session.scalars(select(BrowserHistoryPage))).all()
    assert all(page.current_document_id is not None for page in pages)
    stored_document = await session.scalar(select(BrowserHistoryDocument))
    assert stored_document.search_tsv is not None


async def test_document_upload_enforces_owner_storage_quota(
    client,
    users,
    monkeypatch,
):
    user = await users.create()
    pairing = await _pair(client, users, user)
    document = _document("This content is larger than the configured quota. " * 10)
    monkeypatch.setattr(
        "app.history_ingest.settings.history_user_storage_max_bytes",
        16,
    )

    with _content_enabled(monkeypatch) as (_, object_store):
        response = await client.put(
            f"/api/history/sync/content/{document.content_hash}",
            content=document.canonical_bytes,
            headers={
                "Authorization": f"Bearer {pairing['token']}",
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == 413
    assert response.json()["detail"] == "browser history storage quota exceeded"
    assert object_store.objects == {}


async def test_history_operations_are_owner_scoped(
    client,
    users,
    monkeypatch,
):
    owner = await users.create()
    other = await users.create()
    pairing = await _pair(client, users, owner)
    document = _document("Owner-private content for operational metrics. " * 10)

    with _content_enabled(monkeypatch):
        extension_headers = {"Authorization": f"Bearer {pairing['token']}"}
        assert (
            await client.put(
                f"/api/history/sync/content/{document.content_hash}",
                content=document.canonical_bytes,
                headers={
                    **extension_headers,
                    "Content-Type": "application/json",
                },
            )
        ).status_code == 200
        assert (
            await client.post(
                "/api/history/sync",
                json={
                    "records": [
                        _capture(
                            "metrics",
                            "https://metrics.example.com/article",
                            document.content_hash,
                        )
                    ]
                },
                headers=extension_headers,
            )
        ).status_code == 200

    owner_metrics = await client.get(
        "/api/history/operations",
        headers=users.auth(owner),
    )
    other_metrics = await client.get(
        "/api/history/operations",
        headers=users.auth(other),
    )
    assert owner_metrics.status_code == 200
    assert owner_metrics.json()["storage_used_bytes"] == len(document.canonical_bytes)
    assert owner_metrics.json()["document_count"] == 1
    assert owner_metrics.json()["embedding_backlog_count"] == 1
    assert other_metrics.status_code == 200
    assert other_metrics.json()["storage_used_bytes"] == 0
    assert other_metrics.json()["document_count"] == 0
    assert other_metrics.json()["embedding_backlog_count"] == 0


async def test_content_hash_mismatch_and_cross_owner_attach_fail_safely(
    client,
    users,
    session,
    monkeypatch,
):
    owner = await users.create()
    other = await users.create()
    owner_pairing = await _pair(client, users, owner)
    other_pairing = await _pair(client, users, other)
    document = _document()

    with _content_enabled(monkeypatch):
        owner_headers = {"Authorization": f"Bearer {owner_pairing['token']}"}
        mismatch = await client.put(
            f"/api/history/sync/content/{'a' * 64}",
            content=document.canonical_bytes,
            headers={**owner_headers, "Content-Type": "application/json"},
        )
        assert mismatch.status_code == 422

        uploaded = await client.put(
            f"/api/history/sync/content/{document.content_hash}",
            content=document.canonical_bytes,
            headers={**owner_headers, "Content-Type": "application/json"},
        )
        assert uploaded.status_code == 200
        attached = await client.post(
            "/api/history/sync",
            json={
                "records": [
                    _capture(
                        "cross-owner",
                        "https://other.example.com/private",
                        document.content_hash,
                    )
                ]
            },
            headers={"Authorization": f"Bearer {other_pairing['token']}"},
        )
        assert attached.status_code == 200
        assert attached.json()["rejected"][0]["code"] == "content_missing", attached.json()
        other_upload = await client.put(
            f"/api/history/sync/content/{document.content_hash}",
            content=document.canonical_bytes,
            headers={
                "Authorization": f"Bearer {other_pairing['token']}",
                "Content-Type": "application/json",
            },
        )
        assert other_upload.status_code == 200

    documents = (await session.scalars(select(BrowserHistoryDocument))).all()
    assert len(documents) == 2
    assert {document.user_id for document in documents} == {owner.id, other.id}


async def test_content_upload_rejects_unsupported_encoding_and_gzip_bombs(
    client,
    users,
    monkeypatch,
):
    user = await users.create()
    pairing = await _pair(client, users, user)
    headers = {"Authorization": f"Bearer {pairing['token']}"}

    with _content_enabled(monkeypatch):
        unsupported = await client.put(
            f"/api/history/sync/content/{'a' * 64}",
            content=b"payload",
            headers={**headers, "Content-Encoding": "br"},
        )
        assert unsupported.status_code == 415

        monkeypatch.setattr(
            "app.routers.history.settings.history_object_max_bytes",
            100,
        )
        bomb = await client.put(
            f"/api/history/sync/content/{'a' * 64}",
            content=gzip.compress(b"x" * 10_000),
            headers={**headers, "Content-Encoding": "gzip"},
        )
        assert bomb.status_code == 422
        assert "size limit" in bomb.json()["detail"]


async def test_same_url_versions_are_linked_once_and_out_of_order_sync_keeps_newest(
    client,
    users,
    session,
    monkeypatch,
):
    user = await users.create()
    pairing = await _pair(client, users, user)
    headers = {"Authorization": f"Bearer {pairing['token']}"}
    older = _document("Older version of the private article.")
    newer = _document("Newer version of the private article.")
    now = datetime.now(UTC)

    with _content_enabled(monkeypatch):
        for document in (older, newer):
            response = await client.put(
                f"/api/history/sync/content/{document.content_hash}",
                content=document.canonical_bytes,
                headers={**headers, "Content-Type": "application/json"},
            )
            assert response.status_code == 200

        newer_capture = _capture(
            "newer",
            "https://versions.example.com/article",
            newer.content_hash,
        )
        newer_capture["captured_at"] = now.isoformat()
        older_capture = _capture(
            "older",
            "https://versions.example.com/article",
            older.content_hash,
        )
        older_capture["captured_at"] = (now - timedelta(days=1)).isoformat()
        assert (
            await client.post(
                "/api/history/sync",
                json={"records": [newer_capture]},
                headers=headers,
            )
        ).status_code == 200
        assert (
            await client.post(
                "/api/history/sync",
                json={"records": [older_capture, {**newer_capture, "record_id": "retry"}]},
                headers=headers,
            )
        ).status_code == 200

    page = await session.scalar(select(BrowserHistoryPage))
    current = await session.get(BrowserHistoryDocument, page.current_document_id)
    assert current.content_hash == newer.content_hash
    assert await session.scalar(select(func.count()).select_from(BrowserHistoryPageDocument)) == 2


async def test_first_document_link_enqueues_embedding_once_for_duplicate_locations(
    client,
    users,
    session,
    monkeypatch,
):
    user = await users.create()
    pairing = await _pair(client, users, user)
    headers = {"Authorization": f"Bearer {pairing['token']}"}
    document = _document("Shared article text for two canonical locations.")
    enqueued: list[tuple[str, int]] = []

    async def record(job_name, document_id):
        enqueued.append((job_name, document_id))

    monkeypatch.setattr("app.queue.enqueue", record)
    with _content_enabled(monkeypatch):
        upload = await client.put(
            f"/api/history/sync/content/{document.content_hash}",
            content=document.canonical_bytes,
            headers={**headers, "Content-Type": "application/json"},
        )
        document_id = upload.json()["document_id"]
        response = await client.post(
            "/api/history/sync",
            json={
                "records": [
                    _capture(
                        "first-location",
                        "https://example.com/article",
                        document.content_hash,
                    ),
                    _capture(
                        "second-location",
                        "https://example.net/syndicated",
                        document.content_hash,
                    ),
                ]
            },
            headers=headers,
        )
        assert response.status_code == 200
        assert enqueued == [("embed_history_document", document_id)]

        await client.post(
            "/api/history/sync",
            json={
                "records": [
                    _capture(
                        "repeat-location",
                        "https://example.com/article",
                        document.content_hash,
                    )
                ]
            },
            headers=headers,
        )

    assert enqueued == [("embed_history_document", document_id)]
    assert await session.scalar(select(func.count()).select_from(BrowserHistoryPageDocument)) == 2


async def test_history_search_returns_one_document_with_all_locations_and_page_fallback(
    client,
    users,
    monkeypatch,
):
    user = await users.create()
    pairing = await _pair(client, users, user)
    auth = users.auth(user)
    headers = {"Authorization": f"Bearer {pairing['token']}"}
    document = _document("Quasiparticle retrieval appears only in the captured body.")

    with _content_enabled(monkeypatch):
        await client.put(
            f"/api/history/sync/content/{document.content_hash}",
            content=document.canonical_bytes,
            headers={**headers, "Content-Type": "application/json"},
        )
        metadata = _capture(
            "metadata",
            "https://metadata.example.org/home",
        )
        metadata["title"] = "Metadata control panel"
        response = await client.post(
            "/api/history/sync",
            json={
                "records": [
                    _capture(
                        "canonical",
                        "https://example.com/article",
                        document.content_hash,
                    ),
                    _capture(
                        "syndicated",
                        "https://example.net/copy",
                        document.content_hash,
                    ),
                    metadata,
                ]
            },
            headers=headers,
        )
        assert response.status_code == 200

    body_search = await client.get(
        "/api/history",
        params={"q": "quasiparticle retrieval", "sort": "relevance"},
        headers=auth,
    )
    assert body_search.status_code == 200
    assert len(body_search.json()) == 1
    result = body_search.json()[0]
    assert result["type"] == "document"
    assert result["text_excerpt"].startswith("Quasiparticle retrieval")
    assert {location["hostname"] for location in result["locations"]} == {
        "example.com",
        "example.net",
    }

    metadata_search = await client.get(
        "/api/history",
        params={"q": "control panel", "sort": "relevance"},
        headers=auth,
    )
    assert metadata_search.json()[0]["type"] == "page"
    assert metadata_search.json()[0]["hostname"] == "metadata.example.org"


async def test_image_upload_is_verified_and_owner_scoped(
    client,
    users,
    session,
    monkeypatch,
):
    user = await users.create()
    pairing = await _pair(client, users, user)
    headers = {"Authorization": f"Bearer {pairing['token']}"}
    output = io.BytesIO()
    Image.new("RGB", (24, 24), "green").save(output, format="PNG")
    payload = output.getvalue()
    image_hash = hashlib.sha256(payload).hexdigest()

    with _content_enabled(monkeypatch):
        response = await client.put(
            f"/api/history/sync/image/{image_hash}",
            content=payload,
            headers={**headers, "Content-Type": "image/png"},
        )
        assert response.status_code == 200
        status = await client.post(
            "/api/history/sync/content-status",
            json={"images": [image_hash]},
            headers=headers,
        )
        assert status.json()["images"] == {image_hash: True}

    image = await session.scalar(select(BrowserHistoryImage))
    assert image.format == "png"
    assert (image.width, image.height) == (24, 24)


async def test_one_image_can_be_both_document_lead_and_page_favicon(
    client,
    users,
    session,
    monkeypatch,
):
    user = await users.create()
    pairing = await _pair(client, users, user)
    headers = {"Authorization": f"Bearer {pairing['token']}"}
    document = _document()
    output = io.BytesIO()
    Image.new("RGB", (24, 24), "blue").save(output, format="PNG")
    payload = output.getvalue()
    image_hash = hashlib.sha256(payload).hexdigest()

    with _content_enabled(monkeypatch):
        assert (
            await client.put(
                f"/api/history/sync/content/{document.content_hash}",
                content=document.canonical_bytes,
                headers={**headers, "Content-Type": "application/json"},
            )
        ).status_code == 200
        assert (
            await client.put(
                f"/api/history/sync/image/{image_hash}",
                content=payload,
                headers={**headers, "Content-Type": "image/png"},
            )
        ).status_code == 200
        capture = _capture(
            "with-images",
            "https://images.example.com/article",
            document.content_hash,
        )
        capture["lead_image_hash"] = image_hash
        capture["favicon_image_hash"] = image_hash
        response = await client.post(
            "/api/history/sync",
            json={"records": [capture]},
            headers=headers,
        )
        assert response.json()["accepted"][0]["record_id"] == "with-images"

    page = await session.scalar(select(BrowserHistoryPage))
    document_row = await session.scalar(select(BrowserHistoryDocument))
    image = await session.scalar(select(BrowserHistoryImage))
    assert page.favicon_image_id == image.id
    assert document_row.lead_image_id == image.id
    assert image.source_host == "images.example.com"


async def test_legacy_inline_capture_becomes_encrypted_document_not_page_text(
    client,
    users,
    session,
    monkeypatch,
):
    user = await users.create()
    pairing = await _pair(client, users, user)

    with _content_enabled(monkeypatch):
        response = await client.post(
            "/api/history/sync",
            json={
                "records": [
                    _capture(
                        "legacy",
                        "https://legacy.example.com/article",
                        text="Legacy extension body",
                    )
                ]
            },
            headers={"Authorization": f"Bearer {pairing['token']}"},
        )
        assert response.status_code == 200
        assert response.json()["accepted"][0]["record_id"] == "legacy", response.json()

    page = await session.scalar(select(BrowserHistoryPage))
    document = await session.scalar(select(BrowserHistoryDocument))
    assert page.current_document_id == document.id
    assert document.extraction_version == "history-inline-v1"


async def test_legacy_inline_capture_becomes_metadata_only_after_compatibility_window(
    client,
    users,
    session,
    monkeypatch,
):
    user = await users.create()
    pairing = await _pair(client, users, user)

    with _content_enabled(monkeypatch):
        monkeypatch.setattr(
            "app.routers.history.settings.browser_history_finalize_enabled",
            True,
        )
        response = await client.post(
            "/api/history/sync",
            json={
                "records": [
                    _capture(
                        "expired-legacy",
                        "https://legacy.example.com/article",
                        text="Legacy extension body",
                    )
                ]
            },
            headers={"Authorization": f"Bearer {pairing['token']}"},
        )
        assert response.status_code == 200
        assert response.json()["accepted"][0]["record_id"] == "expired-legacy"

    page = await session.scalar(select(BrowserHistoryPage))
    assert page.current_document_id is None
    assert await session.scalar(select(BrowserHistoryDocument.id)) is None


async def test_search_locations_honor_active_hostname_filter(
    client,
    users,
    session,
    monkeypatch,
):
    """A hostname-filtered search must not surface out-of-filter locations,
    or row actions would target pages outside the filtered view."""
    user = await users.create()
    pairing = await _pair(client, users, user)
    headers = {"Authorization": f"Bearer {pairing['token']}"}
    document = _document("Shared filtered article body about database indexing.")

    with _content_enabled(monkeypatch):
        response = await client.put(
            f"/api/history/sync/content/{document.content_hash}",
            content=document.canonical_bytes,
            headers={**headers, "Content-Type": "application/json"},
        )
        assert response.status_code == 200
        sync = await client.post(
            "/api/history/sync",
            json={
                "records": [
                    _capture(
                        "alpha",
                        "https://alpha.example.com/article",
                        document.content_hash,
                    ),
                    _capture(
                        "beta",
                        "https://beta.example.net/article",
                        document.content_hash,
                    ),
                ]
            },
            headers=headers,
        )
        assert sync.status_code == 200

        unfiltered = await client.get(
            "/api/history",
            params={"q": "database indexing", "sort": "relevance"},
            headers=users.auth(user),
        )
        assert unfiltered.status_code == 200
        [result] = unfiltered.json()
        assert result["type"] == "document"
        assert {location["hostname"] for location in result["locations"]} == {
            "alpha.example.com",
            "beta.example.net",
        }

        filtered = await client.get(
            "/api/history",
            params={
                "q": "database indexing",
                "sort": "relevance",
                "hostname": "alpha.example.com",
            },
            headers=users.auth(user),
        )
        assert filtered.status_code == 200
        [result] = filtered.json()
        assert result["type"] == "document"
        assert [location["hostname"] for location in result["locations"]] == ["alpha.example.com"]
