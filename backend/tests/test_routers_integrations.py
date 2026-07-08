from datetime import datetime, timedelta, timezone

import pytest

from app import crypto
from app.config import settings
from app.messaging.base import MessagingError, OAuthResult, Target
from app.models import ExternalShare, MessagingConnection, ShareTarget
from app.routers.integrations import _make_state


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setattr(settings, "slack_client_id", "cid")
    monkeypatch.setattr(settings, "slack_client_secret", "sec")
    monkeypatch.setattr(settings, "teams_client_id", "tcid")
    monkeypatch.setattr(settings, "teams_client_secret", "tsec")


async def _connect(session, user, platform="slack", **overrides) -> MessagingConnection:
    fields = dict(
        user_id=user.id,
        platform=platform,
        external_account_id="U1",
        account_name="sharon",
        workspace_id="T1",
        workspace_name="Acme",
        access_token_enc=crypto.encrypt_token("tok-1"),
        status="active",
    )
    fields.update(overrides)
    connection = MessagingConnection(**fields)
    session.add(connection)
    await session.commit()
    await session.refresh(connection)
    return connection


async def _saved_target(session, user, connection, **overrides) -> ShareTarget:
    fields = dict(
        user_id=user.id,
        connection_id=connection.id,
        target_type="channel",
        external_id="C1",
        display_name="#general",
        meta={},
    )
    fields.update(overrides)
    target = ShareTarget(**fields)
    session.add(target)
    await session.commit()
    await session.refresh(target)
    return target


# --- status ---


async def test_status_lists_both_platforms(client, users, session):
    me = await users.create()
    await _connect(session, me, "slack")
    resp = await client.get("/api/integrations", headers=users.auth(me))
    body = {i["platform"]: i for i in resp.json()}
    assert set(body) == {"slack", "teams"}
    assert body["slack"]["connected"] is True
    assert body["slack"]["workspace_name"] == "Acme"
    assert body["teams"]["connected"] is False
    assert body["teams"]["configured"] is True


async def test_status_unconfigured_platform(client, users, monkeypatch):
    monkeypatch.setattr(settings, "teams_client_id", "")
    me = await users.create()
    resp = await client.get("/api/integrations", headers=users.auth(me))
    body = {i["platform"]: i for i in resp.json()}
    assert body["teams"]["configured"] is False
    assert body["slack"]["configured"] is True


# --- authorize + callback ---


async def test_authorize_returns_provider_url(client, users):
    me = await users.create()
    resp = await client.get("/api/integrations/slack/authorize", headers=users.auth(me))
    assert resp.status_code == 200
    assert resp.json()["url"].startswith("https://slack.com/oauth/v2/authorize?")


async def test_authorize_unknown_platform(client, users):
    me = await users.create()
    resp = await client.get("/api/integrations/discord/authorize", headers=users.auth(me))
    assert resp.status_code == 404


async def test_authorize_unconfigured_503(client, users, monkeypatch):
    monkeypatch.setattr(settings, "slack_client_id", "")
    me = await users.create()
    resp = await client.get("/api/integrations/slack/authorize", headers=users.auth(me))
    assert resp.status_code == 503


async def test_state_token_is_not_an_access_token(client, users):
    """The signed OAuth state must never pass bearer auth."""
    me = await users.create()
    state = _make_state(me.id, "slack")
    resp = await client.get("/api/feeds", headers={"Authorization": f"Bearer {state}"})
    assert resp.status_code == 401


async def test_callback_creates_connection(client, users, session, monkeypatch):
    me = await users.create()

    async def fake_exchange(code):
        assert code == "the-code"
        return OAuthResult(
            external_account_id="U9",
            account_name="sharon",
            workspace_id="T9",
            workspace_name="Acme",
            access_token="xoxp-fresh",
            scopes="chat:write",
        )

    monkeypatch.setattr("app.messaging.slack.exchange_code", fake_exchange)
    resp = await client.get(
        "/api/integrations/slack/callback",
        params={"state": _make_state(me.id, "slack"), "code": "the-code"},
    )
    assert resp.status_code == 307
    assert resp.headers["location"] == "http://front.test/settings?connected=slack"

    connection = await session.get(MessagingConnection, 1)
    assert connection.user_id == me.id
    assert connection.access_token_enc != "xoxp-fresh"  # encrypted at rest
    assert crypto.decrypt_token(connection.access_token_enc) == "xoxp-fresh"


