"""Rendered-page screenshot capture (the input for vision summaries)."""

import types

import scrapling.fetchers

from app import screenshot


class FakePage:
    """Playwright page double: reports a height, returns screenshot bytes."""

    def __init__(self, height=2000, shots=(b"jpeg-bytes",)):
        self.height = height
        self.shots = list(shots)
        self.screenshot_calls = []

    async def evaluate(self, script):
        return self.height

    async def screenshot(self, **kwargs):
        self.screenshot_calls.append(kwargs)
        return self.shots.pop(0) if len(self.shots) > 1 else self.shots[0]


def _fake_fetcher(page):
    async def async_fetch(url, **kwargs):
        await kwargs["page_action"](page)
        return types.SimpleNamespace(status=200)

    return types.SimpleNamespace(async_fetch=async_fetch)


async def test_capture_returns_full_page_jpeg(monkeypatch):
    page = FakePage()
    monkeypatch.setattr(scrapling.fetchers, "DynamicFetcher", _fake_fetcher(page))
    shot = await screenshot.capture("https://x/comic")
    assert shot == b"jpeg-bytes"
    call = page.screenshot_calls[0]
    assert call["full_page"] is True
    assert call["clip"] is None
    assert call["type"] == "jpeg"


async def test_capture_clips_very_tall_pages(monkeypatch):
    page = FakePage(height=30_000)
    monkeypatch.setattr(scrapling.fetchers, "DynamicFetcher", _fake_fetcher(page))
    await screenshot.capture("https://x/long-comic")
    call = page.screenshot_calls[0]
    assert call["full_page"] is False
    assert call["clip"]["height"] == screenshot.MAX_HEIGHT


async def test_capture_retries_at_lower_quality_when_huge(monkeypatch):
    big = b"x" * (screenshot.MAX_BYTES + 1)
    page = FakePage(shots=[big, b"small"])
    monkeypatch.setattr(scrapling.fetchers, "DynamicFetcher", _fake_fetcher(page))
    shot = await screenshot.capture("https://x/huge")
    assert shot == b"small"
    assert page.screenshot_calls[1]["quality"] == screenshot.RETRY_JPEG_QUALITY


async def test_capture_none_on_fetch_failure(monkeypatch):
    async def async_fetch(url, **kwargs):
        raise RuntimeError("no browser binaries")

    monkeypatch.setattr(
        scrapling.fetchers,
        "DynamicFetcher",
        types.SimpleNamespace(async_fetch=async_fetch),
    )
    assert await screenshot.capture("https://x/broken") is None


async def test_capture_none_when_action_never_ran(monkeypatch):
    async def async_fetch(url, **kwargs):
        return types.SimpleNamespace(status=200)  # page_action skipped

    monkeypatch.setattr(
        scrapling.fetchers,
        "DynamicFetcher",
        types.SimpleNamespace(async_fetch=async_fetch),
    )
    assert await screenshot.capture("https://x/odd") is None
