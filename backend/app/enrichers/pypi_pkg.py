import re
from datetime import timedelta

import httpx

from .base import CleanUrl, Enricher, EnrichError


def _normalize(name: str) -> str:
    """PEP 503 name normalization."""
    return re.sub(r"[-_.]+", "-", name).lower()


class PyPIEnricher(Enricher):
    kind = "pypi"
    ttl = timedelta(hours=24)
    hosts = frozenset({"pypi.org"})

    def matches(self, url: CleanUrl) -> str | None:
        segments = [s for s in url.path.split("/") if s]
        if len(segments) < 2 or segments[0] != "project":
            return None
        return _normalize(segments[1])

    def entity_url(self, key: str) -> str:
        return f"https://pypi.org/project/{key}/"

    async def fetch(self, key: str, client: httpx.AsyncClient) -> dict:
        response = await client.get(f"https://pypi.org/pypi/{key}/json")
        if response.status_code == 404:
            raise EnrichError(f"pypi package {key} not found")
        response.raise_for_status()
        raw = response.json()
        info = raw.get("info") or {}
        urls = raw.get("urls") or []
        license_value = info.get("license_expression") or info.get("license")
        if license_value and len(license_value) > 120:
            license_value = None  # some packages stuff the whole license text here
        return {
            "name": info.get("name"),
            "version": info.get("version"),
            "summary": info.get("summary"),
            "requires_python": info.get("requires_python"),
            "license": license_value,
            "home_page": (info.get("project_urls") or {}).get("Homepage") or info.get("home_page"),
            "released_at": urls[0].get("upload_time_iso_8601") if urls else None,
        }

    def badge(self, data: dict) -> dict:
        return {
            "label": data.get("name"),
            "version": data.get("version"),
            "requires_python": data.get("requires_python"),
        }
