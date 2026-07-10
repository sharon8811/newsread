"""Rendered-page screenshots, the input for vision summaries.

When a page yields no extractable prose (a comic, a chart, an image-only
post), the article can still be summarized by showing a vision-capable model
what the page looks like. Rendering runs through scrapling's DynamicFetcher
(Playwright/Chromium), which the backend image ships browsers for; a local
run without them just returns None and the caller falls back to the text
path's error.
"""

import logging

logger = logging.getLogger(__name__)

# Tall pages (long comics) are captured beyond the viewport but clipped:
# vision models cap image dimensions (8000px/side on Anthropic) and downscale
# anything huge into illegibility anyway.
VIEWPORT_WIDTH = 1280
MAX_HEIGHT = 8000
JPEG_QUALITY = 70
# Keep the base64 payload comfortably under provider limits (5 MB images on
# Anthropic); base64 inflates bytes by ~33%.
MAX_BYTES = 3_500_000
RETRY_JPEG_QUALITY = 40
TIMEOUT_MS = 30_000


async def capture(url: str) -> bytes | None:
    """Screenshot the rendered page as JPEG; None on any failure."""
    try:
        from scrapling.fetchers import DynamicFetcher
    except Exception as exc:  # pragma: no cover - import guard for local runs
        logger.warning("Screenshot support unavailable: %s", exc)
        return None

    shot: bytes | None = None

    async def grab(page):
        nonlocal shot
        height = await page.evaluate("document.documentElement.scrollHeight")
        clip = None
        if height and height > MAX_HEIGHT:
            clip = {"x": 0, "y": 0, "width": VIEWPORT_WIDTH, "height": MAX_HEIGHT}
        shot = await page.screenshot(
            full_page=clip is None, clip=clip, type="jpeg", quality=JPEG_QUALITY
        )
        if len(shot) > MAX_BYTES:
            shot = await page.screenshot(
                full_page=clip is None, clip=clip, type="jpeg", quality=RETRY_JPEG_QUALITY
            )
        return page

    try:
        await DynamicFetcher.async_fetch(
            url,
            headless=True,
            network_idle=True,
            timeout=TIMEOUT_MS,
            page_action=grab,
            # The backend container runs as root, where Chromium refuses to
            # start its sandbox; the container is the isolation boundary.
            extra_flags=["--no-sandbox"],
        )
    except Exception as exc:
        logger.warning("Screenshot of %s failed: %s", url, exc)
        return None
    if shot is None:
        logger.warning("Screenshot of %s produced no image", url)
    return shot
