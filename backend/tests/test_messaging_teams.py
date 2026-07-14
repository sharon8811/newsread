import pytest
import respx
from httpx import Response

from app.config import settings
from app.messaging import teams
from app.messaging.base import MessagingError

TOKEN_URL = "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setattr(settings, "teams_client_id", "teams-cid")
    monkeypatch.setattr(settings, "teams_client_secret", "teams-secret")


def test_authorize_url():
    url = teams.authorize_url("st8")
    assert url.startswith("https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize?")
    assert "client_id=teams-cid" in url
    assert "ChannelMessage.Send" in url and "offline_access" in url
    assert "state=st8" in url


@respx.mock
async def test_exchange_code():
    respx.post(TOKEN_URL).mock(
        return_value=Response(
            200,
            json={
                "access_token": "at-1",
                "refresh_token": "rt-1",
                "expires_in": 3600,
                "scope": "ChatMessage.Send",
            },
        )
    )
    respx.get("https://graph.microsoft.com/v1.0/me").mock(
        return_value=Response(200, json={"id": "aad-1", "displayName": "Sharon T"})
    )
    respx.get("https://graph.microsoft.com/v1.0/organization?$select=id,displayName").mock(
        return_value=Response(200, json={"value": [{"displayName": "Acme Corp"}]})
    )
    result = await teams.exchange_code("code")
    assert result.external_account_id == "aad-1"
    assert result.account_name == "Sharon T"
    assert result.workspace_name == "Acme Corp"
    assert result.access_token == "at-1"
    assert result.refresh_token == "rt-1"
    assert result.token_expires_at is not None


@respx.mock
async def test_exchange_code_org_lookup_is_best_effort():
    respx.post(TOKEN_URL).mock(
        return_value=Response(200, json={"access_token": "at", "expires_in": 3600})
    )
    respx.get("https://graph.microsoft.com/v1.0/me").mock(
        return_value=Response(200, json={"id": "aad-1", "displayName": "S"})
    )
    respx.get("https://graph.microsoft.com/v1.0/organization?$select=id,displayName").mock(
        return_value=Response(403, json={"error": {"message": "nope"}})
    )
    result = await teams.exchange_code("code")
    assert result.workspace_name == ""


@respx.mock
async def test_exchange_code_failure():
    respx.post(TOKEN_URL).mock(
        return_value=Response(400, json={"error_description": "AADSTS bad code"})
    )
    with pytest.raises(MessagingError, match="AADSTS bad code") as exc:
        await teams.exchange_code("bad")
    assert exc.value.reconnect is False


@respx.mock
async def test_refresh_tokens_rotates():
    respx.post(TOKEN_URL).mock(
        return_value=Response(
            200,
            json={
                "access_token": "at-2",
                "refresh_token": "rt-2",
                "expires_in": 3600,
            },
        )
    )
    access, refresh, expires_at = await teams.refresh_tokens("rt-1")
    assert (access, refresh) == ("at-2", "rt-2")
    assert expires_at is not None


@respx.mock
async def test_refresh_failure_means_reconnect():
    respx.post(TOKEN_URL).mock(return_value=Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(MessagingError) as exc:
        await teams.refresh_tokens("dead")
    assert exc.value.reconnect is True


@respx.mock
async def test_list_targets():
    respx.get(
        "https://graph.microsoft.com/v1.0/me/chats"
        "?$top=50&$expand=members($select=displayName,userId)"
    ).mock(
        return_value=Response(
            200,
            json={
                "value": [
                    {
                        "id": "chat1",
                        "chatType": "oneOnOne",
                        "topic": None,
                        "members": [
                            {"displayName": "Me", "userId": "ME"},
                            {"displayName": "Dana", "userId": "U2"},
                        ],
                    },
                    {"id": "chat2", "chatType": "group", "topic": "Launch crew", "members": []},
                    {"id": "chat3", "chatType": "meeting", "topic": "Standup", "members": []},
                ]
            },
        )
    )
    respx.get("https://graph.microsoft.com/v1.0/me/joinedTeams").mock(
        return_value=Response(200, json={"value": [{"id": "team1", "displayName": "Eng"}]})
    )
    respx.get("https://graph.microsoft.com/v1.0/teams/team1/channels").mock(
        return_value=Response(200, json={"value": [{"id": "ch1", "displayName": "General"}]})
    )
    targets = await teams.list_targets("at", "ME")
    by_id = {t.external_id: t for t in targets}
    assert set(by_id) == {"chat1", "chat2", "ch1"}  # meeting chat excluded
    assert by_id["chat1"].display_name == "Dana" and by_id["chat1"].target_type == "dm"
    assert by_id["chat2"].display_name == "Launch crew" and by_id["chat2"].target_type == "group"
    assert by_id["ch1"].display_name == "Eng › General"
    assert by_id["ch1"].meta == {"team_id": "team1"}


@respx.mock
async def test_list_targets_skips_unreadable_team():
    respx.get(
        "https://graph.microsoft.com/v1.0/me/chats"
        "?$top=50&$expand=members($select=displayName,userId)"
    ).mock(return_value=Response(200, json={"value": []}))
    respx.get("https://graph.microsoft.com/v1.0/me/joinedTeams").mock(
        return_value=Response(
            200,
            json={
                "value": [
                    {"id": "team1", "displayName": "Locked"},
                    {"id": "team2", "displayName": "Open"},
                ]
            },
        )
    )
    respx.get("https://graph.microsoft.com/v1.0/teams/team1/channels").mock(
        return_value=Response(403, json={"error": {"message": "denied"}})
    )
    respx.get("https://graph.microsoft.com/v1.0/teams/team2/channels").mock(
        return_value=Response(200, json={"value": [{"id": "ch2", "displayName": "General"}]})
    )
    targets = await teams.list_targets("at", "ME")
    assert [t.external_id for t in targets] == ["ch2"]


@respx.mock
async def test_send_to_channel_builds_html():
    route = respx.post("https://graph.microsoft.com/v1.0/teams/team1/channels/ch1/messages").mock(
        return_value=Response(201, json={"id": "1"})
    )
    await teams.send_message(
        "at",
        "channel",
        "ch1",
        {"team_id": "team1"},
        "must <read>",
        "https://a.example/x?a=1&b=2",
        "Big News",
    )
    body = route.calls.last.request.content.decode()
    assert "must &lt;read&gt;" in body
    assert "a=1&amp;b=2" in body and "Big News" in body


@respx.mock
async def test_send_to_chat_and_errors():
    respx.post("https://graph.microsoft.com/v1.0/chats/chat1/messages").mock(
        return_value=Response(401)
    )
    with pytest.raises(MessagingError) as exc:
        await teams.send_message("at", "dm", "chat1", {}, "m", "u", "t")
    assert exc.value.reconnect is True

    respx.post("https://graph.microsoft.com/v1.0/chats/chat1/messages").mock(
        return_value=Response(404)
    )
    with pytest.raises(MessagingError, match="no longer exists"):
        await teams.send_message("at", "dm", "chat1", {}, "m", "u", "t")


async def test_send_channel_without_team_id():
    with pytest.raises(MessagingError, match="missing its team"):
        await teams.send_message("at", "channel", "ch1", {}, "m", "u", "t")
