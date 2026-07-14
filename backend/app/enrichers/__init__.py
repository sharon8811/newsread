"""Registry of per-site link enrichers. Adding a site = one module + one entry."""

from .arxiv import ArxivEnricher
from .base import Enricher, EnrichError
from .github import GitHubEnricher
from .huggingface import HFDatasetEnricher, HFModelEnricher
from .npm import NpmEnricher
from .pypi_pkg import PyPIEnricher
from .urls import clean_url, extract_links, extract_text_links
from .youtube import YouTubeEnricher

__all__ = [
    "ENRICHERS",
    "BY_KIND",
    "EnrichError",
    "Enricher",
    "badge_for",
    "clean_url",
    "extract_links",
    "extract_text_links",
    "match_url",
]

# HFDatasetEnricher before HFModelEnricher: same hosts, /datasets/ is more specific.
ENRICHERS: list[Enricher] = [
    GitHubEnricher(),
    HFDatasetEnricher(),
    HFModelEnricher(),
    ArxivEnricher(),
    PyPIEnricher(),
    NpmEnricher(),
    YouTubeEnricher(),
]

BY_KIND: dict[str, Enricher] = {e.kind: e for e in ENRICHERS}

_BY_HOST: dict[str, list[Enricher]] = {}
for _enricher in ENRICHERS:
    for _host in _enricher.hosts:
        _BY_HOST.setdefault(_host, []).append(_enricher)


def match_url(raw: str) -> tuple[Enricher, str] | None:
    url = clean_url(raw)
    if url is None:
        return None
    for enricher in _BY_HOST.get(url.host, ()):
        key = enricher.matches(url)
        if key:
            return enricher, key
    return None


def badge_for(kind: str, data: dict) -> dict:
    enricher = BY_KIND.get(kind)
    if enricher is None or not data:
        return {}
    try:
        return {k: v for k, v in enricher.badge(data).items() if v is not None}
    except Exception:
        return {}
