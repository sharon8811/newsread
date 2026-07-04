from datetime import timedelta

import httpx

from ..config import settings
from .base import CleanUrl, EnrichError, Enricher

RESERVED = frozenset(
    "orgs features topics collections trending marketplace sponsors settings apps "
    "about pricing login join search explore contact blog site notifications new "
    "codespaces issues pulls readme events".split()
)


class GitHubEnricher(Enricher):
    kind = "github"
    ttl = timedelta(hours=6)
    hosts = frozenset({"github.com"})

    def matches(self, url: CleanUrl) -> str | None:
        segments = [s for s in url.path.split("/") if s]
        if len(segments) < 2:
            return None
        owner, repo = segments[0], segments[1]
        if owner.lower() in RESERVED:
            return None
        if repo.endswith(".git"):
            repo = repo[:-4]
        if not repo:
            return None
        # Any subpath (/issues/1, /blob/main/...) still identifies the repo.
        return f"{owner}/{repo}".lower()

    def entity_url(self, key: str) -> str:
        return f"https://github.com/{key}"

    async def fetch(self, key: str, client: httpx.AsyncClient) -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
        response = await client.get(f"https://api.github.com/repos/{key}", headers=headers)
        if response.status_code in (404, 451):
            raise EnrichError(f"repo {key} not found")
        if response.status_code in (403, 429):
            raise EnrichError("github rate limited")
        response.raise_for_status()
        raw = response.json()
        license_id = (raw.get("license") or {}).get("spdx_id")
        if license_id == "NOASSERTION":
            license_id = None
        return {
            "full_name": raw.get("full_name"),
            "description": raw.get("description"),
            "stargazers_count": raw.get("stargazers_count"),
            "forks_count": raw.get("forks_count"),
            "open_issues_count": raw.get("open_issues_count"),
            "language": raw.get("language"),
            "license": license_id,
            "pushed_at": raw.get("pushed_at"),
            "archived": raw.get("archived"),
            "topics": (raw.get("topics") or [])[:8],
            "homepage": raw.get("homepage"),
            "subscribers_count": raw.get("subscribers_count"),
        }

    def badge(self, data: dict) -> dict:
        return {
            "label": data.get("full_name"),
            "stars": data.get("stargazers_count"),
            "language": data.get("language"),
            "license": data.get("license"),
        }
