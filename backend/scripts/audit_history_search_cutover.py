"""Compare legacy page search with document search before migration 0011.

This command is deliberately read-only and emits aggregate overlap only: URLs,
titles, page text, and user identifiers never leave the database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass

from sqlalchemy import text

from app import db


@dataclass(frozen=True)
class QueryAudit:
    query: str
    legacy_results: int
    document_results: int
    shared_results: int
    legacy_coverage: float


def compare_results(
    query: str,
    legacy: set[tuple[int, str]],
    documents: set[tuple[int, str]],
) -> QueryAudit:
    shared = legacy & documents
    return QueryAudit(
        query=query,
        legacy_results=len(legacy),
        document_results=len(documents),
        shared_results=len(shared),
        legacy_coverage=1.0 if not legacy else len(shared) / len(legacy),
    )


async def audit_query(query: str, *, limit: int) -> QueryAudit:
    parameters = {"query": query, "limit": limit}
    async with db.SessionLocal() as session:
        legacy = set(
            (
                await session.execute(
                    text(
                        """
                        SELECT user_id, url_hash
                        FROM browser_history_pages
                        WHERE search_tsv @@ websearch_to_tsquery('english', :query)
                        ORDER BY ts_rank_cd(
                          search_tsv,
                          websearch_to_tsquery('english', :query)
                        ) DESC
                        LIMIT :limit
                        """
                    ),
                    parameters,
                )
            ).tuples()
        )
        documents = set(
            (
                await session.execute(
                    text(
                        """
                        SELECT locations.user_id, locations.url_hash
                        FROM browser_history_documents AS documents
                        JOIN browser_history_page_documents AS links
                          ON links.document_id = documents.id
                        JOIN browser_history_pages AS locations
                          ON locations.id = links.page_id
                        WHERE (
                          documents.search_tsv
                            @@ websearch_to_tsquery('simple', :query)
                          OR (
                            setweight(
                              to_tsvector('english', coalesce(locations.title, '')),
                              'A'
                            )
                            ||
                            setweight(
                              to_tsvector('simple', coalesce(locations.hostname, '')),
                              'B'
                            )
                          ) @@ websearch_to_tsquery('simple', :query)
                        )
                        GROUP BY locations.user_id, locations.url_hash
                        LIMIT :limit
                        """
                    ),
                    parameters,
                )
            ).tuples()
        )
    return compare_results(query, legacy, documents)


async def require_dual_read_schema() -> None:
    async with db.SessionLocal() as session:
        legacy_column = await session.scalar(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'browser_history_pages'
                  AND column_name = 'search_tsv'
                """
            )
        )
    if legacy_column is None:
        raise RuntimeError(
            "legacy page search is unavailable; run this audit before migration 0011"
        )


async def run(args: argparse.Namespace) -> int:
    await require_dual_read_schema()
    audits = [await audit_query(query, limit=args.limit) for query in dict.fromkeys(args.query)]
    report = {
        "minimum_legacy_coverage": args.minimum_coverage,
        "passed": all(audit.legacy_coverage >= args.minimum_coverage for audit in audits),
        "queries": [asdict(audit) for audit in audits],
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--query",
        action="append",
        required=True,
        help="Representative search query; repeat for a useful sample.",
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--minimum-coverage", type=float, default=0.8)
    args = parser.parse_args()
    if args.limit < 1 or not 0 <= args.minimum_coverage <= 1:
        parser.error("--limit must be positive and coverage must be between 0 and 1")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
