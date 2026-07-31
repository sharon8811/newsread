"""Benchmark exact vs HNSW-indexed semantic search on synthetic data (#81).

Seeds a scratch database (DROPPED and recreated every run) with N articles +
random embeddings and a multi-user browser-history corpus, then measures the
three query shapes from the issue — global article search, related-articles
KNN pool, per-user (filtered) history search — exact vs indexed, plus recall
of the indexed path against the exact scan and whether EXPLAIN shows the
index. The statements mirror the app's call sites (articles.py,
history_search.py) minus their non-vector legs.

Usage, against the compose Postgres:

    cd backend && uv run python scripts/benchmark_ann.py --rows 100000

The benchmark database defaults to `newsread_bench` on localhost:5433 and is
destroyed on every run; --database-url overrides it.
"""

import argparse
import asyncio
import os
import statistics
import time

DEFAULT_URL = "postgresql+asyncpg://newsread:newsread@localhost:5433/newsread_bench"
MODEL = "bench-emb"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=100_000, help="article embeddings")
    parser.add_argument("--dim", type=int, default=1536)
    parser.add_argument("--users", type=int, default=5)
    parser.add_argument("--docs-per-user", type=int, default=4_000)
    parser.add_argument("--chunks-per-doc", type=int, default=2)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--database-url", default=DEFAULT_URL)
    return parser.parse_args()


async def recreate_database(url: str) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    base, _, dbname = url.rpartition("/")
    admin = create_async_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        from sqlalchemy import text

        await conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    await admin.dispose()


SEED_SQL = [
    # One feed, N users subscribed to it, N articles all inside every window.
    """INSERT INTO feeds (url, title, refresh_interval_minutes)
       VALUES ('https://bench.example/rss', 'Bench', 60)""",
    """INSERT INTO users (email, username, name, password_hash)
       SELECT 'u' || i || '@bench.example', 'u' || i, 'Bench User', 'x'
       FROM generate_series(1, :users) i""",
    """INSERT INTO subscriptions (user_id, feed_id)
       SELECT u.id, f.id FROM users u, feeds f""",
    """INSERT INTO articles (feed_id, guid, url, title, content_html, excerpt)
       SELECT f.id, 'g' || i, 'https://bench.example/' || i, 'Article ' || i,
              '<p>x</p>', 'excerpt'
       FROM feeds f, generate_series(1, :rows) i""",
    # Clustered vectors (1000 centers + per-row noise), not uniform noise:
    # uniform random vectors at high dimension concentrate all distances
    # around 1.0, which makes top-K a giant tie-band and any ANN recall
    # number meaningless. Real embeddings cluster. The 0*c / 0*a.id terms
    # correlate the subqueries so each row gets its own vector.
    """CREATE TEMP TABLE bench_centers AS
       SELECT c AS id,
              (SELECT array_agg(random() * 2 - 1)
               FROM generate_series(1, :dim + 0 * c)) AS center
       FROM generate_series(1, 1000) c""",
    """INSERT INTO article_embeddings (article_id, model, embedding, input_hash)
       SELECT a.id, :model,
              (SELECT array_agg(x + (random() - 0.5) * 0.4 ORDER BY ord)
               FROM unnest((SELECT center FROM bench_centers
                            WHERE id = 1 + (a.id % 1000))) WITH ORDINALITY t(x, ord)
              )::vector,
              md5(a.id::text)
       FROM articles a""",
    """INSERT INTO browser_history_documents
           (user_id, content_hash, object_key, storage_status, byte_size,
            character_count, extraction_version)
       SELECT u.id, md5(u.id || ':' || i) || md5('pad'),
              'bench/' || u.id || '/' || i, 'ready', 100, 100, 'bench-v1'
       FROM users u, generate_series(1, :docs) i""",
    """INSERT INTO browser_history_pages
           (user_id, url_hash, url, hostname, first_visited_at, last_visited_at)
       SELECT d.user_id, md5('p' || d.id) || md5('pad'),
              'https://site' || d.id || '.example/', 'site' || d.id || '.example',
              now(), now()
       FROM browser_history_documents d""",
    """INSERT INTO browser_history_page_documents
           (page_id, document_id, first_seen_at, last_seen_at)
       SELECT p.id, d.id, now(), now()
       FROM browser_history_pages p
       JOIN browser_history_documents d
         ON d.user_id = p.user_id
        AND p.url_hash = md5('p' || d.id) || md5('pad')""",
    """INSERT INTO browser_history_document_embeddings
           (document_id, chunk_index, model, embedding, input_hash)
       SELECT d.id, c, :model,
              (SELECT array_agg(x + (random() - 0.5) * 0.4 ORDER BY ord)
               FROM unnest((SELECT center FROM bench_centers
                            WHERE id = 1 + ((d.id + c) % 1000))) WITH ORDINALITY t(x, ord)
              )::vector,
              md5(d.id || ':' || c) || md5('pad')
       FROM browser_history_documents d, generate_series(0, :chunks - 1) c""",
]


