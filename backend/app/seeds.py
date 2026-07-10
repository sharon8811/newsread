"""Startup seed data. Currently just the feed catalog: a curated directory
converted from the awesome-rss-feeds OPML collection into
data/catalog_seed.json (one object per feed: url, title, description,
site_url, category)."""

import json
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from .models import CatalogEntry

CATALOG_SEED_PATH = Path(__file__).parent / "data" / "catalog_seed.json"


async def seed_catalog(conn: AsyncConnection) -> None:
    """Upsert the curated catalog. Idempotent: entries are keyed by url, and
    re-running refreshes titles/descriptions/categories from the seed file.
    Rows added by hand (or entries dropped from the file) are left alone."""
    entries = json.loads(CATALOG_SEED_PATH.read_text())
    if not entries:
        return
    stmt = insert(CatalogEntry).values(entries)
    stmt = stmt.on_conflict_do_update(
        index_elements=[CatalogEntry.url],
        set_={
            "title": stmt.excluded.title,
            "description": stmt.excluded.description,
            "site_url": stmt.excluded.site_url,
            "category": stmt.excluded.category,
        },
    )
    await conn.execute(stmt)
