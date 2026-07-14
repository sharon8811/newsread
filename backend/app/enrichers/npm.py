import logging
from datetime import timedelta
from urllib.parse import quote

import httpx

from .base import CleanUrl, Enricher, EnrichError

logger = logging.getLogger(__name__)


class NpmEnricher(Enricher):
    kind = "npm"
    ttl = timedelta(hours=12)
    hosts = frozenset({"npmjs.com"})

    def matches(self, url: CleanUrl) -> str | None:
        segments = [s for s in url.path.split("/") if s]
        if len(segments) < 2 or segments[0] != "package":
            return None
        if segments[1].startswith("@"):
            if len(segments) < 3:
                return None
            return f"{segments[1]}/{segments[2]}".lower()
        return segments[1].lower()  # trailing /v/{version} ignored

    def entity_url(self, key: str) -> str:
        return f"https://www.npmjs.com/package/{key}"

    async def fetch(self, key: str, client: httpx.AsyncClient) -> dict:
        # Never fetch the full packument (multi-MB for big packages).
        response = await client.get(f"https://registry.npmjs.org/{quote(key, safe='@/')}/latest")
        if response.status_code == 404:
            raise EnrichError(f"npm package {key} not found")
        response.raise_for_status()
        raw = response.json()
        license_value = raw.get("license")
        if isinstance(license_value, dict):
            license_value = license_value.get("type")
        data = {
            "name": raw.get("name"),
            "version": raw.get("version"),
            "description": raw.get("description"),
            "license": license_value,
            "homepage": raw.get("homepage"),
        }
        try:
            downloads = await client.get(
                f"https://api.npmjs.org/downloads/point/last-week/{quote(key, safe='@/')}"
            )
            if downloads.status_code == 200:
                data["downloads_last_week"] = downloads.json().get("downloads")
        except Exception as exc:  # downloads are decoration, not essential
            logger.debug("npm downloads fetch for %s failed: %s", key, exc)
        return data

    def badge(self, data: dict) -> dict:
        return {
            "label": data.get("name"),
            "version": data.get("version"),
            "downloads_last_week": data.get("downloads_last_week"),
        }
