#!/usr/bin/env python3
"""Push deterministic demo browser history through the public sync contract."""

import argparse
import asyncio
import getpass
from datetime import UTC, datetime, timedelta

import httpx

SAMPLES = [
    (
        "https://developer.mozilla.org/en-us/docs/web/api/indexeddb_api/",
        "IndexedDB API",
        "IndexedDB is a transactional browser database for structured data, files, and offline application queues.",
    ),
    (
        "https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/",
        "Extension service workers",
        "Chrome extension service workers react to browser events, persist state outside global memory, and stop when idle.",
    ),
    (
        "https://www.postgresql.org/docs/current/textsearch.html",
        "PostgreSQL full text search",
        "PostgreSQL provides tsvector documents, tsquery expressions, ranking functions, and GIN indexes for text retrieval.",
    ),
    (
        "https://github.com/pgvector/pgvector/",
        "pgvector",
        "Open source vector similarity search for PostgreSQL with cosine distance, exact scans, and approximate indexes.",
    ),
    (
        "https://fastapi.tiangolo.com/tutorial/dependencies/",
        "FastAPI dependencies",
        "Dependency injection can authenticate requests, validate headers, and reject invalid payloads before endpoint work begins.",
    ),
    (
        "https://www.w3.org/tr/wai-aria-practices/",
        "ARIA authoring practices",
        "Accessible interface patterns cover keyboard interaction, names, roles, focus management, and dynamic status messages.",
    ),
    (
        "https://react.dev/learn/render-and-commit",
        "Render and commit",
        "React renders components to calculate UI changes and then commits the minimum required updates to the browser DOM.",
    ),
    (
        "https://nextjs.org/docs/app/getting-started/caching-and-revalidating",
        "Caching and revalidating",
        "Next.js supports request caching, data revalidation, and explicit invalidation for server-rendered applications.",
    ),
    (
        "https://owasp.org/www-project-top-ten/",
        "OWASP Top 10",
        "The OWASP Top 10 describes common web application risks including broken access control, injection, and insecure design.",
    ),
    (
        "https://docs.python.org/3/library/asyncio.html",
        "asyncio",
        "Python asyncio provides event loops, tasks, synchronization primitives, and structured tools for concurrent network code.",
    ),
    (
        "https://en.wikipedia.org/wiki/Reciprocal_rank_fusion",
        "Reciprocal rank fusion",
        "Reciprocal rank fusion combines independently ranked result lists without requiring their scores to share a scale.",
    ),
    (
        "https://developer.mozilla.org/en-us/docs/web/http/headers/retry-after/",
        "Retry-After header",
        "The Retry-After response header tells a client how long to wait before making another request after rate limiting.",
    ),
]


def _records() -> list[dict]:
    now = datetime.now(UTC).replace(microsecond=0)
    records = []
    for index in range(36):
        url, title, text = SAMPLES[index % len(SAMPLES)]
        visit_time = now - timedelta(hours=index * 11 + index % 5)
        records.append(
            {
                "record_id": f"demo-history-{index + 1}",
                "url": f"{url}?demo={index + 1}",
                "title": title,
                "text": text,
                "text_excerpt": text[:220],
                "first_visited_at": (visit_time - timedelta(days=index % 4)).isoformat(),
                "last_visited_at": visit_time.isoformat(),
                "captured_at": visit_time.isoformat(),
                "visit_count": 1 + index % 7,
                "known_revision": 0,
            }
        )
    return records


async def _connection_token(
    client: httpx.AsyncClient,
    identifier: str,
    password: str,
) -> str:
    login = await client.post(
        "/api/auth/login",
        json={"identifier": identifier, "password": password},
    )
    login.raise_for_status()
    access_token = login.json()["access_token"]
    pairing = await client.post(
        "/api/history/connections",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "History demo seed"},
    )
    pairing.raise_for_status()
    return pairing.json()["token"]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--identifier", help="NewsRead username or email")
    parser.add_argument("--password", help="NewsRead password; omit to prompt")
    parser.add_argument(
        "--connection-token",
        help="Existing nrh_ token; skips login and connection creation",
    )
    args = parser.parse_args()

    async with httpx.AsyncClient(
        base_url=args.api_url.rstrip("/"),
        timeout=30,
    ) as client:
        token = args.connection_token
        if not token:
            if not args.identifier:
                parser.error("--identifier is required without --connection-token")
            password = args.password or getpass.getpass("NewsRead password: ")
            token = await _connection_token(client, args.identifier, password)

        accepted = 0
        rejected = 0
        records = _records()
        for start in range(0, len(records), 12):
            response = await client.post(
                "/api/history/sync",
                headers={"Authorization": f"Bearer {token}"},
                json={"records": records[start : start + 12]},
            )
            response.raise_for_status()
            body = response.json()
            accepted += len(body["accepted"])
            rejected += len(body["rejected"])

    print(f"history seed complete: {accepted} accepted, {rejected} rejected")


if __name__ == "__main__":
    asyncio.run(main())
