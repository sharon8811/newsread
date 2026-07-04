import asyncio
import re
import time
from datetime import timedelta

import feedparser
import httpx

from .base import CleanUrl, EnrichError, Enricher

# New-style 2007+ ids (2301.12345) or old-style archive ids (cs/9901002).
_NEW_ID = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
_OLD_ID = re.compile(r"^([a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?$")

# arXiv ToU: at most 1 request every 3 seconds, single connection.
_lock = asyncio.Lock()
_last_request = 0.0
_MIN_INTERVAL = 3.0


def _parse_id(path: str) -> str | None:
    segments = [s for s in path.split("/") if s]
    if len(segments) < 2 or segments[0] not in ("abs", "pdf", "html"):
        return None
    candidate = "/".join(segments[1:])
    if candidate.endswith(".pdf"):
        candidate = candidate[:-4]
    for pattern in (_NEW_ID, _OLD_ID):
        match = pattern.match(candidate)
        if match:
            return match.group(1)  # version stripped
    return None


class ArxivEnricher(Enricher):
    kind = "arxiv"
    ttl = timedelta(days=7)
    hosts = frozenset({"arxiv.org", "export.arxiv.org"})

    def matches(self, url: CleanUrl) -> str | None:
        return _parse_id(url.path)

    def entity_url(self, key: str) -> str:
        return f"https://arxiv.org/abs/{key}"

    async def fetch(self, key: str, client: httpx.AsyncClient) -> dict:
        global _last_request
        async with _lock:
            elapsed = time.monotonic() - _last_request
            if elapsed < _MIN_INTERVAL:
                await asyncio.sleep(_MIN_INTERVAL - elapsed)
            try:
                response = await client.get(
                    "https://export.arxiv.org/api/query",
                    params={"id_list": key, "max_results": 1},
                )
            finally:
                _last_request = time.monotonic()
        response.raise_for_status()
        parsed = feedparser.parse(response.text)
        if not parsed.entries:
            raise EnrichError(f"arxiv id {key} not found")
        entry = parsed.entries[0]
        title = re.sub(r"\s+", " ", entry.get("title", "")).strip()
        if not title or title.lower() == "error":
            raise EnrichError(f"arxiv id {key} not found")
        categories = [t.get("term") for t in entry.get("tags", []) if t.get("term")]
        primary = (entry.get("arxiv_primary_category") or {}).get("term") or (
            categories[0] if categories else None
        )
        return {
            "title": title,
            "abstract": re.sub(r"\s+", " ", entry.get("summary", "")).strip()[:1500],
            "published": entry.get("published"),
            "updated": entry.get("updated"),
            "authors": [a.get("name") for a in entry.get("authors", []) if a.get("name")][:20],
            "primary_category": primary,
            "categories": categories[:8],
            "comment": entry.get("arxiv_comment"),
            "journal_ref": entry.get("arxiv_journal_ref"),
            "doi": entry.get("arxiv_doi"),
        }

    def badge(self, data: dict) -> dict:
        authors = data.get("authors") or []
        authors_short = None
        if authors:
            first = authors[0].split()[-1] if authors[0] else authors[0]
            authors_short = first if len(authors) == 1 else f"{first} et al."
        return {
            "label": data.get("title"),
            "authors_short": authors_short,
            "primary_category": data.get("primary_category"),
        }
