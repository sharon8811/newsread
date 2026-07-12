from datetime import datetime, timezone

import httpx
import pytest
import respx
from sqlalchemy import select, text

from app.fetcher import (
    FeedParseError,
    FeedRateLimited,
    ParsedArticle,
    canonical_hn_comments_url,
    detect_comments_url,
    derive_excerpt,
    fetch_feed_data,
    parse_json_feed,
    parse_xml_feed,
    refresh_feed,
    sanitize_html,
    strip_html,
    _parse_date,
    _to_utc,
    _validate_public_url,
)
from app.models import Article, Feed


# --- small helpers ---

def test_to_utc_naive_and_aware():
    naive = datetime(2024, 1, 1, 12, 0, 0)
    assert _to_utc(naive).tzinfo == timezone.utc
    assert _to_utc(None) is None


def test_parse_date_valid_and_invalid():
    assert _parse_date("2024-01-01T00:00:00Z").year == 2024
    assert _parse_date("") is None
    assert _parse_date("not a date") is None


def test_sanitize_and_strip_html():
    assert "<script>" not in sanitize_html("<script>evil()</script><p>ok</p>")
    assert strip_html("<p>hello   world</p>") == "hello world"
    assert strip_html("") == ""


def test_derive_excerpt_truncates():
    text = "word " * 200
    out = derive_excerpt(f"<p>{text}</p>", max_len=50)
    assert out.endswith("…")
    assert len(out) <= 50


def test_derive_excerpt_short_passthrough():
    assert derive_excerpt("<p>short</p>") == "short"


def test_derive_excerpt_hn_points_and_comments():
    html = "<p>Points: 186 # Comments: 124 Comments URL: https://news.ycombinator.com/x</p>"
    out = derive_excerpt(html)
    assert "186 points" in out
    assert "124 comments" in out
    assert "via Hacker News" in out


def test_derive_excerpt_hn_points_no_comments():
    html = "<p>Points: 5 Comments URL: https://news.ycombinator.com/x</p>"
    out = derive_excerpt(html)
    assert out == "5 points · via Hacker News"


def test_canonical_hn_comments_url_is_strict():
    assert canonical_hn_comments_url(
        "http://news.ycombinator.com/item?foo=x&id=0042#reply"
    ) == "https://news.ycombinator.com/item?id=42"
    assert canonical_hn_comments_url("https://evil.example/item?id=42") is None
    assert canonical_hn_comments_url("https://news.ycombinator.com/user?id=42") is None
    assert canonical_hn_comments_url("https://news.ycombinator.com/item?id=nope") is None
    assert canonical_hn_comments_url("https://news.ycombinator.com:444/item?id=42") is None


def test_detect_comments_url_from_hnrss_content_only():
    content = (
        '<p>Points: 12 # Comments: 3 Comments URL: '
        '<a href="https://news.ycombinator.com/item?id=123&amp;ref=x">thread</a></p>'
    )
    assert detect_comments_url(None, "https://example.com/story", content) == (
        "https://news.ycombinator.com/item?id=123"
    )
    assert detect_comments_url(
        None,
        "https://example.com/story",
        '<a href="https://news.ycombinator.com/item?id=123">unrelated link</a>',
    ) is None


def test_detect_comments_url_prefers_link_after_label():
    # Self-post bodies may link other HN threads; only the link following the
    # hnrss "Comments URL:" boilerplate is this article's own discussion.
    content = (
        '<p>See <a href="https://news.ycombinator.com/item?id=555">another thread</a></p>'
        "<p>Points: 12 # Comments: 3 Comments URL: "
        '<a href="https://news.ycombinator.com/item?id=123">thread</a></p>'
    )
    assert detect_comments_url(None, "https://example.com/story", content) == (
        "https://news.ycombinator.com/item?id=123"
    )


def test_detect_comments_url_for_hn_self_post():
    assert detect_comments_url(
        None, "https://news.ycombinator.com/item?id=88", ""
    ) == "https://news.ycombinator.com/item?id=88"


# --- JSON Feed ---

def test_parse_json_feed_basic():
    feed = parse_json_feed({
        "title": "My Feed",
        "home_page_url": "https://example.com",
        "description": "desc",
        "items": [
            {
                "id": "1", "url": "https://example.com/a", "title": "A",
                "content_html": "<p>body</p>", "author": {"name": "Jo"},
                "date_published": "2024-01-01T00:00:00Z", "image": "https://x/i.png",
            },
        ],
    })
    assert feed.title == "My Feed"
    assert feed.site_url == "https://example.com"
    assert len(feed.articles) == 1
    art = feed.articles[0]
    assert art.author == "Jo"
    assert art.image_url == "https://x/i.png"