async def test_callback_upserts_existing_connection(client, users, session, monkeypatch):
    me = await users.create()
    old = await _connect(session, me, "slack", status="error")

    async def fake_exchange(code):
        return OAuthResult("U1", "sharon", "T1", "Acme", "xoxp-new")

    monkeypatch.setattr("app.messaging.slack.exchange_code", fake_exchange)
    await client.get(
        "/api/integrations/slack/callback",
        params={"state": _make_state(me.id, "slack"), "code": "c"},
    )
    await session.refresh(old)
    assert crypto.decrypt_token(old.access_token_enc) == "xoxp-new"
    assert old.status == "active"


async def test_callback_user_denied(client, users):
    me = await users.create()
    resp = await client.get(
        "/api/integrations/slack/callback",
        params={"state": _make_state(me.id, "slack"), "error": "access_denied"},
    )
    assert resp.status_code == 307
    assert "error=slack:access_denied" in resp.headers["location"]


async def test_callback_bad_state(client):
    resp = await client.get(
        "/api/integrations/slack/callback", params={"state": "junk", "code": "c"}
    )
    assert resp.status_code == 400


async def test_callback_state_platform_mismatch(client, users):
    me = await users.create()
    resp = await client.get(
        "/api/integrations/teams/callback",
        params={"state": _make_state(me.id, "slack"), "code": "c"},
    )
    assert resp.status_code == 400


async def test_callback_exchange_failure_redirects(client, users, monkeypatch):
    me = await users.create()

    async def fake_exchange(code):
        raise MessagingError("nope")

    monkeypatch.setattr("app.messaging.slack.exchange_code", fake_exchange)
    resp = await client.get(
        "/api/integrations/slack/callback",
        params={"state": _make_state(me.id, "slack"), "code": "c"},
    )
    assert "error=slack:exchange_failed" in resp.headers["location"]


# --- disconnect ---


async def test_disconnect_removes_connection_and_targets(client, users, session):
    me = await users.create()
    connection = await _connect(session, me)
    await _saved_target(session, me, connection)
    resp = await client.delete("/api/integrations/slack", headers=users.auth(me))
    assert resp.status_code == 204
    # Fresh queries — the app deleted the rows through its own session.
    assert (await session.execute(MessagingConnection.__table__.select())).first() is None
    assert (await session.execute(ShareTarget.__table__.select())).first() is None


async def test_disconnect_not_connected(client, users):
    me = await users.create()
    resp = await client.delete("/api/integrations/slack", headers=users.auth(me))
    assert resp.status_code == 409


# --- live target search ---


async def test_search_targets_marks_saved(client, users, session, monkeypatch):
    me = await users.create()
    connection = await _connect(session, me)
    saved = await _saved_target(session, me, connection, external_id="C1")

    async def fake_list(token, account_id, query=""):
        assert token == "tok-1" and account_id == "U1" and query == "gen"
        return [Target("C1", "#general", "channel"), Target("C2", "#random", "channel")]

    monkeypatch.setattr("app.messaging.slack.list_targets", fake_list)
    resp = await client.get(
        "/api/integrations/slack/targets", params={"q": "gen"}, headers=users.auth(me)
    )
    body = {t["external_id"]: t for t in resp.json()}
    assert body["C1"]["saved_id"] == saved.id
    assert body["C2"]["saved_id"] is None


async def test_search_targets_reconnect_error_flags_connection(
    client, users, session, monkeypatch
):
    me = await users.create()
    connection = await _connect(session, me)

    async def fake_list(token, account_id, query=""):
        raise MessagingError("token revoked", reconnect=True)

    monkeypatch.setattr("app.messaging.slack.list_targets", fake_list)
    resp = await client.get("/api/integrations/slack/targets", headers=users.auth(me))
    assert resp.status_code == 502
    assert resp.json()["detail"]["reconnect"] is True
    await session.refresh(connection)
    assert connection.status == "error"


