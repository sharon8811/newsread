import pytest
import respx
from httpx import Response

from app.config import settings
from app.messaging import slack
from app.messaging.base import MessagingError


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setattr(settings, "slack_client_id", "slack-cid")
    monkeypatch.setattr(settings, "slack_client_secret", "slack-secret")


def test_is_configured(monkeypatch):
    assert slack.is_configured() is True
    monkeypatch.setattr(settings, "slack_client_id", "")
    assert slack.is_configured() is False


def test_authorize_url():
    url = slack.authorize_url("the-state")
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    assert "client_id=slack-cid" in url
    assert "user_scope=" in url and "chat%3Awrite" in url
    assert "state=the-state" in url
    assert "integrations%2Fslack%2Fcallback" in url


@respx.mock
async def test_exchange_code():
    respx.post("https://slack.com/api/oauth.v2.access").mock(
        return_value=Response(
            200,
            json={
                "ok": True,
                "authed_user": {"id": "U1", "access_token": "xoxp-1", "scope": "chat:write"},
                "team": {"id": "T1", "name": "Acme"},
            },
        )
    )
    respx.post("https://slack.com/api/auth.test").mock(
        return_value=Response(200, json={"ok": True, "user": "sharon", "team": "Acme"})
    )
    result = await slack.exchange_code("the-code")
    assert result.external_account_id == "U1"
    assert result.account_name == "sharon"
    assert result.workspace_id == "T1"
    assert result.workspace_name == "Acme"
    assert result.access_token == "xoxp-1"
    assert result.refresh_token is None


@respx.mock
async def test_exchange_code_rejected():
    respx.post("https://slack.com/api/oauth.v2.access").mock(
        return_value=Response(200, json={"ok": False, "error": "invalid_code"})
    )
    with pytest.raises(MessagingError, match="invalid_code"):
        await slack.exchange_code("bad")


@respx.mock
async def test_exchange_code_without_user_token():
    respx.post("https://slack.com/api/oauth.v2.access").mock(
        return_value=Response(200, json={"ok": True, "authed_user": {"id": "U1"}})
    )
    with pytest.raises(MessagingError, match="user token"):
        await slack.exchange_code("code")


def _conversations(payload):
    respx.post("https://slack.com/api/conversations.list").mock(
        return_value=Response(200, json={"ok": True, "channels": payload})
    )


@respx.mock
async def test_list_targets_maps_types_and_membership():
    _conversations(
        [
            {"id": "C1", "name": "general", "is_member": True},
            {"id": "C2", "name": "secret", "is_member": False},  # can't post -> dropped
            {"id": "G1", "is_mpim": True, "name_normalized": "mpdm-alice--bob-1"},
            {"id": "D1", "is_im": True, "user": "U2"},
            {"id": "D2", "is_im": True, "user": "ME"},  # self-DM dropped
        ]
    )
    respx.post("https://slack.com/api/users.list").mock(
        return_value=Response(
            200,
            json={
                "ok": True,
                "members": [
                    {"id": "U2", "name": "bob", "profile": {"display_name": "Bob"}},
                ],
            },
        )
    )
    targets = await slack.list_targets("xoxp", "ME")
    by_id = {t.external_id: t for t in targets}
    assert set(by_id) == {"C1", "G1", "D1"}
    assert by_id["C1"].display_name == "#general" and by_id["C1"].target_type == "channel"
    assert by_id["G1"].display_name == "alice, bob" and by_id["G1"].target_type == "group"
    assert by_id["D1"].display_name == "Bob" and by_id["D1"].target_type == "dm"


@respx.mock
async def test_list_targets_query_filter():
    _conversations(
        [
            {"id": "C1", "name": "ai-news", "is_member": True},
            {"id": "C2", "name": "random", "is_member": True},
        ]
    )
    targets = await slack.list_targets("xoxp", "ME", query="ai")
    assert [t.external_id for t in targets] == ["C1"]


@respx.mock
async def test_send_message_includes_url():
    route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )
    await slack.send_message(
        "xoxp", "channel", "C1", {}, "worth a read", "https://a.example/x", "T"
    )
    sent = dict(pair.split("=", 1) for pair in route.calls.last.request.content.decode().split("&"))
    assert sent["channel"] == "C1"
    assert "worth+a+read" in sent["text"] and "https%3A%2F%2Fa.example%2Fx" in sent["text"]


@respx.mock
async def test_send_message_url_only_when_no_message():
    route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True})
    )
    await slack.send_message("xoxp", "channel", "C1", {}, "   ", "https://a.example/x", "T")
    assert b"text=https%3A%2F%2Fa.example%2Fx" in route.calls.last.request.content


@respx.mock
async def test_send_error_mapping_and_reconnect():
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": False, "error": "not_in_channel"})
    )
    with pytest.raises(MessagingError, match="not a member") as exc:
        await slack.send_message("xoxp", "channel", "C1", {}, "m", "u", "t")
    assert exc.value.reconnect is False

    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": False, "error": "token_revoked"})
    )
    with pytest.raises(MessagingError) as exc:
        await slack.send_message("xoxp", "channel", "C1", {}, "m", "u", "t")
    assert exc.value.reconnect is True
