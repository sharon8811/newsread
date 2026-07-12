"""Feed fetching and parsing: JSON Feed + RSS/Atom, sanitized on ingest."""

import logging
import ipaddress
import html
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urljoin, urlsplit
import asyncio

import feedparser
import httpx
import nh3
from dateutil import parser as dateparser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .models import Article, Feed

logger = logging.getLogger(__name__)

USER_AGENT = "NewsRead/0.1 (+https://github.com/newsread)"

# hnrss-style boilerplate, e.g. "Points: 186" / "# Comments: 124"
_HN_POINTS_RE = re.compile(r"Points:\s*(\d+)")
_HN_COMMENTS_RE = re.compile(r"#\s*Comments:\s*(\d+)")
_HN_ITEM_URL_RE = re.compile(
    r"https?://news\.ycombinator\.com/item\?[^\s\"'<>]+", re.IGNORECASE
)


@dataclass
class ParsedArticle:
    guid: str
    url: str
    title: str
    content_html: str = ""
    author: str | None = None
    published_at: datetime | None = None
    image_url: str | None = None
    comments_url: str | None = None


@dataclass
class ParsedFeed:
    title: str
    site_url: str | None = None
    description: str | None = None
    articles: list[ParsedArticle] = field(default_factory=list)
    final_url: str | None = None
    content_type: str | None = None


class FeedParseError(ValueError):
    pass


class FeedRateLimited(Exception):
    """The publisher answered 429: the feed exists, it's just throttling us
    (reddit does this aggressively for server-side clients)."""

    def __init__(self, host: str):
        super().__init__(f"{host} is rate-limiting our requests right now")
        self.host = host


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _to_utc(dateparser.parse(value))
    except (ValueError, OverflowError):
        return None


def sanitize_html(html: str) -> str:
    return nh3.clean(html or "")


def strip_html(html: str) -> str:
    text = nh3.clean(html or "", tags=set())
    return re.sub(r"\s+", " ", text).strip()


def canonical_hn_comments_url(value: str | None) -> str | None:
    """Return a canonical HN item URL when *value* is exactly an HN thread.

    Matching the structured thread URL, rather than a feed title or hostname,
    lets filtered and transformed hnrss feeds retain HN-specific features.
    """
    if not value:
        return None
    try:
        parsed = urlsplit(html.unescape(value.strip()))
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or (parsed.hostname or "").rstrip(".").lower() != "news.ycombinator.com"
        or port is not None
        or parsed.path.rstrip("/") != "/item"
    ):
        return None
    item_ids = parse_qs(parsed.query).get("id") or []
    if not item_ids or not item_ids[0].isdigit() or int(item_ids[0]) <= 0:
        return None
    return f"https://news.ycombinator.com/item?id={int(item_ids[0])}"


def detect_comments_url(
    structured_url: str | None,
    article_url: str,
    content_html: str,
) -> str | None:
    """Keep generic comment links and recover HN threads from known formats."""
    if structured_url:
        return canonical_hn_comments_url(structured_url) or structured_url

    # Ask HN, Show HN and other self-posts use the discussion as the article.
    self_thread = canonical_hn_comments_url(article_url)
    if self_thread:
        return self_thread

    # Do not classify arbitrary HN links in normal article content. Only scan
    # content carrying hnrss's explicit discussion boilerplate.
    text = html.unescape(content_html or "")
    plain = strip_html(text)
    if "comments url:" not in plain.lower() and not (
        _HN_POINTS_RE.search(plain) and _HN_COMMENTS_RE.search(plain)
    ):
        return None
    for match in _HN_ITEM_URL_RE.finditer(text):
        canonical = canonical_hn_comments_url(match.group(0))
        if canonical:
            return canonical
    return None


def derive_excerpt(content_html: str, max_len: int = 320) -> str:
    text = strip_html(content_html)
    points = _HN_POINTS_RE.search(text)
    comments = _HN_COMMENTS_RE.search(text)
    if points and "Comments URL:" in text:
        parts = [f"{points.group(1)} points"]
        if comments:
            parts.append(f"{comments.group(1)} comments")
        return " · ".join(parts) + " · via Hacker News"
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


def parse_json_feed(data: dict) -> ParsedFeed:
    if not isinstance(data, dict):
        raise FeedParseError("The response is not a JSON Feed object")
    articles: list[ParsedArticle] = []
    for item in data.get("items", []):
        url = item.get("url") or item.get("external_url") or ""
        guid = str(item.get("id") or url)
        if not guid:
            continue
        content = item.get("content_html") or item.get("content_text") or ""
        author = None
        if isinstance(item.get("author"), dict):
            author = item["author"].get("name")
        elif item.get("authors"):
            author = item["authors"][0].get("name")
        external = item.get("external_url")
        articles.append(
            ParsedArticle(
                guid=guid,
                url=url,
                title=item.get("title") or strip_html(content)[:120] or url,
                content_html=content,
                author=author,
                published_at=_parse_date(item.get("date_published") or item.get("date_modified")),
                image_url=item.get("image") or item.get("banner_image"),
                # hnrss puts the article in `url` and the HN thread in
                # `external_url`; transformed feeds may leave it in content.
                comments_url=detect_comments_url(
                    external if external and external != url else None,
                    url,
                    content,
                ),
            )
        )
    return ParsedFeed(
        title=data.get("title") or "",
        site_url=data.get("home_page_url"),
        description=strip_html(data.get("description") or "") or None,
        articles=articles,
    )


