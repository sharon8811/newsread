"""Outbound messaging-platform adapters (share to Slack / Teams as the user).

Each adapter module exposes the same surface:
    is_configured() -> bool
    redirect_uri() -> str
    authorize_url(state) -> str
    exchange_code(code) -> OAuthResult
    list_targets(token, account_id, query) -> list[Target]
    send_message(token, target_type, external_id, meta, message, url, title)
Teams additionally has refresh_tokens() (Slack user tokens don't expire).
"""

from . import slack, teams
from .base import MessagingError, OAuthResult, Target

ADAPTERS = {"slack": slack, "teams": teams}

__all__ = ["ADAPTERS", "MessagingError", "OAuthResult", "Target", "slack", "teams"]
