#!/usr/bin/env python3
"""Embed all new or changed catalog entries in restart-safe batches."""

import asyncio

from app.catalog_embeddings import embed_catalog_batch
from app.db import SessionLocal, init_db


async def main() -> None:
    await init_db()
    total = 0
    while True:
        async with SessionLocal() as session:
            count = await embed_catalog_batch(session)
        total += count
        if count == 0:
            break
        print(f"embedded {total} catalog entries")
    print(f"catalog embeddings up to date: {total} written")


if __name__ == "__main__":
    asyncio.run(main())