def parse_xml_feed(text: str) -> ParsedFeed:
    parsed = feedparser.parse(text)
    articles: list[ParsedArticle] = []
    for entry in parsed.entries:
        url = entry.get("link") or ""
        guid = entry.get("id") or url
        if not guid:
            continue
        content = ""
        if entry.get("content"):
            content = entry.content[0].get("value", "")
        elif entry.get("summary"):
            content = entry.summary
        published = None
        for key in ("published_parsed", "updated_parsed"):
            if entry.get(key):
                published = datetime(*entry[key][:6], tzinfo=timezone.utc)
                break
        image_url = None
        for media in (entry.get("media_content") or []) + (entry.get("media_thumbnail") or []):
            if media.get("url"):
                image_url = media["url"]
                break
        if not image_url:
            for enclosure in entry.get("enclosures", []) or []:
                if str(enclosure.get("type", "")).startswith("image/") and enclosure.get("href"):
                    image_url = enclosure["href"]
                    break
        articles.append(
            ParsedArticle(
                guid=guid,
                url=url,
                title=entry.get("title") or url,
                content_html=content,
                author=entry.get("author"),
                published_at=published,
                image_url=image_url,
                comments_url=detect_comments_url(entry.get("comments"), url, content),
            )
        )
    feed_info = parsed.feed
    return ParsedFeed(
        title=feed_info.get("title") or "",
        site_url=feed_info.get("link"),
        description=strip_html(feed_info.get("subtitle") or "") or None,
        articles=articles,
    )


async def _validate_public_url(url: str) -> None:
    """Reject non-web and private-network feed targets, including DNS names."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise FeedParseError("Feed URL must use http or https")
    # Self-hosted deployments may legitimately subscribe to feeds on their
    # own LAN; they can turn the private-network guard off.
    if not settings.block_private_feed_urls:
        return
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise FeedParseError("Private network feed URLs are not allowed")
    try:
        literal = ipaddress.ip_address(hostname)
        addresses = [literal]
    except ValueError:
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
        except socket.gaierror as exc:
            raise FeedParseError("Feed hostname could not be resolved") from exc
        addresses = list({ipaddress.ip_address(info[4][0]) for info in infos})
    if any(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        for address in addresses
    ):
        raise FeedParseError("Private network feed URLs are not allowed")


async def _get_public_feed(url: str) -> httpx.Response:
    current = url
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=25, headers={"User-Agent": USER_AGENT}
    ) as client:
        for _ in range(6):
            await _validate_public_url(current)
            response = await client.get(current)
            if response.status_code == 429:
                raise FeedRateLimited(urlsplit(current).netloc)
            if response.status_code not in {301, 302, 303, 307, 308}:
                response.raise_for_status()
                return response
            location = response.headers.get("location")
            if not location:
                response.raise_for_status()
            current = urljoin(str(response.url), location)
    raise FeedParseError("Feed redirected too many times")


async def fetch_feed_data(url: str, *, require_articles: bool = False) -> ParsedFeed:
    response = await _get_public_feed(url)
    content_type = response.headers.get("content-type", "")
    body = response.text
    if "json" in content_type or body.lstrip().startswith("{"):
        parsed = parse_json_feed(response.json())
    else:
        parsed = parse_xml_feed(body)
    if not parsed.title and not parsed.articles:
        raise FeedParseError("The URL did not return a recognizable RSS, Atom, or JSON feed")
    if require_articles and not parsed.articles:
        raise FeedParseError("The feed currently contains no items")
    parsed.final_url = str(response.url)
    parsed.content_type = content_type.split(";", 1)[0].strip() or None
    return parsed


async def refresh_feed(
    session: AsyncSession, feed: Feed, *, require_articles: bool = False
) -> int:
    """Fetch a feed and insert new articles. Returns the number of new articles.

    Polling tolerates feeds that are temporarily empty (require_articles=False);
    subscribing rejects them so users don't add dead feeds."""
    parsed = await fetch_feed_data(feed.url, require_articles=require_articles)

    if parsed.title and not feed.title:
        feed.title = parsed.title
    if parsed.site_url and not feed.site_url:
        feed.site_url = parsed.site_url
    if parsed.description and not feed.description:
        feed.description = parsed.description

    guids = [a.guid for a in parsed.articles]
    existing: dict[str, Article] = {}
    if guids:
        rows = await session.scalars(
            select(Article).where(Article.feed_id == feed.id, Article.guid.in_(guids))
        )
        existing = {article.guid: article for article in rows}

    new_count = 0
    seen: set[str] = set()
    now = datetime.now(timezone.utc)
    for position, item in enumerate(parsed.articles):
        if item.guid in seen:
            continue
        seen.add(item.guid)
        if stored := existing.get(item.guid):
            # Metadata-only repair: parser improvements should benefit rows
            # that already exist without rewriting their article content.
            if not stored.comments_url and item.comments_url:
                stored.comments_url = item.comments_url[:2048]
            continue
        clean = sanitize_html(item.content_html)
        # Undated entries get a fetch-time fallback (offset by feed position so
        # feed order survives the sort) instead of NULL, which would pin them
        # below every dated article and let them shift on later refreshes.
        published_at = item.published_at or now - timedelta(seconds=position)
        session.add(
            Article(
                feed_id=feed.id,
                guid=item.guid[:1024],
                url=item.url[:2048],
                comments_url=item.comments_url[:2048] if item.comments_url else None,
                title=strip_html(item.title) or item.url,
                author=item.author[:255] if item.author else None,
                published_at=published_at,
                content_html=clean,
                excerpt=derive_excerpt(clean),
                image_url=item.image_url[:2048] if item.image_url else None,
            )
        )
        new_count += 1

    feed.last_fetched_at = datetime.now(timezone.utc)
    await session.commit()
    logger.info("Refreshed feed %s (%s): %d new articles", feed.id, feed.url, new_count)
    return new_count
