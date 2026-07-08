"""Slack adapter: OAuth v2 with user-token scopes, so messages post genuinely
as the user (their name/avatar, no bot attribution). Token rotation is off in
the app manifest, so user tokens don't expire — no refresh flow."""

import logging
from urllib.parse import urlencode

import httpx

from ..config import settings
from .base import MessagingError, OAuthResult, Target

logger = logging.getLogger(__name__)

AUTHORIZE_ENDPOINT = "https://slack.com/oauth/v2/authorize"
API_BASE = "https://slack.com/api"

# Requested as *user* scopes (the `user_scope` OAuth param); must stay a
# subset of the user scopes declared in the app manifest.
USER_SCOPES = "chat:write,channels:read,groups:read,im:read,mpim:read,users:read,team:read"

# Slack error codes that mean the stored token is dead and only reconnecting
# from settings can fix it.
_RECONNECT_ERRORS = {"invalid_auth", "token_revoked", "token_expired", "account_inactive", "not_authed"}

_SEND_ERRORS = {
    "not_in_channel": "You're not a member of that channel on Slack",
    "channel_not_found": "That channel no longer exists (or you can't see it)",
    "is_archived": "That channel is archived",
    "msg_too_long": "The message is too long for Slack",
    "restricted_action": "Posting to that channel is restricted by the workspace",
    "ratelimited": "Slack is rate-limiting requests; try again in a minute",
}


def is_configured() -> bool:
    return bool(settings.slack_client_id and settings.slack_client_secret)


def redirect_uri() -> str:
    return f"{settings.oauth_redirect_base.rstrip('/')}/api/integrations/slack/callback"


def authorize_url(state: str) -> str:
    query = urlencode(
        {
            "client_id": settings.slack_client_id,
            "user_scope": USER_SCOPES,
            "redirect_uri": redirect_uri(),
            "state": state,
        }
    )
    return f"{AUTHORIZE_ENDPOINT}?{query}"


async def _api(
    client: httpx.AsyncClient, method: str, token: str | None = None, **params
) -> dict:
    """Call a Slack Web API method; raises MessagingError on ok=false."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = await client.post(f"{API_BASE}/{method}", data=params, headers=headers)
    data = response.json()
    if not data.get("ok"):
        error = data.get("error", "unknown_error")
        raise MessagingError(
            _SEND_ERRORS.get(error, f"Slack API error: {error}"),
            reconnect=error in _RECONNECT_ERRORS,
        )
    return data


async def exchange_code(code: str) -> OAuthResult:
    async with httpx.AsyncClient(timeout=20) as client:
        data = await _api(
            client,
            "oauth.v2.access",
            client_id=settings.slack_client_id,
            client_secret=settings.slack_client_secret,
            code=code,
            redirect_uri=redirect_uri(),
        )
        authed = data.get("authed_user") or {}
        token = authed.get("access_token")
        if not token:
            raise MessagingError("Slack did not return a user token — check the app's user scopes")
        # auth.test resolves the human-readable handle + workspace name.
        who = await _api(client, "auth.test", token=token)
    return OAuthResult(
        external_account_id=authed.get("id", ""),
        account_name=who.get("user", ""),
        workspace_id=(data.get("team") or {}).get("id", ""),
        workspace_name=(data.get("team") or {}).get("name", "") or who.get("team", ""),
        access_token=token,
        scopes=authed.get("scope", ""),
    )


async def _user_names(client: httpx.AsyncClient, token: str) -> dict[str, str]:
    """user id -> display name, for labelling DMs. One page is plenty here."""
    try:
        data = await _api(client, "users.list", token=token, limit=500)
    except MessagingError as exc:
        if exc.reconnect:
            raise
        return {}
    names = {}
    for member in data.get("members", []):
        profile = member.get("profile") or {}
        names[member["id"]] = (
            profile.get("display_name") or profile.get("real_name") or member.get("name", "")
        )
    return names


async def list_targets(token: str, account_id: str, query: str = "") -> list[Target]:
    """Channels + group DMs + DMs the user can post to, filtered by `query`."""
    targets: list[Target] = []
    async with httpx.AsyncClient(timeout=20) as client:
        cursor = ""
        conversations: list[dict] = []
        for _ in range(3):  # up to 600 conversations; enough for a picker
            data = await _api(
                client,
                "conversations.list",
                token=token,
                types="public_channel,private_channel,mpim,im",
                exclude_archived="true",
                limit="200",
                **({"cursor": cursor} if cursor else {}),
            )
            conversations.extend(data.get("channels", []))
            cursor = (data.get("response_metadata") or {}).get("next_cursor", "")
            if not cursor:
                break

        names: dict[str, str] = {}
        if any(c.get("is_im") for c in conversations):
            names = await _user_names(client, token)

    for conv in conversations:
        if conv.get("is_im"):
            user_id = conv.get("user", "")
            if user_id == account_id or user_id.startswith("USLACKBOT"):
                continue
            display = names.get(user_id)
            if not display:
                continue
            targets.append(Target(conv["id"], display, "dm"))
        elif conv.get("is_mpim"):
            # mpdm-alice--bob--carol-1 -> alice, bob, carol
            raw = conv.get("name_normalized") or conv.get("name", "")
            display = ", ".join(raw.removeprefix("mpdm-").rstrip("-0123456789").split("--"))
            targets.append(Target(conv["id"], display or "Group DM", "group"))
        else:
            # A user token can only post where the user is a member.
            if not conv.get("is_member"):
                continue
            targets.append(
                Target(conv["id"], f"#{conv.get('name', conv['id'])}", "channel")
            )

    if query:
        needle = query.lower()
        targets = [t for t in targets if needle in t.display_name.lower()]
    targets.sort(key=lambda t: t.display_name.lower())
    return targets[:50]


async def send_message(
    token: str,
    target_type: str,
    external_id: str,
    meta: dict,
    message: str,
    article_url: str,
    article_title: str,
) -> None:
    """Post as the user; the article URL rides in the text and Slack unfurls it."""
    text = f"{message.strip()}\n{article_url}" if message.strip() else article_url
    async with httpx.AsyncClient(timeout=20) as client:
        await _api(
            client,
            "chat.postMessage",
            token=token,
            channel=external_id,
            text=text,
            unfurl_links="true",
        )
