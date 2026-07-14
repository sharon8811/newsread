#!/usr/bin/env python3
"""Audit, enrich, and optionally clean the managed RSS catalog.

Dry-run is the default. Pass --apply to replace the seed with healthy entries.
The JSON report is always written so removals can be reviewed and reproduced.
"""

import argparse
import asyncio
import html
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.fetcher import FeedParseError, fetch_feed_data, strip_html
from app.seeds import CATALOG_SEED_PATH


@dataclass
class AuditResult:
    url: str
    status: str
    detail: str = ""
    final_url: str | None = None
    title: str | None = None
    description: str | None = None
    site_url: str | None = None
    content_type: str | None = None
    item_count: int = 0
    latest_item_at: str | None = None
    preview_items: list[dict] | None = None


async def _inspect_once(entry: dict, semaphore: asyncio.Semaphore, stale_days: int) -> AuditResult:
    async with semaphore:
        try:
            parsed = await fetch_feed_data(entry["url"], require_articles=True)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            status = (
                "gone"
                if code in {404, 410}
                else "blocked"
                if code in {401, 403, 429}
                else "transient"
            )
            return AuditResult(entry["url"], status, f"HTTP {code}")
        except FeedParseError as exc:
            message = str(exc)
            status = "empty" if "no items" in message else "invalid"
            return AuditResult(entry["url"], status, message)
        except Exception as exc:
            return AuditResult(entry["url"], "unreachable", f"{type(exc).__name__}: {exc}")

        description = strip_html(parsed.description or entry.get("description") or "").strip()
        for _ in range(3):
            decoded = html.unescape(description)
            if decoded == description:
                break
            description = decoded
        if not description:
            return AuditResult(
                entry["url"],
                "missing_description",
                "No description in seed or live feed",
                final_url=parsed.final_url,
                title=parsed.title,
                site_url=parsed.site_url,
                content_type=parsed.content_type,
                item_count=len(parsed.articles),
            )
        dated = [article.published_at for article in parsed.articles if article.published_at]
        latest = max(dated) if dated else None
        cutoff = datetime.now(UTC) - timedelta(days=stale_days)
        status = "stale" if latest and latest < cutoff else "healthy"
        preview = [
            {
                "title": article.title,
                "url": article.url,
                "published_at": article.published_at.isoformat() if article.published_at else None,
            }
            for article in parsed.articles[:3]
        ]
        return AuditResult(
            entry["url"],
            status,
            f"Latest item is older than {stale_days} days" if status == "stale" else "",
            final_url=parsed.final_url,
            title=parsed.title,
            description=description,
            site_url=parsed.site_url,
            content_type=parsed.content_type,
            item_count=len(parsed.articles),
            latest_item_at=latest.isoformat() if latest else None,
            preview_items=preview,
        )


async def inspect(entry: dict, semaphore: asyncio.Semaphore, stale_days: int) -> AuditResult:
    result = await _inspect_once(entry, semaphore, stale_days)
    if result.status in {"transient", "unreachable"}:
        await asyncio.sleep(1)
        result = await _inspect_once(entry, semaphore, stale_days)
    return result


async def run(args: argparse.Namespace) -> None:
    entries = json.loads(CATALOG_SEED_PATH.read_text())
    semaphore = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(
        *(inspect(entry, semaphore, args.stale_days) for entry in entries)
    )
    by_url = {result.url: result for result in results}
    counts = Counter(result.status for result in results)
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "total": len(results),
        "counts": dict(sorted(counts.items())),
        "results": [asdict(result) for result in results],
    }
    report_path = Path(args.report)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    removals = {"gone", "invalid", "empty", "missing_description", "unreachable"}
    if args.remove_stale:
        removals.add("stale")
    cleaned = []
    for entry in entries:
        result = by_url[entry["url"]]
        if not strip_html(entry.get("description") or "").strip() and result.status != "healthy":
            continue
        if result.status in removals:
            continue
        entry["health_status"] = result.status
        # The check timestamp lives in the report, not the seed: stamping all
        # entries would make every audit run dirty the seed file and force the
        # monthly workflow to open a churn-only PR.
        entry.pop("checked_at", None)
        entry["is_active"] = result.status == "healthy"
        if result.status == "healthy":
            # Prefer canonical live metadata, but retain the curated title when
            # the feed exposes an empty or machine-like one.
            entry["description"] = result.description
            entry["site_url"] = result.site_url or entry.get("site_url")
            entry["item_count"] = result.item_count
            entry["latest_item_at"] = result.latest_item_at
            entry["final_url"] = result.final_url
            entry["content_type"] = result.content_type
            entry["preview_items"] = result.preview_items or []
        cleaned.append(entry)

    print(
        json.dumps(
            {
                "report": str(report_path),
                "kept": len(cleaned),
                "removed": len(entries) - len(cleaned),
                "counts": counts,
            },
            default=dict,
        )
    )
    if args.apply:
        CATALOG_SEED_PATH.write_text(json.dumps(cleaned, indent=1, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the cleaned seed")
    parser.add_argument(
        "--remove-stale", action="store_true", help="remove feeds with no recent dated item"
    )
    parser.add_argument("--stale-days", type=int, default=548)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--report", default="catalog-audit-report.json")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
