from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


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

    model_config = {"from_attributes": True}


class UserPublic(BaseModel):
    id: int
    username: str
    name: str

    model_config = {"from_attributes": True}


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# --- Feeds ---

class AddFeedIn(BaseModel):
    url: str = Field(min_length=4, max_length=2048)


class FeedOut(BaseModel):
    id: int
    url: str
    title: str
    site_url: str | None
    description: str | None
    last_fetched_at: datetime | None
    article_count: int
    unread_count: int


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
    is_read: bool
    is_saved: bool
    summary: str = ""


class ArticleDetail(ArticleListItem):
    content_html: str
    summary_model: str | None = None


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


class SummaryOut(BaseModel):
    summary: str
    model: str | None
    generated_at: datetime | None


class AskIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}