async def test_search_targets_requires_connection(client, users):
    me = await users.create()
    resp = await client.get("/api/integrations/slack/targets", headers=users.auth(me))
    assert resp.status_code == 409


# --- saved targets CRUD ---


async def test_save_list_delete_target(client, users, session):
    me = await users.create()
    await _connect(session, me)
    resp = await client.post("/api/share-targets", json={
        "platform": "slack", "external_id": "C7", "display_name": "#news",
        "target_type": "channel", "meta": {},
    }, headers=users.auth(me))
    assert resp.status_code == 201
    target_id = resp.json()["id"]
    assert resp.json()["platform"] == "slack"

    resp = await client.get("/api/share-targets", headers=users.auth(me))
    assert [t["id"] for t in resp.json()] == [target_id]

    resp = await client.delete(f"/api/share-targets/{target_id}", headers=users.auth(me))
    assert resp.status_code == 204
    resp = await client.get("/api/share-targets", headers=users.auth(me))
    assert resp.json() == []


async def test_save_target_is_idempotent(client, users, session):
    me = await users.create()
    await _connect(session, me)
    body = {
        "platform": "slack", "external_id": "C7", "display_name": "#news",
        "target_type": "channel",
    }
    first = await client.post("/api/share-targets", json=body, headers=users.auth(me))
    second = await client.post("/api/share-targets", json=body, headers=users.auth(me))
    assert first.json()["id"] == second.json()["id"]


async def test_delete_someone_elses_target_404(client, users, session):
    me = await users.create()
    other = await users.create()
    connection = await _connect(session, other)
    target = await _saved_target(session, other, connection)
    resp = await client.delete(f"/api/share-targets/{target.id}", headers=users.auth(me))
    assert resp.status_code == 404


# --- external sends ---


async def _sharable(users, data):
    me = await users.create()
    feed = await data.feed()
    await data.subscribe(me, feed)
    article = await data.article(feed, title="Big News")
    return me, article


async def test_send_to_saved_target(client, users, data, session, monkeypatch):
    me, article = await _sharable(users, data)
    connection = await _connect(session, me)
    target = await _saved_target(session, me, connection)
    calls = {}

    async def fake_send(token, target_type, external_id, meta, message, url, title):
        calls.update(token=token, external_id=external_id, message=message, url=url)

    monkeypatch.setattr("app.messaging.slack.send_message", fake_send)
    resp = await client.post("/api/shares/external", json={
        "article_id": article.id, "message": "worth a read", "target_id": target.id,
    }, headers=users.auth(me))
    assert resp.status_code == 201
    assert resp.json()["status"] == "sent"
    assert resp.json()["target_display"] == "#general"
    assert calls == {
        "token": "tok-1", "external_id": "C1",
        "message": "worth a read", "url": article.url,
    }
    await session.refresh(target)
    assert target.last_used_at is not None


async def test_send_to_adhoc_target(client, users, data, session, monkeypatch):
    me, article = await _sharable(users, data)
    await _connect(session, me, "teams")

    async def fake_send(token, target_type, external_id, meta, message, url, title):
        assert (target_type, external_id, meta) == ("channel", "ch1", {"team_id": "t1"})

    monkeypatch.setattr("app.messaging.teams.send_message", fake_send)
    resp = await client.post("/api/shares/external", json={
        "article_id": article.id,
        "message": "",
        "target": {
            "platform": "teams", "external_id": "ch1", "display_name": "Eng › General",
            "target_type": "channel", "meta": {"team_id": "t1"},
        },
    }, headers=users.auth(me))
    assert resp.status_code == 201


async def test_send_failure_logs_and_returns_502(client, users, data, session, monkeypatch):
    me, article = await _sharable(users, data)
    connection = await _connect(session, me)
    target = await _saved_target(session, me, connection)

    async def fake_send(*args, **kwargs):
        raise MessagingError("You're not a member of that channel on Slack")

    monkeypatch.setattr("app.messaging.slack.send_message", fake_send)
    resp = await client.post("/api/shares/external", json={
        "article_id": article.id, "message": "m", "target_id": target.id,
    }, headers=users.auth(me))
    assert resp.status_code == 502
    assert resp.json()["detail"]["reconnect"] is False

    record = (await session.execute(ExternalShare.__table__.select())).first()
    assert record.status == "failed" and "not a member" in record.error
    await session.refresh(connection)
    assert connection.status == "active"  # per-target failure, not an auth one


