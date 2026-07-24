"""Rewrap browser-history data keys under the configured current master key."""

from __future__ import annotations

import argparse
import asyncio

from app import db
from app.config import settings
from app.history_crypto import MasterKeyring
from app.history_storage import HistoryKeyService


async def run(batch_size: int) -> int:
    key_service = HistoryKeyService(
        MasterKeyring.from_config(
            current_key=settings.history_encryption_master_key,
            current_version=settings.history_encryption_wrapping_key_version,
            previous_keys_json=settings.history_encryption_previous_master_keys,
        )
    )
    total = 0
    while True:
        async with db.SessionLocal() as session:
            count = await key_service.rewrap_data_keys(
                session,
                batch_size=batch_size,
            )
            await session.commit()
        total += count
        if count < batch_size:
            return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    count = asyncio.run(run(args.batch_size))
    print(f"rewrapped {count} browser-history data keys")


if __name__ == "__main__":
    main()