async def main() -> None:
    args = parse_args()
    os.environ["NEWSREAD_DATABASE_URL"] = args.database_url
    os.environ["NEWSREAD_OPENAI_API_KEY"] = "bench"
    os.environ["NEWSREAD_OPENAI_EMBEDDING_MODEL"] = MODEL
    os.environ.setdefault("NEWSREAD_DEPLOYMENT", "self_hosted")
    # The repo-root .env is the prod config; without this override its
    # history-content flag drags object-store settings into validation.
    os.environ["NEWSREAD_BROWSER_HISTORY_CONTENT_ENABLED"] = "0"

    await recreate_database(args.database_url)

    # Import after the env points at the bench database (settings and the
    # engine are built at import time, same trick as tests/conftest.py).
    from sqlalchemy import text

    from app import ann, db
    from app.history_search import _document_vector_ids
    from app.models import Article, ArticleEmbedding, Subscription
    from app.routers.articles import RELATED_KNN_POOL

    print(
        f"Schema + seed: {args.rows} articles, {args.users}x{args.docs_per_user} history docs, dim {args.dim}"
    )
    await db.init_db()
    seed_started = time.perf_counter()
    async with db.engine.begin() as conn:
        for statement in SEED_SQL:
            await conn.execute(
                text(statement),
                {
                    "users": args.users,
                    "rows": args.rows,
                    "dim": args.dim,
                    "model": MODEL,
                    "docs": args.docs_per_user,
                    "chunks": args.chunks_per_doc,
                },
            )
        await conn.execute(text("ANALYZE"))
    print(f"Seeded in {time.perf_counter() - seed_started:.1f}s")

    from sqlalchemy import select

    async with db.SessionLocal() as session:
        query_vector = list(
            await session.scalar(
                select(ArticleEmbedding.embedding).where(ArticleEmbedding.article_id == 1)
            )
        )
        history_vector = query_vector[: args.dim]

    def article_search_stmt():
        # Mirrors articles._hybrid_search_ids' vector leg (subscription scope).
        knn = ann.knn_distance(ArticleEmbedding.embedding, query_vector)
        return (
            select(Article.id)
            .join(Subscription, Subscription.feed_id == Article.feed_id)
            .where(Subscription.user_id == 1)
            .join(ArticleEmbedding, ArticleEmbedding.article_id == Article.id)
            .where(ArticleEmbedding.model == MODEL)
            .order_by(knn)
            .limit(60)
        )

    def related_pool_stmt():
        # Mirrors articles.related_articles' KNN candidate pool.
        knn = ann.knn_distance(ArticleEmbedding.embedding, query_vector)
        return (
            select(ArticleEmbedding.article_id, knn.label("distance"))
            .where(ArticleEmbedding.model == MODEL)
            .order_by(knn)
            .limit(RELATED_KNN_POOL)
        )

    async def run_history(session):
        return await _document_vector_ids(
            session,
            user_id=1,
            query_vector=history_vector,
            hostname=None,
            date_from=None,
            date_to=None,
        )

    async def run_stmt(session, stmt):
        return list(await session.scalars(stmt))

    async def measure(runner, *, exact: bool):
        timings, result = [], None
        for _ in range(2 + args.runs):
            async with db.SessionLocal() as session:
                await ann.relax_scan(session)
                if exact:
                    await session.execute(text("SET LOCAL enable_indexscan = off"))
                started = time.perf_counter()
                result = await runner(session)
                timings.append((time.perf_counter() - started) * 1000)
        return statistics.median(timings[2:]), result

    vector_literal = "[" + ",".join(f"{x:.6f}" for x in query_vector) + "]"

    async def uses_index(order_only: bool) -> bool:
        # Hand-built SQL (a pgvector bind can't render as a literal) with the
        # same cast expression the app statements produce.
        knn_sql = f"(embedding::halfvec({args.dim})) <=> '{vector_literal}'::halfvec({args.dim})"
        scope = (
            ""
            if order_only
            else (
                "JOIN articles a ON a.id = e.article_id "
                "JOIN subscriptions s ON s.feed_id = a.feed_id AND s.user_id = 1 "
            )
        )
        async with db.SessionLocal() as session:
            await ann.relax_scan(session)
            plan = "\n".join(
                (
                    await session.scalars(
                        text(
                            f"EXPLAIN SELECT e.article_id FROM article_embeddings e "
                            f"{scope}WHERE e.model = '{MODEL}' "
                            f"ORDER BY {knn_sql} LIMIT {60 if not order_only else RELATED_KNN_POOL}"
                        )
                    )
                ).all()
            )
        return "hnsw_" in plan

    shapes = [
        ("article search (scoped, LIMIT 60)", lambda s: run_stmt(s, article_search_stmt()), False),
        (
            f"related KNN pool (LIMIT {RELATED_KNN_POOL})",
            lambda s: run_stmt(s, related_pool_stmt()),
            True,
        ),
        ("history search (per-user, grouped)", run_history, None),
    ]

    exact_results = {}
    print("\nExact scans (no index):")
    for name, runner, _ in shapes:
        ms, rows = await measure(runner, exact=True)
        exact_results[name] = rows
        print(f"  {name}: {ms:.1f} ms ({len(rows)} rows)")

    build_started = time.perf_counter()
    await ann.ensure_indexes()
    build_seconds = time.perf_counter() - build_started
    async with db.SessionLocal() as session:
        size = await session.scalar(
            text(
                "SELECT pg_size_pretty(sum(pg_relation_size(indexname::regclass))) "
                "FROM pg_indexes WHERE indexname LIKE 'hnsw#_%' ESCAPE '#'"
            )
        )
    print(f"\nIndex build (all tables): {build_seconds:.1f}s, total size {size}")

    print("\n| shape | exact ms | indexed ms | recall | index used |")
    print("|---|---|---|---|---|")
    for name, runner, order_only in shapes:
        exact_ms, _ = await measure(runner, exact=True)
        indexed_ms, rows = await measure(runner, exact=False)
        exact_rows = exact_results[name]
        recall = len(set(rows) & set(exact_rows)) / len(exact_rows) if exact_rows else 1.0
        used = await uses_index(order_only) if order_only is not None else "n/a"
        print(f"| {name} | {exact_ms:.1f} | {indexed_ms:.1f} | {recall:.3f} | {used} |")

    await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
