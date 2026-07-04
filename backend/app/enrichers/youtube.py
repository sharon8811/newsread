import re
from datetime import timedelta

import httpx

from .base import CleanUrl, EnrichError, Enricher

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class YouTubeEnricher(Enricher):
    kind = "youtube"
    ttl = timedelta(days=30)
    hosts = frozenset({"youtube.com", "youtu.be"})

    def matches(self, url: CleanUrl) -> str | None:
        candidate: str | None = None
        segments = [s for s in url.path.split("/") if s]
        if url.host == "youtu.be":
            candidate = segments[0] if segments else None
        elif url.path == "/watch":
            candidate = url.query.get("v")
        elif len(segments) == 2 and segments[0] in ("shorts", "embed"):
            candidate = segments[1]
        if candidate and _VIDEO_ID.match(candidate):
            return candidate
        return None

    def entity_url(self, key: str) -> str:
        return f"https://www.youtube.com/watch?v={key}"

    async def fetch(self, key: str, client: httpx.AsyncClient) -> dict:
        # oEmbed: title/channel/thumbnail only — views need a Data API key.
        response = await client.get(
            "https://www.youtube.com/oembed",
            params={"url": self.entity_url(key), "format": "json"},
        )
        if response.status_code in (400, 401, 403, 404):
            raise EnrichError(f"youtube video {key} unavailable")
        response.raise_for_status()
        raw = response.json()
        return {
            "title": raw.get("title"),
            "channel": raw.get("author_name"),
            "channel_url": raw.get("author_url"),
            "thumbnail_url": raw.get("thumbnail_url"),
        }

    def badge(self, data: dict) -> dict:
        return {
            "label": data.get("title"),
            "channel": data.get("channel"),
            "thumbnail_url": data.get("thumbnail_url"),
        }
