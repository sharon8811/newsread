from dataclasses import dataclass, field
from datetime import datetime


class MessagingError(Exception):
    """A platform API failure with a user-facing message. `reconnect` marks
    auth problems that only re-linking the platform can fix (revoked/expired
    credentials), as opposed to transient or per-target errors."""

    def __init__(self, message: str, *, reconnect: bool = False):
        super().__init__(message)
        self.reconnect = reconnect


@dataclass
class OAuthResult:
    """Everything a completed code exchange yields about the new connection."""

    external_account_id: str
    account_name: str
    workspace_id: str
    workspace_name: str
    access_token: str
    scopes: str = ""
    refresh_token: str | None = None
    token_expires_at: datetime | None = None


@dataclass
class Target:
    """A place messages can be sent (channel, group chat, DM...)."""

    external_id: str
    display_name: str
    target_type: str  # 'channel' | 'group' | 'dm' | 'chat'
    meta: dict = field(default_factory=dict)
