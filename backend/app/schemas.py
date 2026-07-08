from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

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
    # Template for generated article images; "" resets to the default prompt.
    image_prompt: str | None = Field(default=None, max_length=2000)


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
    # An AI illustration is being rendered right now — worth one refetch soon.
    image_pending: bool = False


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


# --- Projects ---

ProjectRole = Literal["owner", "member"]


def _stripped_nonempty(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class ProjectCreateIn(BaseModel):
    name: str = Field(max_length=120)
    description: str = Field(default="", max_length=2000)

    _strip_name = field_validator("name")(_stripped_nonempty)


class ProjectUpdateIn(BaseModel):
    """PATCH semantics: only fields present in the request are applied."""

    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        return None if value is None else _stripped_nonempty(value)


class ProjectMemberOut(BaseModel):
    user: UserPublic
    role: ProjectRole


class ProjectOut(BaseModel):
    id: int
    name: str
    description: str
    owner: UserPublic
    my_role: ProjectRole
    members: list[ProjectMemberOut]
    # Only counts articles the viewer can see (others' private pins excluded).
    article_count: int
    # Articles others published since the viewer last opened the project.
    unseen_count: int = 0
    # The viewer's per-project push mute.
    is_muted: bool = False
    created_at: datetime


class ProjectMembershipIn(BaseModel):
    is_muted: bool


class ProjectMemberAddIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)


# The ticket workflow an article moves through inside a project. Extend this
# literal (and the STATUSES list in frontend/lib/api.ts) to add states — the
# status dropdown and filters render from those lists, nothing else changes.
ProjectTicketStatus = Literal["open", "done"]


def _http_link(value: str | None) -> str | None:
    value = value.strip() if value else None
    if value and not value.startswith(("http://", "https://")):
        raise ValueError("must be an http(s) URL")
    return value or None


class ProjectArticleAddIn(BaseModel):
    article_id: int
    is_shared: bool = False
    # Posted as the article's first thread comment, not stored on the pin.
    note: str | None = Field(default=None, max_length=4000)


class ProjectArticleUpdateIn(BaseModel):
    """PATCH semantics: only fields present are applied. Flipping is_shared
    on stamps shared_at; off clears it."""

    is_shared: bool | None = None


class ProjectCommentIn(BaseModel):
    body: str = Field(max_length=4000)
    link_url: str | None = Field(default=None, max_length=2000)

    _strip_body = field_validator("body")(_stripped_nonempty)
    _check_link = field_validator("link_url")(_http_link)


class ProjectCommentOut(BaseModel):
    id: int
    author: UserPublic
    body: str
    link_url: str | None
    created_at: datetime


class ProjectArticleStatusIn(BaseModel):
    status: ProjectTicketStatus
    # Optional resolution note ("done — merged in <PR>"), posted atomically
    # as a thread comment alongside the status change.
    comment: str | None = Field(default=None, max_length=4000)
    link_url: str | None = Field(default=None, max_length=2000)

    _check_link = field_validator("link_url")(_http_link)


class ProjectArticleStateOut(BaseModel):
    status: ProjectTicketStatus
    updated_by: UserPublic
    updated_at: datetime
    comment: ProjectCommentOut | None = None  # the resolution comment, if sent


class ProjectArticleOut(BaseModel):
    id: int
    project_id: int
    article: ArticleListItem
    added_by: UserPublic
    is_shared: bool
    shared_at: datetime | None
    created_at: datetime
    # Ticket state, shared per (project, article) across every pin of it.
    status: ProjectTicketStatus = "open"
    status_updated_by: UserPublic | None = None
    comment_count: int = 0


class ArticleProjectStatus(BaseModel):
    """Picker state for one of the viewer's projects against one article."""

    project_id: int
    project_name: str
    project_article_id: int | None  # the viewer's own pin, if any
    is_shared: bool | None  # the viewer's pin's flag
    shared_by_others: bool
    # Embedding similarity says this article belongs here (best match only).
    suggested: bool = False


# --- Messaging integrations ---

Platform = Literal["slack", "teams"]
TargetType = Literal["channel", "group", "dm", "chat"]


class IntegrationStatusOut(BaseModel):
    platform: Platform
    configured: bool  # server has client credentials for this platform
    connected: bool
    status: str | None = None  # 'active' | 'error' (needs reconnect)
    workspace_name: str | None = None
    account_name: str | None = None


class AuthorizeUrlOut(BaseModel):
    url: str


class TargetOptionOut(BaseModel):
    """One row in the live target picker (proxied from the platform API)."""

    external_id: str
    display_name: str
    target_type: TargetType
    meta: dict = {}
    saved_id: int | None = None  # ShareTarget id when already saved


class ShareTargetIn(BaseModel):
    platform: Platform
    external_id: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    target_type: TargetType
    meta: dict = {}


class ShareTargetOut(BaseModel):
    id: int
    platform: Platform
    external_id: str
    display_name: str
    target_type: TargetType
    meta: dict = {}
    last_used_at: datetime | None = None


class ExternalShareIn(BaseModel):
    """Send to either a saved target (target_id) or an ad-hoc one straight
    from the picker (target); exactly one must be provided."""

    article_id: int
    message: str = Field(default="", max_length=4000)
    target_id: int | None = None
    target: ShareTargetIn | None = None