def test_parse_json_feed_authors_list_and_external_url():
    feed = parse_json_feed({
        "items": [{
            "id": "x", "url": "https://example.com/story",
            "external_url": "https://news.ycombinator.com/item?id=1",
            "content_text": "just text", "authors": [{"name": "Al"}],
            "date_modified": "2024-02-02T00:00:00Z", "banner_image": "https://b/i.png",
        }],
    })
    art = feed.articles[0]
    assert art.author == "Al"
    assert art.comments_url == "https://news.ycombinator.com/item?id=1"
    assert art.image_url == "https://b/i.png"


def test_parse_json_feed_title_falls_back_to_content():
    feed = parse_json_feed({"items": [{"id": "1", "url": "u", "content_html": "<p>Hello world body</p>"}]})
    assert feed.articles[0].title == "Hello world body"


def test_parse_json_feed_recovers_hn_thread_from_content():
    feed = parse_json_feed({
        "items": [{
            "id": "1",
            "url": "https://example.com/story",
            "content_html": (
                "<p>Points: 7 # Comments: 2 Comments URL: "
                "https://news.ycombinator.com/item?id=91</p>"
            ),
        }],
    })
    assert feed.articles[0].comments_url == "https://news.ycombinator.com/item?id=91"


def test_parse_json_feed_skips_item_without_guid():
    feed = parse_json_feed({"items": [{"title": "no id or url"}]})
    # guid becomes "" -> skipped
    assert feed.articles == []


# --- XML Feed ---

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>RSS Feed</title><link>https://site.example</link><description>sub</description>
  <item>
    <title>Post One</title><link>https://site.example/1</link>
    <guid>guid-1</guid><author>writer@x.com</author>
    <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
    <description>&lt;p&gt;summary body&lt;/p&gt;</description>
    <comments>https://site.example/1/comments</comments>
  </item>
</channel></rss>"""


def test_parse_xml_feed_basic():
    feed = parse_xml_feed(RSS)
    assert feed.title == "RSS Feed"
    assert feed.site_url == "https://site.example"
    art = feed.articles[0]
    assert art.title == "Post One"
    assert art.guid == "guid-1"
    assert art.comments_url == "https://site.example/1/comments"
    assert art.published_at.year == 2024


def test_parse_xml_feed_media_content_image():
    xml = """<?xml version="1.0"?>
    <rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>F</title>
      <item><title>T</title><link>https://x/1</link>
        <media:content url="https://x/pic.jpg"/></item>
    </channel></rss>"""
    feed = parse_xml_feed(xml)
    assert feed.articles[0].image_url == "https://x/pic.jpg"


def test_parse_xml_feed_enclosure_image():
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel><title>F</title>
      <item><title>T</title><link>https://x/1</link>
        <enclosure url="https://x/pic.jpg" type="image/jpeg"/></item>
    </channel></rss>"""
    feed = parse_xml_feed(xml)
    assert feed.articles[0].image_url == "https://x/pic.jpg"


