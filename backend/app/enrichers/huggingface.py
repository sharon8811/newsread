from datetime import timedelta

import httpx

from ..config import settings
from .base import CleanUrl, EnrichError, Enricher

HF_HOSTS = frozenset({"huggingface.co", "hf.co"})

# First path segments that are site sections, not org names.
RESERVED = frozenset(
    "datasets spaces docs blog tasks models papers collections pricing chat "
    "settings join login api new learn enterprise posts organizations terms "
    "privacy metrics welcome".split()
)


def _headers() -> dict:
    if settings.hf_token:
        return {"Authorization": f"Bearer {settings.hf_token}"}
    return {}


def _license_of(raw: dict) -> str | None:
    card = raw.get("cardData") or {}
    if card.get("license"):
        license_value = card["license"]
        return license_value[0] if isinstance(license_value, list) else license_value
    for tag in raw.get("tags") or []:
        if isinstance(tag, str) and tag.startswith("license:"):
            return tag.removeprefix("license:")
    return None


async def _get(path: str, client: httpx.AsyncClient) -> dict:
    response = await client.get(f"https://huggingface.co/api/{path}", headers=_headers())
    if response.status_code in (401, 403, 404):
        raise EnrichError(f"hf resource {path} unavailable")
    if response.status_code == 429:
        raise EnrichError("hf rate limited")
    response.raise_for_status()
    return response.json()


class HFModelEnricher(Enricher):
    kind = "hf_model"
    ttl = timedelta(hours=12)
    hosts = HF_HOSTS

    def matches(self, url: CleanUrl) -> str | None:
        segments = [s for s in url.path.split("/") if s]
        if len(segments) < 2 or segments[0].lower() in RESERVED:
            return None
        return f"{segments[0]}/{segments[1]}"

    def entity_url(self, key: str) -> str:
        return f"https://huggingface.co/{key}"

    async def fetch(self, key: str, client: httpx.AsyncClient) -> dict:
        raw = await _get(f"models/{key}", client)
        return {
            "id": raw.get("id"),
            "downloads": raw.get("downloads"),
            "likes": raw.get("likes"),
            "pipeline_tag": raw.get("pipeline_tag"),
            "last_modified": raw.get("lastModified"),
            "library": raw.get("library_name"),
            "gated": raw.get("gated"),
            "license": _license_of(raw),
            "params": (raw.get("safetensors") or {}).get("total"),
        }

    def badge(self, data: dict) -> dict:
        return {
            "label": data.get("id"),
            "downloads": data.get("downloads"),
            "likes": data.get("likes"),
            "params": data.get("params"),
        }


class HFDatasetEnricher(Enricher):
    kind = "hf_dataset"
    ttl = timedelta(hours=12)
    hosts = HF_HOSTS

    def matches(self, url: CleanUrl) -> str | None:
        segments = [s for s in url.path.split("/") if s]
        if len(segments) < 3 or segments[0] != "datasets":
            return None
        return f"{segments[1]}/{segments[2]}"

    def entity_url(self, key: str) -> str:
        return f"https://huggingface.co/datasets/{key}"

    async def fetch(self, key: str, client: httpx.AsyncClient) -> dict:
        raw = await _get(f"datasets/{key}", client)
        card = raw.get("cardData") or {}
        return {
            "id": raw.get("id"),
            "downloads": raw.get("downloads"),
            "likes": raw.get("likes"),
            "last_modified": raw.get("lastModified"),
            "gated": raw.get("gated"),
            "license": _license_of(raw),
            "task_categories": card.get("task_categories"),
            "size_categories": card.get("size_categories"),
        }

    def badge(self, data: dict) -> dict:
        return {
            "label": data.get("id"),
            "downloads": data.get("downloads"),
            "likes": data.get("likes"),
        }
