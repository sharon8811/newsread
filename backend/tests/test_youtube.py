"""Channel resolution and caption fetching, with the network mocked."""

import types

import httpx
import pytest
import respx

from app import youtube

CHANNEL_ID = "UCkVfrGwV-iG9bSsgCbrNPxQ"
OTHER_ID = "UCBJycsmduvYEL83R_U4JriQ"
CHANNEL_PAGE = f"""<!doctype html><html><head>
<link rel="alternate" type="application/rss+xml" title="RSS"
 href="https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}">
<meta property="og:title" content="Better Stack">
<meta property="og:description" content="30x cheaper than Datadog.">
</head><body></body></html>"""


@pytest.fixture(autouse=True)
def _clear_cache():
    youtube._resolve_cache.clear()
    youtube._blocked_until = 0.0
    yield
    youtube._resolve_cache.clear()
    youtube._blocked_until = 0.0


@pytest.fixture
def no_api_key(monkeypatch):
    monkeypatch.setattr(youtube.settings, "youtube_api_key", "")


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setattr(youtube.settings, "youtube_api_key", "test-key")


def test_video_id_forms():
    assert youtube.video_id("https://www.youtube.com/watch?v=RsR6cbovMfI") == "RsR6cbovMfI"
    assert youtube.video_id("https://youtu.be/RsR6cbovMfI?t=30") == "RsR6cbovMfI"
    assert youtube.video_id("https://www.youtube.com/shorts/RsR6cbovMfI") == "RsR6cbovMfI"
    assert youtube.video_id("https://m.youtube.com/embed/RsR6cbovMfI") == "RsR6cbovMfI"
    assert youtube.video_id("https://www.youtube.com/@mkbhd") is None
    assert youtube.video_id("https://example.com/watch?v=RsR6cbovMfI") is None
    assert youtube.video_id("https://www.youtube.com/watch?v=too-short") is None
    assert youtube.video_id("") is None
    assert youtube.video_id("http://[::1") is None


def test_channel_feed_url():
    assert youtube.channel_feed_url(CHANNEL_ID) == (
        f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
    )


async def test_resolve_direct_forms_never_touch_the_network(no_api_key):
    for raw in (
        CHANNEL_ID,
        f"https://www.youtube.com/channel/{CHANNEL_ID}",
        f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}",
    ):
        resolved = await youtube.resolve_channel(raw)
        assert resolved.match.channel_id == CHANNEL_ID
        assert resolved.match.feed_url.endswith(CHANNEL_ID)
        assert resolved.alternatives == ()


def _stub_pages(monkeypatch, pages: dict):
    """Channel pages come through Scrapling (browser impersonation), so they
    are stubbed at the fetcher, not with respx. Values are (status, html) or
    an exception to raise. Returns the list of URLs fetched."""
    fetched: list[str] = []

    async def fake_get(url, **kwargs):
        assert kwargs.get("impersonate") == "chrome"
        fetched.append(url)
        result = pages[url]
        if isinstance(result, Exception):
            raise result
        status, html = result
        return types.SimpleNamespace(status=status, html_content=html)

    monkeypatch.setattr(youtube.AsyncFetcher, "get", staticmethod(fake_get))
    return fetched


async def test_resolve_handle_reads_the_page_feed_link(no_api_key, monkeypatch):
    fetched = _stub_pages(
        monkeypatch, {"https://www.youtube.com/@betterstack": (200, CHANNEL_PAGE)}
    )
    for raw in (
        "@betterstack",
        "https://www.youtube.com/@betterstack",
        "youtube.com/c/betterstack",
    ):
        resolved = await youtube.resolve_channel(raw)
        assert resolved.match.channel_id == CHANNEL_ID
        assert resolved.match.title == "Better Stack"
        assert resolved.match.description == "30x cheaper than Datadog."
    # Three inputs, three lookups: the cache is keyed on what was typed.
    assert len(fetched) == 3
    # A repeat of the same input is served from the cache.
    await youtube.resolve_channel("@betterstack")
    assert len(fetched) == 3


