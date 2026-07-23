"""Browser-history connection, policy, and scoped-token foundation."""

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select

from app.config import settings
from app.models import (
    BrowserConnection,
    BrowserHistoryDomainRule,
    BrowserHistorySettings,
)
from app.routers import history as history_router


async def _create_connection(client, users, user, name="Chrome on MacBook"):
    return await client.post(
        "/api/history/connections",
        json={"name": name},
        headers=users.auth(user),
    )


async def test_create_connection_returns_token_once_and_persists_only_hash(client, users, session):
    user = await users.create()
    response = await _create_connection(client, users, user, "  Chrome   on MacBook  ")

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["name"] == "Chrome on MacBook"
    assert body["token"].startswith(f"{body['token_prefix']}.")
    assert body["token_prefix"].startswith("nrh_")
    assert body["revoked_at"] is None

    connection = await session.get(BrowserConnection, body["id"])
    assert connection.token_hash == hashlib.sha256(body["token"].encode()).hexdigest()
    assert body["token"] not in connection.token_hash
    assert await session.get(BrowserHistorySettings, user.id) is not None

    listed = await client.get("/api/history/connections", headers=users.auth(user))
    assert listed.status_code == 200
    assert listed.json() == [{key: value for key, value in body.items() if key != "token"}]


async def test_connection_name_rejects_control_characters(client, users):
    user = await users.create()
    response = await _create_connection(client, users, user, "Chrome\u202ehidden")
    assert response.status_code == 422


async def test_connections_are_owner_scoped_and_revocation_is_idempotent(client, users, session):
    alice = await users.create(username="alice")
    bob = await users.create(username="bob")
    created = await _create_connection(client, users, alice)
    connection_id = created.json()["id"]

    assert (await client.get("/api/history/connections", headers=users.auth(bob))).json() == []
    other_delete = await client.delete(
        f"/api/history/connections/{connection_id}",
        headers=users.auth(bob),
    )
    assert other_delete.status_code == 404

    for _ in range(2):
        response = await client.delete(
            f"/api/history/connections/{connection_id}",
            headers=users.auth(alice),
        )
        assert response.status_code == 204
    await session.refresh(await session.get(BrowserConnection, connection_id))
    connection = await session.get(BrowserConnection, connection_id)
    assert connection.revoked_at is not None


