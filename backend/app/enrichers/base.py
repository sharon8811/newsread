"""Enricher contract: recognize a URL, fetch normalized data from a free API."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar

import httpx


class EnrichError(Exception):
    """Fetch failed in an expected way (404, rate limit, gone). Non-fatal."""


@dataclass(frozen=True)
class CleanUrl:
    raw: str
    host: str  # lowercased, "www."/"m." stripped
    path: str  # fragment removed, trailing slash stripped
    query: dict[str, str]  # tracking params removed


class Enricher(ABC):
    kind: ClassVar[str]
    ttl: ClassVar[timedelta]
    hosts: ClassVar[frozenset[str]]  # for O(1) dispatch in the registry

    @abstractmethod
    def matches(self, url: CleanUrl) -> str | None:
        """Return the canonical key (e.g. 'owner/repo') or None."""

    @abstractmethod
    def entity_url(self, key: str) -> str:
        """Canonical display URL for the entity."""

    @abstractmethod
    async def fetch(self, key: str, client: httpx.AsyncClient) -> dict:
        """Return a normalized, trimmed payload. Raises EnrichError on
        expected failures (404, rate limited, private)."""

    @abstractmethod
    def badge(self, data: dict) -> dict:
        """2-4 display fields for list rows; must tolerate partial data."""