class ExternalShareOut(BaseModel):
    id: int
    platform: Platform
    target_display: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ShareMessageIn(BaseModel):
    article_id: int
    draft: str = Field(default="", max_length=4000)
    tone: Literal["casual", "professional", "enthusiastic"] | None = None
    target_name: str | None = Field(default=None, max_length=255)


class ShareMessageOut(BaseModel):
    message: str


# --- AI ---

class AiStatusOut(BaseModel):
    configured: bool
    model: str | None
    search: bool = False  # web search/extract tools available to the Q&A agent
    search_provider: str | None = None  # "searxng" | "tavily" | None
    source: Literal["user", "system"] | None = None  # whose key interactive calls run on


AIProvider = Literal["openai", "anthropic", "custom"]


def _ai_base_url(value: str | None) -> str | None:
    """Unlike _http_link, empty stays empty — '' means 'not set' here."""
    if value is None:
        return None
    value = value.strip()
    if value and not value.startswith(("http://", "https://")):
        raise ValueError("must be an http(s) URL")
    return value


class AIImageSettingsIn(BaseModel):
    provider: AIProvider
    model: str = Field(min_length=1, max_length=120)
    # None keeps the stored key — or, when the provider matches the main one,
    # reuses the main key at call time.
    api_key: str | None = Field(default=None, max_length=512)
    base_url: str = Field(default="", max_length=2048)  # custom only

    _check_base_url = field_validator("base_url")(_ai_base_url)


class AISettingsIn(BaseModel):
    provider: AIProvider
    model: str = Field(min_length=1, max_length=120)
    api_key: str | None = Field(default=None, max_length=512)  # None = keep stored key
    base_url: str = Field(default="", max_length=2048)  # custom only
    image: AIImageSettingsIn | None = None  # None clears the image model

    _check_base_url = field_validator("base_url")(_ai_base_url)


class AIImageSettingsOut(BaseModel):
    provider: AIProvider
    model: str
    base_url: str = ""
    key_hint: str = ""


class AISettingsOut(BaseModel):
    configured: bool  # the user saved their own key
    system_available: bool  # a server-wide default exists to fall back to
    provider: AIProvider | None = None
    model: str | None = None
    base_url: str | None = None
    key_hint: str | None = None  # keys are write-only; this is all that comes back
    image: AIImageSettingsOut | None = None
    # Article image generation: whether any image model would serve this user
    # (their own block or the server-wide default), plus their prompt template.
    image_generation_available: bool = False
    image_prompt: str | None = None  # None = default_image_prompt applies
    default_image_prompt: str = ""


class AITestIn(BaseModel):
    """Values to test-drive; anything omitted falls back to the stored settings."""

    provider: AIProvider | None = None
    model: str | None = Field(default=None, max_length=120)
    api_key: str | None = Field(default=None, max_length=512)
    base_url: str | None = Field(default=None, max_length=2048)

    _check_base_url = field_validator("base_url")(_ai_base_url)


class AITestOut(BaseModel):
    ok: bool
    detail: str | None = None
    model: str | None = None


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


# --- Activity ---

ActivitySource = Literal["web", "mobile"]
ActivityRange = Literal["week", "month", "year"]


class HeartbeatIn(BaseModel):
    article_id: int
    # One heartbeat covers at most ~2 minutes so a stuck client can't inflate
    # the stats; well-behaved clients flush every ~30s.
    seconds: int = Field(ge=1, le=120)
    source: ActivitySource
    day: date  # client-local date, so "today" flips at the user's midnight


class ActivityDayOut(BaseModel):
    day: date
    seconds: int


class ActivityFeedOut(BaseModel):
    feed_id: int
    title: str
    seconds: int


class ActivityArticleOut(BaseModel):
    article_id: int
    title: str
    feed_title: str
    seconds: int


class ActivitySummaryOut(BaseModel):
    range: ActivityRange
    total_seconds: int
    prev_total_seconds: int  # same-length window immediately before; powers the delta
    days: list[ActivityDayOut]  # dense series oldest→newest, zero-filled
    streak_days: int
    top_feeds: list[ActivityFeedOut]
    top_articles: list[ActivityArticleOut]


# --- LLM usage (bring-your-own-key audit trail) ---

class UsageDayOut(BaseModel):
    day: date
    calls: int
    tokens: int  # prompt + completion


class UsageFeatureOut(BaseModel):
    feature: str
    calls: int
    tokens: int


class UsageModelOut(BaseModel):
    provider: str
    model: str
    calls: int
    tokens: int


class UsageSummaryOut(BaseModel):
    range: ActivityRange
    configured: bool  # the user currently has their own key saved
    total_calls: int
    total_tokens: int
    prev_total_tokens: int  # same-length window immediately before; powers the delta
    error_count: int
    days: list[UsageDayOut]  # dense series oldest→newest, zero-filled
    by_feature: list[UsageFeatureOut]
    by_model: list[UsageModelOut]


class UsageEventOut(BaseModel):
    id: int
    feature: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    duration_ms: int
    status: str
    error: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
