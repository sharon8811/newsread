"""Microsoft Teams adapter via Microsoft Graph delegated permissions —
messages send as the signed-in user. Graph access tokens live ~1 hour, so
connections keep a refresh token and the router refreshes before use."""

import html
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from ..config import settings
from .base import MessagingError, OAuthResult, Target

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# offline_access yields the refresh token; the rest must be granted as
# delegated permissions on the Entra app registration.
SCOPES = (
    "openid profile email offline_access User.Read Chat.ReadBasic "
    "ChatMessage.Send ChannelMessage.Send Team.ReadBasic.All Channel.ReadBasic.All"
)

MAX_TEAMS_LISTED = 15  # channel listing costs one Graph call per team


def _authority() -> str:
    return f"https://login.microsoftonline.com/{settings.teams_tenant}"


def is_configured() -> bool:
    return bool(settings.teams_client_id and settings.teams_client_secret)


def redirect_uri() -> str:
    return f"{settings.oauth_redirect_base.rstrip('/')}/api/integrations/teams/callback"


def authorize_url(state: str) -> str:
    query = urlencode(
        {
            "client_id": settings.teams_client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri(),
            "response_mode": "query",
            "scope": SCOPES,
            "state": state,
        }
    )
    return f"{_authority()}/oauth2/v2.0/authorize?{query}"


async def _token_request(client: httpx.AsyncClient, **data) -> dict:
    response = await client.post(
        f"{_authority()}/oauth2/v2.0/token",
        data={
            "client_id": settings.teams_client_id,
            "client_secret": settings.teams_client_secret,
            "scope": SCOPES,
            **data,
        },
    )
    payload = response.json()
    if response.status_code != 200 or "access_token" not in payload:
        description = payload.get("error_description") or payload.get("error") or "unknown error"
        logger.warning("Teams token request failed: %s", description)
        raise MessagingError(
            f"Microsoft sign-in failed: {description.splitlines()[0][:200]}",
            # A dead refresh token (revoked, >90 days idle) means reconnect.
            reconnect="grant_type" in data and data["grant_type"] == "refresh_token",
        )
    return payload


def _expiry(payload: dict) -> datetime:
    # A small safety margin so we never send with a token mid-expiry.
    return datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in", 3600)) - 120)


async def _graph(client: httpx.AsyncClient, token: str, method: str, path: str, **kwargs) -> dict:
    response = await client.request(
        method,
        f"{GRAPH_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        **kwargs,
    )
    if response.status_code == 401:
        raise MessagingError("Microsoft session expired — reconnect Teams", reconnect=True)
    if response.status_code == 403:
        raise MessagingError("Microsoft Graph denied the request (missing permission or policy)")
    if response.status_code == 404:
        raise MessagingError("That chat or channel no longer exists (or you can't access it)")
    if response.status_code >= 400:
        message = ""
        try:
            message = (response.json().get("error") or {}).get("message", "")
        except Exception:
            pass
        raise MessagingError(f"Microsoft Graph error {response.status_code}: {message[:200]}")
    return response.json() if response.content else {}


async def exchange_code(code: str) -> OAuthResult:
    async with httpx.AsyncClient(timeout=20) as client:
        payload = await _token_request(
            client, grant_type="authorization_code", code=code, redirect_uri=redirect_uri()
        )
        token = payload["access_token"]
        me = await _graph(client, token, "GET", "/me")
        org_name = ""
        try:  # User.Read covers basic company info, but stay best-effort.
            org = await _graph(client, token, "GET", "/organization?$select=id,displayName")
            if org.get("value"):
                org_name = org["value"][0].get("displayName", "")
        except MessagingError:
            pass
    return OAuthResult(
        external_account_id=me.get("id", ""),
        account_name=me.get("displayName", "") or me.get("userPrincipalName", ""),
        workspace_id="",
        workspace_name=org_name,
        access_token=token,
        scopes=payload.get("scope", ""),
        refresh_token=payload.get("refresh_token"),
        token_expires_at=_expiry(payload),
    )


async def refresh_tokens(refresh_token: str) -> tuple[str, str | None, datetime]:
    """-> (access_token, new_refresh_token, expires_at). Microsoft rotates
    refresh tokens, so always persist the returned one."""
    async with httpx.AsyncClient(timeout=20) as client:
        payload = await _token_request(
            client, grant_type="refresh_token", refresh_token=refresh_token
        )
    return payload["access_token"], payload.get("refresh_token"), _expiry(payload)


async def list_targets(token: str, account_id: str, query: str = "") -> list[Target]:
    """The user's chats plus channels of joined teams, filtered by `query`."""
    targets: list[Target] = []
    async with httpx.AsyncClient(timeout=30) as client:
        chats = await _graph(
            client,
            token,
            "GET",
            "/me/chats?$top=50&$expand=members($select=displayName,userId)",
        )
        for chat in chats.get("value", []):
            if chat.get("chatType") not in ("oneOnOne", "group"):
                continue  # meeting chats are noise in a share picker
            others = [
                m.get("displayName", "")
                for m in chat.get("members", [])
                if m.get("userId") != account_id and m.get("displayName")
            ]
            display = chat.get("topic") or ", ".join(others)
            if not display:
                continue
            kind = "dm" if chat.get("chatType") == "oneOnOne" else "group"
            targets.append(Target(chat["id"], display, kind, {"kind": "chat"}))

        teams = await _graph(client, token, "GET", "/me/joinedTeams")
        for team in teams.get("value", [])[:MAX_TEAMS_LISTED]:
            try:
                channels = await _graph(client, token, "GET", f"/teams/{team['id']}/channels")
            except MessagingError as exc:
                if exc.reconnect:
                    raise
                continue  # a single unreadable team shouldn't kill the picker
            for channel in channels.get("value", []):
                targets.append(
                    Target(
                        channel["id"],
                        f"{team.get('displayName', '')} › {channel.get('displayName', '')}",
                        "channel",
                        {"team_id": team["id"]},
                    )
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
    """HTML body so the article link is reliably clickable in Teams."""
    parts = []
    if message.strip():
        parts.append(html.escape(message.strip()).replace("\n", "<br>"))
    parts.append(
        f'<a href="{html.escape(article_url, quote=True)}">'
        f"{html.escape(article_title or article_url)}</a>"
    )
    body = {"body": {"contentType": "html", "content": "<br>".join(parts)}}

    if target_type == "channel":
        team_id = (meta or {}).get("team_id")
        if not team_id:
            raise MessagingError("This saved channel is missing its team — re-add it in settings")
        path = f"/teams/{team_id}/channels/{external_id}/messages"
    else:
        path = f"/chats/{external_id}/messages"

    async with httpx.AsyncClient(timeout=20) as client:
        await _graph(client, token, "POST", path, json=body)
