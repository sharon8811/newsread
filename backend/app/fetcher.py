"""Feed fetching and parsing: JSON Feed + RSS/Atom, sanitized on ingest."""

import logging
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit
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
                # hnrss puts the article in `url` and the HN thread in `external_url`
                comments_url=external if external and external != url else None,
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
                comments_url=entry.get("comments"),
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
    existing = set()
    if guids:
        rows = await session.execute(
            select(Article.guid).where(Article.feed_id == feed.id, Article.guid.in_(guids))
        )
        existing = {row[0] for row in rows}

    new_count = 0
    seen: set[str] = set()
    now = datetime.now(timezone.utc)
    for position, item in enumerate(parsed.articles):
        if item.guid in existing or item.guid in seen:
            continue
        seen.add(item.guid)
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