async def test_send_auth_failure_flags_reconnect(client, users, data, session, monkeypatch):
    me, article = await _sharable(users, data)
    connection = await _connect(session, me)
    target = await _saved_target(session, me, connection)

    async def fake_send(*args, **kwargs):
        raise MessagingError("revoked", reconnect=True)

    monkeypatch.setattr("app.messaging.slack.send_message", fake_send)
    resp = await client.post("/api/shares/external", json={
        "article_id": article.id, "target_id": target.id,
    }, headers=users.auth(me))
    assert resp.status_code == 502
    assert resp.json()["detail"]["reconnect"] is True
    await session.refresh(connection)
    assert connection.status == "error"


async def test_send_requires_some_target(client, users, data):
    me, article = await _sharable(users, data)
    resp = await client.post("/api/shares/external", json={
        "article_id": article.id, "message": "m",
    }, headers=users.auth(me))
    assert resp.status_code == 422


async def test_send_inaccessible_article_404(client, users, data, session):
    me = await users.create()
    feed = await data.feed()  # not subscribed
    article = await data.article(feed)
    connection = await _connect(session, me)
    target = await _saved_target(session, me, connection)
    resp = await client.post("/api/shares/external", json={
        "article_id": article.id, "target_id": target.id,
    }, headers=users.auth(me))
    assert resp.status_code == 404


async def test_send_refreshes_expired_teams_token(client, users, data, session, monkeypatch):
    me, article = await _sharable(users, data)
    connection = await _connect(
        session, me, "teams",
        refresh_token_enc=crypto.encrypt_token("rt-old"),
        token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    target = await _saved_target(session, me, connection, external_id="chat1", target_type="chat")
    sent_with = {}

    async def fake_refresh(refresh_token):
        assert refresh_token == "rt-old"
        return "at-new", "rt-new", datetime.now(timezone.utc) + timedelta(hours=1)

    async def fake_send(token, *args, **kwargs):
        sent_with["token"] = token

    monkeypatch.setattr("app.messaging.teams.refresh_tokens", fake_refresh)
    monkeypatch.setattr("app.messaging.teams.send_message", fake_send)
    resp = await client.post("/api/shares/external", json={
        "article_id": article.id, "target_id": target.id,
    }, headers=users.auth(me))
    assert resp.status_code == 201
    assert sent_with["token"] == "at-new"
    await session.refresh(connection)
    assert crypto.decrypt_token(connection.refresh_token_enc) == "rt-new"


# --- AI share message ---


async def test_share_message_requires_llm(client, users, data):
    me, article = await _sharable(users, data)
    resp = await client.post("/api/ai/share-message", json={
        "article_id": article.id,
    }, headers=users.auth(me))
    assert resp.status_code == 503


async def test_share_message_generates(client, users, data, monkeypatch):
    me, article = await _sharable(users, data)
    monkeypatch.setattr("app.llm.is_configured", lambda: True)

    async def fake_share_message(*, title, summary, draft, tone, target_name):
        assert title == "Big News" and draft == "my draft"
        return "A polished message."

    monkeypatch.setattr("app.llm.share_message", fake_share_message)
    resp = await client.post("/api/ai/share-message", json={
        "article_id": article.id, "draft": "my draft",
    }, headers=users.auth(me))
    assert resp.status_code == 200
    assert resp.json()["message"] == "A polished message."


async def test_share_message_llm_failure_502(client, users, data, monkeypatch):
    me, article = await _sharable(users, data)
    monkeypatch.setattr("app.llm.is_configured", lambda: True)

    async def boom(**kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr("app.llm.share_message", boom)
    resp = await client.post("/api/ai/share-message", json={
        "article_id": article.id,
    }, headers=users.auth(me))
    assert resp.status_code == 502