async def test_extension_status_authenticates_only_scoped_raw_token(client, users, session):
    user = await users.create()
    created = await _create_connection(client, users, user)
    token = created.json()["token"]

    missing = await client.get("/api/history/sync/status")
    malformed = await client.get(
        "/api/history/sync/status",
        headers={"Authorization": "Bearer not-a-history-token"},
    )
    jwt = await client.get("/api/history/sync/status", headers=users.auth(user))
    assert missing.status_code == malformed.status_code == jwt.status_code == 401

    response = await client.get(
        "/api/history/sync/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["connection"]["name"] == "Chrome on MacBook"
    assert response.json()["user_name"] == "Test User"
    assert response.json()["settings"] == {"retention_days": 90, "sync_revision": 0}
    assert response.json()["domain_rules"] == []

    connection = await session.get(BrowserConnection, created.json()["id"])
    assert connection.last_seen_at is not None


async def test_extension_token_cannot_access_normal_protected_endpoints(client, users):
    user = await users.create()
    token = (await _create_connection(client, users, user)).json()["token"]
    response = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


async def test_connection_creation_is_rate_limited(client, users, monkeypatch):
    user = await users.create()
    monkeypatch.setattr(history_router, "TOKEN_CREATION_LIMIT", 1)
    assert (await _create_connection(client, users, user)).status_code == 201

    limited = await _create_connection(client, users, user, "Second browser")
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "3600"


async def test_revoked_extension_token_is_rejected(client, users):
    user = await users.create()
    created = await _create_connection(client, users, user)
    body = created.json()
    await client.delete(
        f"/api/history/connections/{body['id']}",
        headers=users.auth(user),
    )
    response = await client.get(
        "/api/history/sync/status",
        headers={"Authorization": f"Bearer {body['token']}"},
    )
    assert response.status_code == 401


async def test_history_settings_are_lazy_and_support_forever(client, users, session):
    user = await users.create()
    response = await client.get("/api/history/settings", headers=users.auth(user))
    assert response.json() == {"retention_days": 90, "sync_revision": 0}

    patched = await client.patch(
        "/api/history/settings",
        json={"retention_days": 365},
        headers=users.auth(user),
    )
    assert patched.json() == {"retention_days": 365, "sync_revision": 0}
    forever = await client.patch(
        "/api/history/settings",
        json={"retention_days": None},
        headers=users.auth(user),
    )
    assert forever.json() == {"retention_days": None, "sync_revision": 0}
    invalid = await client.patch(
        "/api/history/settings",
        json={"retention_days": 7},
        headers=users.auth(user),
    )
    assert invalid.status_code == 422
    assert (await session.get(BrowserHistorySettings, user.id)).retention_days is None


async def test_domain_rules_normalize_upsert_delete_and_advance_revision(client, users, session):
    user = await users.create()
    headers = users.auth(user)

    created = await client.post(
        "/api/history/domain-rules",
        json={
            "hostname": "BÜCHER.Example.",
            "match_subdomains": True,
            "mode": "metadata_only",
        },
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["hostname"] == "xn--bcher-kva.example"
    assert body["match_subdomains"] is True
    settings_row = await session.get(BrowserHistorySettings, user.id)
    assert settings_row.sync_revision == 1

    updated = await client.post(
        "/api/history/domain-rules",
        json={
            "hostname": "xn--bcher-kva.example",
            "match_subdomains": True,
            "mode": "exclude",
        },
        headers=headers,
    )
    assert updated.status_code == 201
    assert updated.json()["id"] == body["id"]
    assert updated.json()["mode"] == "exclude"
    rules = (await session.scalars(select(BrowserHistoryDomainRule))).all()
    assert len(rules) == 1
    await session.refresh(settings_row)
    assert settings_row.sync_revision == 2

    deleted = await client.delete(
        f"/api/history/domain-rules/{body['id']}",
        headers=headers,
    )
    assert deleted.status_code == 204
    await session.refresh(settings_row)
    assert settings_row.sync_revision == 3
    assert (await session.scalars(select(BrowserHistoryDomainRule))).all() == []


async def test_domain_rules_are_owner_scoped(client, users):
    alice = await users.create(username="alice")
    bob = await users.create(username="bob")
    created = await client.post(
        "/api/history/domain-rules",
        json={"hostname": "mail.example", "mode": "exclude"},
        headers=users.auth(alice),
    )
    rule_id = created.json()["id"]

    assert (await client.get("/api/history/domain-rules", headers=users.auth(bob))).json() == []
    deleted = await client.delete(
        f"/api/history/domain-rules/{rule_id}",
        headers=users.auth(bob),
    )
    assert deleted.status_code == 404


async def test_feature_flag_hides_history_surface(client, users, monkeypatch):
    user = await users.create()
    monkeypatch.setattr(settings, "browser_history_enabled", False)
    response = await client.get("/api/history/connections", headers=users.auth(user))
    assert response.status_code == 404


async def test_connection_rows_cascade_with_user(client, users, session):
    user = await users.create()
    created = await _create_connection(client, users, user)
    connection_id = created.json()["id"]
    await session.delete(user)
    await session.commit()
    assert await session.get(BrowserConnection, connection_id) is None
    assert await session.get(BrowserHistorySettings, user.id) is None


async def test_connection_revoked_timestamp_is_timezone_aware(client, users, session):
    user = await users.create()
    created = await _create_connection(client, users, user)
    connection_id = created.json()["id"]
    before = datetime.now(UTC)
    await client.delete(
        f"/api/history/connections/{connection_id}",
        headers=users.auth(user),
    )
    connection = await session.get(BrowserConnection, connection_id)
    assert connection.revoked_at >= before
