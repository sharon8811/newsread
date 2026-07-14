"""Startup seed data. Currently just the feed catalog: a curated directory
converted from the awesome-rss-feeds OPML collection into
data/catalog_seed.json (one object per feed: url, title, description,
site_url, category)."""

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from .models import CatalogEntry

CATALOG_SEED_PATH = Path(__file__).parent / "data" / "catalog_seed.json"


async def seed_catalog(conn: AsyncConnection) -> None:
    """Upsert the curated catalog. Idempotent: entries are keyed by url, and
    re-running refreshes titles/descriptions/categories from the seed file.
    Entries removed from this managed source are deactivated; submissions and
    other catalog sources are left untouched."""
    entries = json.loads(CATALOG_SEED_PATH.read_text())
    if not entries:
        return
    normalized = []
    for entry in entries:
        value = {
            **entry,
            "source": "awesome-rss-feeds",
            "is_active": entry.get("is_active", True),
        }
        for field in ("checked_at", "item_count", "latest_item_at", "final_url", "content_type"):
            value.setdefault(field, None)
        value.setdefault("health_status", "unchecked")
        value.setdefault("preview_items", [])
        for field in ("checked_at", "latest_item_at"):
            if value.get(field):
                value[field] = datetime.fromisoformat(value[field])
        normalized.append(value)
    entries = normalized
    stmt = insert(CatalogEntry).values(entries)
    stmt = stmt.on_conflict_do_update(
        index_elements=[CatalogEntry.url],
        set_={
            "title": stmt.excluded.title,
            "description": stmt.excluded.description,
            "site_url": stmt.excluded.site_url,
            "category": stmt.excluded.category,
            "source": stmt.excluded.source,
            "is_active": stmt.excluded.is_active,
            "health_status": stmt.excluded.health_status,
            "checked_at": stmt.excluded.checked_at,
            "item_count": stmt.excluded.item_count,
            "latest_item_at": stmt.excluded.latest_item_at,
            "final_url": stmt.excluded.final_url,
            "content_type": stmt.excluded.content_type,
            "preview_items": stmt.excluded.preview_items,
        },
    )
    await conn.execute(stmt)
    urls = [entry["url"] for entry in entries]
    await conn.execute(
        update(CatalogEntry)
        .where(CatalogEntry.source == "awesome-rss-feeds", CatalogEntry.url.not_in(urls))
        .values(is_active=False)
    )