async def test_resolve_handle_page_failures(no_api_key, monkeypatch):
    _stub_pages(
        monkeypatch,
        {
            "https://www.youtube.com/@ghost": (404, ""),
            "https://www.youtube.com/@empty": (200, "<html>no feed link here</html>"),
            "https://www.youtube.com/@boom": (500, ""),
            "https://www.youtube.com/@offline": httpx.ConnectError("down"),
        },
    )
    with pytest.raises(ValueError, match="No YouTube channel found"):
        await youtube.resolve_channel("@ghost")
    with pytest.raises(ValueError, match="No YouTube channel found"):
        await youtube.resolve_channel("@empty")
    with pytest.raises(ValueError, match="did not return"):
        await youtube.resolve_channel("@boom")
    with pytest.raises(ValueError, match="Could not reach YouTube"):
        await youtube.resolve_channel("@offline")


@respx.mock
async def test_resolve_handle_uses_the_data_api_when_configured(api_key):
    route = respx.get("https://www.googleapis.com/youtube/v3/channels").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"id": CHANNEL_ID, "snippet": {"title": "Better Stack", "description": "Logs"}}
                ]
            },
        )
    )
    resolved = await youtube.resolve_channel("@betterstack")
    assert resolved.match == youtube.ChannelMatch(CHANNEL_ID, "Better Stack", "Logs")
    assert route.calls[0].request.url.params["forHandle"] == "@betterstack"
    assert route.calls[0].request.url.params["key"] == "test-key"


@respx.mock
async def test_resolve_falls_back_to_the_page_when_the_api_knows_no_handle(api_key, monkeypatch):
    # /c/ and /user/ vanity names are not handles; their page still advertises
    # the feed, so an empty API answer must not dead-end.
    respx.get("https://www.googleapis.com/youtube/v3/channels").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    _stub_pages(monkeypatch, {"https://www.youtube.com/@betterstack": (200, CHANNEL_PAGE)})
    resolved = await youtube.resolve_channel("https://www.youtube.com/user/betterstack")
    assert resolved.match.channel_id == CHANNEL_ID


@respx.mock
async def test_data_api_errors_surface_as_user_facing_values(api_key):
    respx.get("https://www.googleapis.com/youtube/v3/search").mock(
        return_value=httpx.Response(403, json={"error": {"message": "quota exceeded"}})
    )
    with pytest.raises(ValueError, match="rejected that lookup"):
        await youtube.resolve_channel("Better Stack")

    respx.get("https://www.googleapis.com/youtube/v3/search").mock(
        return_value=httpx.Response(500, text="not json")
    )
    with pytest.raises(ValueError, match="rejected that lookup"):
        await youtube.resolve_channel("Other Name")


@respx.mock
async def test_search_by_name_offers_alternatives(api_key):
    respx.get("https://www.googleapis.com/youtube/v3/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": {"channelId": CHANNEL_ID},
                        "snippet": {"title": "Better Stack", "description": " Logs "},
                    },
                    {"id": {"channelId": OTHER_ID}, "snippet": {"title": "Life Stack"}},
                    {"id": {"kind": "youtube#video"}, "snippet": {"title": "not a channel"}},
                ]
            },
        )
    )
    resolved = await youtube.resolve_channel("Better Stack")
    assert resolved.match.channel_id == CHANNEL_ID
    assert resolved.match.description == "Logs"
    assert [alternative.channel_id for alternative in resolved.alternatives] == [OTHER_ID]


@respx.mock
async def test_search_with_no_results(api_key):
    respx.get("https://www.googleapis.com/youtube/v3/search").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    with pytest.raises(ValueError, match="No YouTube channel found"):
        await youtube.resolve_channel("nobody at all")


async def test_search_by_name_needs_a_key(no_api_key):
    with pytest.raises(ValueError, match="needs a YouTube API key"):
        await youtube.resolve_channel("Better Stack")


