from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

ViewMode = Literal["cards", "list", "stories"]
SortOrder = Literal["newest", "oldest"]


# --- Auth ---

class RegisterIn(BaseModel):
    email: EmailStr
    username: str = Field(pattern=r"^[a-zA-Z0-9_]{3,30}$")
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    identifier: str  # email or username
    password: str


class UserOut(BaseModel):
    id: int
    email: EmailStr
    username: str
    name: str
    default_view: ViewMode = "cards"

    model_config = {"from_attributes": True}


class UserUpdateIn(BaseModel):
    default_view: ViewMode | None = None  # PATCH semantics: omitted/None = unchanged


class UserPublic(BaseModel):
    id: int
    username: str
    name: str

    model_config = {"from_attributes": True}


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# --- Devices (mobile push) ---

class DeviceIn(BaseModel):
    push_token: str = Field(min_length=1, max_length=512)
    platform: Literal["ios", "android"]


class DeviceOut(BaseModel):
    id: int
    push_token: str
    platform: str
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Feeds ---

class AddFeedIn(BaseModel):
    url: str = Field(min_length=4, max_length=2048)


class FeedOut(BaseModel):
    id: int
    url: str
    title: str  # effective: the user's rename if set, else the feed's own title
    site_url: str | None
    description: str | None
    last_fetched_at: datetime | None
    article_count: int
    unread_count: int
    # Articles still awaiting background enrichment (full text / lead image).
    pending_count: int = 0
    view_override: ViewMode | None = None
    # Per-subscription settings (NULL = default).
    title_override: str | None = None
    sort_order: SortOrder | None = None
    retention_days: int | None = None
    is_muted: bool = False
    # Global per-feed settings (shared by all subscribers).
    ai_enabled: bool = True
    refresh_interval_minutes: int = 15


class SubscriptionViewIn(BaseModel):
    view_override: ViewMode | None  # required field; explicit null clears the override


class FeedSettingsIn(BaseModel):
    """PATCH semantics: only fields present in the request are applied;
    an explicit null clears the override back to its default."""

    view_override: ViewMode | None = None
    title_override: str | None = Field(default=None, max_length=512)
    sort_order: SortOrder | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    is_muted: bool | None = None
    ai_enabled: bool | None = None
    refresh_interval_minutes: int | None = Field(default=None, ge=5, le=10080)


# --- Entities (smart link enrichment) ---

class EntityBadge(BaseModel):
    id: int
    kind: str
    key: str
    url: str
    source: str  # 'primary' | 'inline'
    badge: dict  # per-kind display fields, see enrichers.badge_for


class EntitySnapshotOut(BaseModel):
    captured_at: datetime
    data: dict

    model_config = {"from_attributes": True}


class EntityFull(EntityBadge):
    data: dict
    fetched_at: datetime | None
    deltas: dict = {}
    snapshots: list[EntitySnapshotOut] = []  # newest-first, capped


# --- Articles ---

class ArticleListItem(BaseModel):
    id: int
    feed_id: int
    feed_title: str
    title: str
    url: str
    comments_url: str | None
    author: str | None
    published_at: datetime | None
    excerpt: str
    image_url: str | None
    # Background enrichment hasn't visited this article yet; an image may
    # still be backfilled, so the UI keeps the thumbnail slot reserved.
    enriching: bool = False
    is_read: bool
    is_saved: bool
    summary: str = ""
    summary_short: str = ""
    summary_medium: str = ""
    entities: list[EntityBadge] = []


class ArticleDetail(ArticleListItem):
    content_html: str
    summary_model: str | None = None
    entities: list[EntityFull] = []


class ArticleStateIn(BaseModel):
    is_read: bool | None = None
    is_saved: bool | None = None


class MarkAllReadIn(BaseModel):
    feed_id: int | None = None


# --- Shares ---

class ShareCreateIn(BaseModel):
    article_id: int
    recipients: list[str] = Field(min_length=1, max_length=20)  # usernames
    note: str | None = Field(default=None, max_length=4000)


class ShareOut(BaseModel):
    id: int
    article: ArticleListItem
    from_user: UserPublic
    to_users: list[UserPublic]
    note: str | None
    created_at: datetime
    seen_at: datetime | None  # for received shares: my seen state


class UnseenCountOut(BaseModel):
    count: int


# --- AI ---

class AiStatusOut(BaseModel):
    configured: bool
    model: str | None
    search: bool = False  # web search/extract tools available to the Q&A agent
    search_provider: str | None = None  # "searxng" | "tavily" | None


class SummaryOut(BaseModel):
    summary: str
    summary_short: str = ""
    summary_medium: str = ""
    model: str | None
    generated_at: datetime | None


class AskIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    tool_events: list[dict] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