def test_parse_xml_feed_content_over_summary():
    xml = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/">
      <title>Atom</title>
      <entry><title>E</title><id>atom-1</id><link href="https://a/1"/>
        <content type="html">&lt;p&gt;full content&lt;/p&gt;</content>
        <updated>2024-03-03T00:00:00Z</updated>
      </entry>
    </feed>"""
    feed = parse_xml_feed(xml)
    art = feed.articles[0]
    assert "full content" in art.content_html
    assert art.published_at.month == 3


# --- fetch_feed_data ---

@respx.mock
async def test_fetch_feed_data_json():
    respx.get("https://feed.example/json").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"title": "JSON Feed", "items": []},
        )
    )
    feed = await fetch_feed_data("https://feed.example/json")
    assert feed.title == "JSON Feed"


@respx.mock
async def test_fetch_feed_data_json_by_body_sniff():
    respx.get("https://feed.example/x").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/plain"},
                                    text='{"title": "Sniffed", "items": []}')
    )
    feed = await fetch_feed_data("https://feed.example/x")
    assert feed.title == "Sniffed"


@respx.mock
async def test_fetch_feed_data_xml():
    respx.get("https://feed.example/rss").mock(
        return_value=httpx.Response(200, headers={"content-type": "application/rss+xml"}, text=RSS)
    )
    feed = await fetch_feed_data("https://feed.example/rss")
    assert feed.title == "RSS Feed"


@respx.mock
async def test_fetch_feed_data_raises_on_http_error():
    respx.get("https://feed.example/bad").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await fetch_feed_data("https://feed.example/bad")


@respx.mock
async def test_fetch_feed_data_raises_rate_limited_on_429():
    respx.get("https://www.reddit.com/r/x/.rss").mock(return_value=httpx.Response(429))
    with pytest.raises(FeedRateLimited, match="www.reddit.com"):
        await fetch_feed_data("https://www.reddit.com/r/x/.rss")


@respx.mock
async def test_fetch_feed_data_rejects_empty_when_required():
    respx.get("https://feed.example/empty").mock(
        return_value=httpx.Response(200, json={"title": "Empty", "items": []})
    )
    with pytest.raises(FeedParseError, match="no items"):
        await fetch_feed_data("https://feed.example/empty", require_articles=True)


async def test_private_feed_targets_are_rejected():
    with pytest.raises(FeedParseError, match="Private network"):
        await _validate_public_url("http://127.0.0.1:8000/private")


async def test_private_feed_guard_can_be_disabled(monkeypatch):
    monkeypatch.setattr("app.fetcher.settings.block_private_feed_urls", False)
    await _validate_public_url("http://127.0.0.1:8000/private")  # does not raise


# --- refresh_feed (DB) ---

@respx.mock
async def test_refresh_feed_tolerates_empty_feed(session):
    """Polling must not error on a feed that is temporarily empty; only
    subscribing (require_articles=True) rejects it."""
    feed = Feed(url="https://feed.example/empty", title="Empty")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)

    respx.get("https://feed.example/empty").mock(
        return_value=httpx.Response(200, json={"title": "Empty", "items": []})
    )
    assert await refresh_feed(session, feed) == 0
    assert feed.last_fetched_at is not None


@respx.mock
async def test_refresh_feed_inserts_new_articles(session):
    feed = Feed(url="https://feed.example/rss")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)

    respx.get("https://feed.example/rss").mock(
        return_value=httpx.Response(200, headers={"content-type": "application/xml"}, text=RSS)
    )
    count = await refresh_feed(session, feed)
    assert count == 1
    assert feed.title == "RSS Feed"  # backfilled
    assert feed.last_fetched_at is not None

    arts = (await session.scalars(select(Article).where(Article.feed_id == feed.id))).all()
    assert len(arts) == 1
    assert arts[0].title == "Post One"


@respx.mock
async def test_refresh_feed_dedupes_existing(session):
    feed = Feed(url="https://feed.example/rss", title="Existing")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    session.add(Article(feed_id=feed.id, guid="guid-1", url="https://site.example/1", title="Old"))
    await session.commit()

    respx.get("https://feed.example/rss").mock(
        return_value=httpx.Response(200, headers={"content-type": "application/xml"}, text=RSS)
    )
    count = await refresh_feed(session, feed)
    assert count == 0  # guid-1 already present


@respx.mock
async def test_refresh_feed_repairs_missing_comments_url(session):
    feed = Feed(url="https://feed.example/rss", title="Existing")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    article = Article(
        feed_id=feed.id,
        guid="guid-1",
        url="https://site.example/1",
        title="Old",
        comments_url=None,
    )
    session.add(article)
    await session.commit()

    respx.get("https://feed.example/rss").mock(
        return_value=httpx.Response(200, headers={"content-type": "application/xml"}, text=RSS)
    )
    assert await refresh_feed(session, feed) == 0
    await session.refresh(article)
    assert article.comments_url == "https://site.example/1/comments"


async def test_hn_comments_url_backfill_migration(session):
    from app.db import ONE_SHOT_MIGRATIONS

    feed = Feed(url="https://feed.example/hn")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    self_post = Article(
        feed_id=feed.id, guid="g1", url="https://news.ycombinator.com/item?id=42", title="t"
    )
    boilerplate = Article(
        feed_id=feed.id,
        guid="g2",
        url="https://example.com/story",
        title="t",
        content_html=(
            '<p>See <a href="https://news.ycombinator.com/item?id=555">other</a></p>'
            "<p>Comments URL: "
            '<a href="https://news.ycombinator.com/item?id=123">thread</a></p>'
        ),
    )
    plain_link = Article(
        feed_id=feed.id,
        guid="g3",
        url="https://example.com/other",
        title="t",
        content_html='<a href="https://news.ycombinator.com/item?id=9">link</a>',
    )
    session.add_all([self_post, boilerplate, plain_link])
    await session.commit()

    for statement in ONE_SHOT_MIGRATIONS["backfill_hn_comments_url"]:
        await session.execute(text(statement))
    await session.commit()
    for article in (self_post, boilerplate, plain_link):
        await session.refresh(article)
    assert self_post.comments_url == "https://news.ycombinator.com/item?id=42"
    assert boilerplate.comments_url == "https://news.ycombinator.com/item?id=123"
    assert plain_link.comments_url is None


@respx.mock
async def test_refresh_feed_undated_gets_fallback_time(session):
    feed = Feed(url="https://feed.example/nodate")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)

    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel><title>F</title>
      <item><title>No Date</title><link>https://x/nd</link><guid>nd-1</guid></item>
    </channel></rss>"""
    respx.get("https://feed.example/nodate").mock(
        return_value=httpx.Response(200, headers={"content-type": "application/xml"}, text=xml)
    )
    await refresh_feed(session, feed)
    art = await session.scalar(select(Article).where(Article.guid == "nd-1"))
    assert art.published_at is not None