async def test_resolve_rejects_input_that_is_not_a_channel(no_api_key):
    with pytest.raises(ValueError, match="Enter a YouTube channel"):
        await youtube.resolve_channel("   ")
    with pytest.raises(ValueError, match="does not look like"):
        await youtube.resolve_channel("x" * 201)
    with pytest.raises(ValueError, match="single video"):
        await youtube.resolve_channel("https://www.youtube.com/watch?v=RsR6cbovMfI")
    with pytest.raises(ValueError, match="does not point at a channel"):
        await youtube.resolve_channel("https://www.youtube.com/results?search_query=x")
    with pytest.raises(ValueError, match="does not point at a channel"):
        await youtube.resolve_channel("youtube.com/")


def _snippets(*texts):
    return [types.SimpleNamespace(text=text) for text in texts]


def _track(is_generated, *texts):
    return types.SimpleNamespace(is_generated=is_generated, fetch=lambda: _snippets(*texts))


def _stub_api(monkeypatch, tracks, error=None):
    """Swap the library for a stub. Its real exception types are reused so the
    module's except clause is exercised as written."""
    import sys

    from youtube_transcript_api import IpBlocked, RequestBlocked, YouTubeRequestFailed

    class FakeApi:
        def list(self, video):
            if error is not None:
                raise error
            assert video == "RsR6cbovMfI"
            return tracks

    module = types.SimpleNamespace(
        YouTubeTranscriptApi=FakeApi,
        IpBlocked=IpBlocked,
        RequestBlocked=RequestBlocked,
        YouTubeRequestFailed=YouTubeRequestFailed,
    )
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", module)


async def test_fetch_transcript_prefers_a_human_written_track(monkeypatch):
    _stub_api(monkeypatch, [_track(True, "auto", "text"), _track(False, "written  ", "\ntext")])
    assert await youtube.fetch_transcript("RsR6cbovMfI") == "written text"


async def test_fetch_transcript_falls_back_to_captions_that_exist(monkeypatch):
    _stub_api(monkeypatch, [_track(True, "auto", "text")])
    assert await youtube.fetch_transcript("RsR6cbovMfI") == "auto text"


async def test_fetch_transcript_without_captions(monkeypatch):
    _stub_api(monkeypatch, [])
    assert await youtube.fetch_transcript("RsR6cbovMfI") == ""


async def test_fetch_transcript_swallows_youtube_refusals(monkeypatch):
    _stub_api(monkeypatch, [], error=RuntimeError("transcripts disabled"))
    assert await youtube.fetch_transcript("RsR6cbovMfI") == ""


async def test_a_blocked_request_raises_and_pauses_further_attempts(monkeypatch):
    from youtube_transcript_api import RequestBlocked

    calls = 0

    class CountingApi:
        def list(self, video):
            nonlocal calls
            calls += 1
            raise RequestBlocked(video)

    import sys

    monkeypatch.setitem(
        sys.modules,
        "youtube_transcript_api",
        types.SimpleNamespace(
            YouTubeTranscriptApi=CountingApi,
            IpBlocked=Exception,
            RequestBlocked=RequestBlocked,
            YouTubeRequestFailed=Exception,
        ),
    )
    with pytest.raises(youtube.TranscriptBlocked):
        await youtube.fetch_transcript("RsR6cbovMfI")
    # The cooldown answers without touching YouTube, so a channel's remaining
    # videos don't each feed the block.
    with pytest.raises(youtube.TranscriptBlocked):
        await youtube.fetch_transcript("other-video")
    assert calls == 1


async def test_fetch_transcript_is_capped(monkeypatch):
    _stub_api(monkeypatch, [_track(False, "x" * (youtube.MAX_TRANSCRIPT_CHARS + 500))])
    assert len(await youtube.fetch_transcript("RsR6cbovMfI")) == youtube.MAX_TRANSCRIPT_CHARS
