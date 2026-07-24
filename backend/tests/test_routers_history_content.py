import base64
import gzip
import hashlib
import io
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from PIL import Image
from sqlalchemy import func, select

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
    assert all(page.text == "" for page in pages)
    assert all(page.content_hash is None for page in pages)
    assert all(page.current_document_id is not None for page in pages)
    stored_document = await session.scalar(select(BrowserHistoryDocument))
    assert stored_document.search_tsv is not None


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
    assert page.text == ""
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
            "app.routers.history.settings.browser_history_legacy_inline_enabled",
            False,
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
    assert page.text == ""
    assert page.current_document_id is None
    assert await session.scalar(select(BrowserHistoryDocument.id)) is None
