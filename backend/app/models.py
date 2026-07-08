from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(128))
    default_view: Mapped[str] = mapped_column(String(16), default="cards", server_default="cards")
    # Template for generated article images ({article_title}/{article_excerpt}
    # tags); NULL = image_gen.DEFAULT_IMAGE_PROMPT.
    image_prompt: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserAISettings(Base):
    """A user's own LLM configuration ("bring your own key"). No row means the
    user rides the server-wide default from config.py. API keys are
    Fernet-encrypted at rest (crypto.py) and never leave the backend —
    responses only carry `key_hint` (last characters). The image_* block is an
    optional second model for generating pictures for imageless articles; its
    key falls back to the main one when the provider matches."""

    __tablename__ = "user_ai_settings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(16))  # 'openai' | 'anthropic' | 'custom'
    api_key_enc: Mapped[str] = mapped_column(Text)
    key_hint: Mapped[str] = mapped_column(String(8), default="")
    base_url: Mapped[str] = mapped_column(String(2048), default="")  # custom only
    model: Mapped[str] = mapped_column(String(120))
    image_provider: Mapped[str | None] = mapped_column(String(16))
    image_model: Mapped[str | None] = mapped_column(String(120))
    image_api_key_enc: Mapped[str | None] = mapped_column(Text)
    image_key_hint: Mapped[str | None] = mapped_column(String(8))
    image_base_url: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LLMUsage(Base):
    """One row per LLM call made with a user's own key — the audit trail behind
    the usage page. Calls on the server-wide default key are deliberately not
    logged here (that's the operator's bill, not the user's)."""

    __tablename__ = "llm_usage"
    __table_args__ = (Index("ix_llm_usage_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    feature: Mapped[str] = mapped_column(String(16))  # 'summary' | 'qa' | 'share' | 'image'
    provider: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(120))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(8), default="ok")  # 'ok' | 'error'
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Device(Base):
    """A mobile device registered for push notifications. Tokens are Expo push
    tokens, which cover both iOS and Android; a token that logs into another
    account is reassigned (one physical device, one owner at a time)."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    push_token: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    platform: Mapped[str] = mapped_column(String(16))  # 'ios' | 'android'
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Feed(Base):
    """Global: one row per feed URL, fetched once no matter how many subscribers."""

    __tablename__ = "feeds"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    site_url: Mapped[str | None] = mapped_column(String(2048))
    description: Mapped[str | None] = mapped_column(Text)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_interval_minutes: Mapped[int] = mapped_column(Integer, default=15)
    # Global switch: skip auto-summaries/embeddings for this feed (shared by
    # all subscribers; on-demand summaries in the article view still work).
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    articles: Mapped[list["Article"]] = relationship(back_populates="feed", cascade="all, delete-orphan")


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "feed_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("feeds.id", ondelete="CASCADE"), index=True)
    view_override: Mapped[str | None] = mapped_column(String(16))
    # Per-user feed settings; NULL means "inherit the default".
    title_override: Mapped[str | None] = mapped_column(String(512))
    sort_order: Mapped[str | None] = mapped_column(String(16))  # 'oldest'; NULL = newest
    retention_days: Mapped[int | None] = mapped_column(Integer)  # NULL = keep forever
    is_muted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    feed: Mapped[Feed] = relationship()


class Article(Base):
    """Global: one row per (feed, guid); per-user state lives in UserArticleState."""

    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("feed_id", "guid"),
        Index("ix_articles_feed_published", "feed_id", "published_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("feeds.id", ondelete="CASCADE"), index=True)
    guid: Mapped[str] = mapped_column(String(1024))
    url: Mapped[str] = mapped_column(String(2048))
    comments_url: Mapped[str | None] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    content_html: Mapped[str] = mapped_column(Text, default="")
    excerpt: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str | None] = mapped_column(String(2048))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    full_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    full_text_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_short: Mapped[str] = mapped_column(Text, default="", server_default="")
    summary_medium: Mapped[str] = mapped_column(Text, default="", server_default="")
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    summary_model: Mapped[str | None] = mapped_column(String(120))
    summary_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entities_extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Claim marker for lazy image generation (set once, attempt-once policy);
    # doubles as the "in flight" signal while image_url is still NULL.
    image_gen_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    feed: Mapped[Feed] = relationship(back_populates="articles")


class GeneratedImage(Base):
    """AI-generated illustration for an article without one, stored as bytes
    in Postgres (no media volume to configure at this scale) and served by
    GET /articles/{id}/generated-image."""

    __tablename__ = "generated_images"

    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    content_type: Mapped[str] = mapped_column(String(64), default="image/png")
    data: Mapped[bytes] = mapped_column(LargeBinary)
    model: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArticleEmbedding(Base):
    """One vector per article for semantic search, embedded from title +
    summary/excerpt. `model` records the embedding model so a model switch
    re-embeds; queries must filter on it (dimensions may differ across models).
    The vector column is dimension-less on purpose: search does exact scans
    (no ANN index needed at this scale), and the dimension follows whatever
    model the OpenAI-compatible endpoint serves."""

    __tablename__ = "article_embeddings"

    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(String(120))
    embedding: Mapped[list] = mapped_column(Vector())
    embedded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Entity(Base):
    """Global: one row per external resource (repo, model, paper…), shared by
    every article that links to it. `data` holds the latest normalized payload."""

    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("kind", "canonical_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    canonical_key: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(2048))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EntitySnapshot(Base):
    """Append-only history: written on first fetch and whenever `data` changes."""

    __tablename__ = "entity_snapshots"
    __table_args__ = (Index("ix_entity_snapshots_entity_captured", "entity_id", "captured_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    data: Mapped[dict] = mapped_column(JSONB)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArticleEntity(Base):
    __tablename__ = "article_entities"
    __table_args__ = (UniqueConstraint("article_id", "entity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(8))  # 'primary' | 'inline'
    position: Mapped[int] = mapped_column(Integer, default=0)


class UserArticleState(Base):
    __tablename__ = "user_article_states"
    __table_args__ = (UniqueConstraint("user_id", "article_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    is_saved: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReadingActivity(Base):
    """Time a user spent reading one article on one day, split by client kind.
    Heartbeats increment `seconds`; `day` is the client's local date so "today"
    on the activity page flips at the user's midnight, not UTC's. Kept
    per-article (not just per-day) so interests can be inferred later."""

    __tablename__ = "reading_activity"
    __table_args__ = (
        UniqueConstraint("user_id", "article_id", "day", "source"),
        Index("ix_reading_activity_user_day", "user_id", "day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(8))  # 'web' | 'mobile'
    seconds: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Share(Base):
    __tablename__ = "shares"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    from_user: Mapped[User] = relationship()
    article: Mapped[Article] = relationship()
    recipients: Mapped[list["ShareRecipient"]] = relationship(
        back_populates="share", cascade="all, delete-orphan"
    )


class Conversation(Base):
    """One Q&A thread per (article, user) — or per (project, user) when the
    chat spans a project's collection (then article_id is NULL). The
    (project_id, user_id) uniqueness lives in a partial index (db.MIGRATIONS)."""

    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("article_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True, nullable=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(12))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    # Web tool calls made while answering: [{name, args, summary}], null for user
    # messages and tool-less answers.
    tool_events: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Project(Base):
    """A workspace articles get pinned to. A project with one member is a
    personal collection; sharing is what happens when membership grows."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    owner: Mapped[User] = relationship()
    members: Mapped[list["ProjectMember"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(12), default="member")  # 'owner' | 'member'
    # Powers the "new since last visit" badge.
    last_visited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Per-member push mute for this project's publish notifications.
    is_muted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


class ProjectArticle(Base):
    """An article pinned to a project. Private pins (is_shared=False) are
    visible only to their adder — every query joining this table must filter
    `is_shared OR added_by_user_id == viewer`. Two members may each pin the
    same article, hence the three-column uniqueness. The shared feed orders by
    shared_at (publish time), so an old private pin published today surfaces."""

    __tablename__ = "project_articles"
    __table_args__ = (UniqueConstraint("project_id", "article_id", "added_by_user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    added_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False)
    shared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Legacy — notes now live in ProjectArticleComment. The column stays so the
    # db.MIGRATIONS backfill can reference it on fresh databases; always NULL.
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped[Project] = relationship()
    article: Mapped[Article] = relationship()
    added_by: Mapped[User] = relationship()


class ProjectArticleState(Base):
    """Shared ticket status of one article within one project. Keyed by
    (project, article), not by pin: the project page groups pins of the same
    article into one card, and "done" must mean done for everyone. No row
    means "open" — pinning never writes here."""

    __tablename__ = "project_article_states"
    __table_args__ = (UniqueConstraint("project_id", "article_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    # Allowed values are the schema's ProjectTicketStatus literal — extend there.
    status: Mapped[str] = mapped_column(String(16), default="open")
    updated_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    updated_by: Mapped[User] = relationship()


class ProjectArticleComment(Base):
    """One comment on an article's thread within a project. Threads are per
    (project, article) like the grouped card and the ticket status — anyone
    who can see the article in the project sees the whole thread, including
    comments that began life as private-pin notes (the backfill in
    db.MIGRATIONS folded legacy ProjectArticle.note values in here)."""

    __tablename__ = "project_article_comments"
    __table_args__ = (Index("ix_project_article_comments_thread", "project_id", "article_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text)
    link_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    author: Mapped[User] = relationship()


class ShareRecipient(Base):
    __tablename__ = "share_recipients"
    __table_args__ = (UniqueConstraint("share_id", "to_user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    share_id: Mapped[int] = mapped_column(ForeignKey("shares.id", ondelete="CASCADE"), index=True)
    to_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    share: Mapped[Share] = relationship(back_populates="recipients")
    to_user: Mapped[User] = relationship()


class MessagingConnection(Base):
    """A user's OAuth link to a messaging platform (a Slack workspace, a Teams
    tenant). Tokens are Fernet-encrypted at rest (crypto.py). Slack user tokens
    don't expire (rotation off), so refresh_token/token_expires_at stay NULL;
    Teams access tokens live ~1h and refresh on use."""

    __tablename__ = "messaging_connections"
    __table_args__ = (UniqueConstraint("user_id", "platform"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(16))  # 'slack' | 'teams'
    external_account_id: Mapped[str] = mapped_column(String(255), default="")
    account_name: Mapped[str] = mapped_column(String(255), default="")
    workspace_id: Mapped[str] = mapped_column(String(255), default="")
    workspace_name: Mapped[str] = mapped_column(String(255), default="")
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[str] = mapped_column(Text, default="")
    # 'error' means sends fail with an auth problem and the user must reconnect.
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    targets: Mapped[list["ShareTarget"]] = relationship(
        back_populates="connection", cascade="all, delete-orphan"
    )


class ShareTarget(Base):
    """A saved quick-share destination (channel/chat) on a connection. `meta`
    holds platform extras a send needs beyond the id (Teams channels need
    their team_id)."""

    __tablename__ = "share_targets"
    __table_args__ = (UniqueConstraint("connection_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("messaging_connections.id", ondelete="CASCADE"), index=True
    )
    target_type: Mapped[str] = mapped_column(String(16))  # 'channel' | 'group' | 'dm' | 'chat'
    external_id: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    connection: Mapped[MessagingConnection] = relationship(back_populates="targets")


class ExternalShare(Base):
    """Log of one message sent (or attempted) to a messaging platform; powers
    the sent history and keeps delivery failures inspectable."""

    __tablename__ = "external_shares"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(16))
    target_display: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16))  # 'sent' | 'failed'
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
