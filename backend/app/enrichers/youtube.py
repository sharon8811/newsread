from datetime import timedelta

import httpx

from .. import youtube
from .base import CleanUrl, Enricher, EnrichError


class YouTubeEnricher(Enricher):
    kind = "youtube"
    ttl = timedelta(days=30)
    # music. is not stripped by clean_url the way www./m. are, so it needs its
    # own dispatch entry; youtube.video_id normalizes it away afterwards.
    hosts = frozenset({"youtube.com", "youtu.be", "music.youtube.com", "youtube-nocookie.com"})

    def matches(self, url: CleanUrl) -> str | None:
        # Delegated rather than reimplemented: the extractor decides from the
        # same function whether to read captions instead of the page, and a
        # link recognized by only one of the two would be badged as a video
        # while being summarized as an article (or the reverse).
        return youtube.video_id(url.raw)

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
