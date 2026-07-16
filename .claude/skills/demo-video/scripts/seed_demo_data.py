#!/usr/bin/env python
"""Seed the newsread_test database with polished demo content for the README
demo video.

Run with the backend venv, with the same NEWSREAD_* env the demo backend
server uses (so `settings.openai_embedding_model` matches what the related-
articles endpoint will query):

    /path/to/backend/.venv/bin/python seed_demo_data.py --manifest out.json

Everything is inserted directly through the ORM — never via POST /api/feeds,
which would synchronously try to fetch the (fake) feed URLs. Feed URLs use
the reserved-invalid host demo.newsread.invalid so even a stray arq worker
poll can only fail harmlessly; the worker should not be running during a
demo recording anyway.

Idempotent: re-running wipes previous demo feeds/articles and reuses the
demo user.
"""

import argparse
import asyncio
import json
import math
import os
import random
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND_DIR = REPO_ROOT / "backend"

DEMO_URL_PREFIX = "https://demo.newsread.invalid/"
DEMO_USERNAME = "demo"
DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo-pass-1234"
DEMO_NAME = "Demo Reader"

EMBED_DIM = 256

# ---------------------------------------------------------------------------
# Demo content. Publications are fictional; the flagship article links to the
# real SQLite essay so its live Hacker News discussion renders in the demo.
# Summaries follow the product's teaser policy: <=120 words of markdown that
# make you want to click through.
# ---------------------------------------------------------------------------

FEEDS = [
    {
        "key": "build-log",
        "title": "The Build Log",
        "site_url": "https://demo.newsread.invalid/build-log",
        "description": "Notes from the tooling trenches: databases, build systems, deploys.",
    },
    {
        "key": "wire-protocol",
        "title": "Wire Protocol",
        "site_url": "https://demo.newsread.invalid/wire-protocol",
        "description": "Networking and infrastructure, measured rather than argued.",
    },
    {
        "key": "orbital-weekly",
        "title": "Orbital Weekly",
        "site_url": "https://demo.newsread.invalid/orbital-weekly",
        "description": "Spaceflight engineering, once a week, no hype.",
    },
    {
        "key": "signal-path",
        "title": "Signal Path",
        "site_url": "https://demo.newsread.invalid/signal-path",
        "description": "Machine learning systems in production.",
    },
]

# Each article: feed key, slug, title, author, hours_ago, excerpt,
# summary_short (one line), summary (markdown teaser), cluster role.
# cluster: "flagship" | "same_story" | "related" | None
ARTICLES = [
    # --- newest first in the inbox ---------------------------------------
    {
        "feed": "signal-path",
        "slug": "small-models-big-context",
        "title": "Small models, big context: the new efficiency frontier",
        "author": "Priya Raman",
        "hours_ago": 2,
        "excerpt": "Long-context small models are quietly displacing retrieval pipelines for a surprising share of workloads.",
        "summary_short": "Long-context small models now beat RAG pipelines on cost for many mid-scale workloads.",
        "summary": "**The pitch:** for corpora under ~50k documents, stuffing a long context often beats running a retrieval stack.\n\n- Cache-aware batching makes 128k-token prompts cheaper than they look\n- Retrieval still wins on freshness and auditability\n- The crossover point moved 10x in a year\n\nThe full post walks through the cost model with real traffic numbers.",
    },
    {
        "feed": "orbital-weekly",
        "slug": "europa-clipper-gravity-assist",
        "title": "Europa Clipper's first gravity assist, explained",
        "author": "Dana Okafor",
        "hours_ago": 5,
        "excerpt": "The Mars flyby bent the trajectory by 23 degrees and bought the mission 700 m/s of delta-v for free.",
        "summary_short": "How one Mars flyby bought Europa Clipper 700 m/s of delta-v.",
        "summary": "**Why it matters:** gravity assists are the only reason a mission this heavy can reach Jupiter on a Falcon Heavy.\n\nThe piece traces the trajectory design — why Mars first, what the navigation team corrected two weeks out, and what the June 2027 Earth flyby sets up. Clear diagrams, no math required.",
    },
    {
        "feed": "build-log",
        "slug": "postgres-18-features",
        "title": "Postgres 18 features you'll actually use",
        "author": "Marta Silva",
        "hours_ago": 9,
        "excerpt": "Skipping the headline features to look at the small quality-of-life wins that change day-to-day work.",
        "summary_short": "The unglamorous Postgres 18 changes that matter day to day.",
        "summary": "Not the headline features — the small ones:\n\n- `RETURNING` on `MERGE` finally lands\n- Faster `pg_dump` on partitioned tables\n- Better plan stability under extended statistics\n\nEach comes with a before/after example from a real schema. Worth ten minutes before your next upgrade window.",
    },
    {
        "feed": "wire-protocol",
        "slug": "quic-at-the-edge",
        "title": "QUIC at the edge: what we measured after a year",
        "author": "Jonas Meier",
        "hours_ago": 14,
        "excerpt": "Twelve months of QUIC in production at the edge: tail latency down, CPU up, and one nasty middlebox story.",
        "summary_short": "A year of production QUIC: p99 latency down 18%, CPU cost up 9%.",
        "summary": "**The headline numbers:** p99 latency down 18%, connection setup down 40%, CPU up 9%.\n\nThe interesting part is the failure modes — a carrier middlebox that silently rate-limited UDP, and how connection migration papered over Wi-Fi handoffs until it didn't. Honest, data-heavy write-up.",
    },
    {
        "feed": "signal-path",
        "slug": "evaluating-rag-beyond-recall",
        "title": "Evaluating RAG systems beyond recall",
        "author": "Priya Raman",
        "hours_ago": 20,
        "excerpt": "Recall@k tells you almost nothing about whether your RAG system produces correct answers. Here's what to measure instead.",
        "summary_short": "Recall@k is a weak proxy — measure answer faithfulness and citation precision instead.",
        "summary": "**The argument:** retrieval metrics are a proxy, and a weak one — high recall@k coexists with wrong answers all the time.\n\nThe post proposes a three-layer eval: retrieval, grounding, and answer faithfulness, with an open-source harness for each. The citation-precision metric alone is worth the read.",
    },
    {
        "feed": "orbital-weekly",
        "slug": "ion-propulsion-comeback",
        "title": "The quiet comeback of ion propulsion",
        "author": "Dana Okafor",
        "hours_ago": 26,
        "excerpt": "Hall-effect thrusters are eating the station-keeping market, and the supply chain finally caught up.",
        "summary_short": "Hall-effect thrusters now power 60% of new GEO satellites.",
        "summary": "Ion drives went from exotic to default in five years — 60% of new GEO satellites now fly electric.\n\nThe piece covers why xenon gave way to krypton, what changed in cathode lifetime, and the one physics problem that still caps thrust. A tidy survey with good sourcing.",
    },
    # --- the flagship + its cluster ---------------------------------------
    {
        "feed": "build-log",
        "slug": "sqlite-faster-than-fs",
        "title": "SQLite: 35% faster than the filesystem",
        "author": "SQLite team",
        "hours_ago": 31,
        "url": "https://www.sqlite.org/fasterthanfs.html",
        "hn_query": "SQLite 35% faster than the filesystem",
        "cluster": "flagship",
        "excerpt": "Reading small blobs from SQLite can be ~35% faster than reading them from individual files, thanks to fewer open/close syscalls.",
        "summary_short": "For small blobs, one SQLite database beats thousands of little files by ~35%.",
        "summary": "**The claim:** reading 10 KB blobs out of a single SQLite database is about **35% faster** than reading equivalent individual files — and stores them in ~20% less space.\n\n- The win comes from skipping repeated `open()`/`close()` syscalls\n- Holds across Linux, macOS, and Windows, with caveats measured honestly\n- Writes are a different story, covered separately\n\nThe essay includes the full benchmark methodology, so you can re-run it yourself.",
    },
    {
        "feed": "wire-protocol",
        "slug": "blobs-vs-small-files",
        "title": "Benchmark deep-dive: SQLite blobs vs. small files on ext4 and APFS",
        "author": "Jonas Meier",
        "hours_ago": 44,
        "cluster": "same_story",
        "excerpt": "Reproducing the famous SQLite claim on modern filesystems, with flame graphs of where the syscall time actually goes.",
        "summary_short": "The SQLite-beats-the-filesystem result reproduces on ext4 and APFS in 2026.",
        "summary": "**The verdict:** the classic result still reproduces — SQLite wins on small-blob reads by 22–41% depending on filesystem.\n\nFlame graphs show the cost is almost entirely `open()`/`stat()` overhead, not read throughput. APFS narrows the gap; ext4 with relatime widens it. Full harness on GitHub.",
    },
    {
        "feed": "signal-path",
        "slug": "filesystem-slower-than-db",
        "title": "Why your filesystem is slower than a database for small reads",
        "author": "Ted Kowalski",
        "hours_ago": 52,
        "cluster": "same_story",
        "excerpt": "A mental model for when a single-file database beats the directory tree, and when it badly doesn't.",
        "summary_short": "A mental model for the database-beats-filesystem effect — and its limits.",
        "summary": "A conceptual companion to the benchmarks: filesystems pay per-file fixed costs (lookup, permissions, inode) that a database amortizes across one open handle.\n\nThe post is careful about the limits — large files, concurrent writers, and backup tooling all flip the trade. Ends with a decision table you'll want to bookmark.",
    },
    {
        "feed": "build-log",
        "slug": "duckdb-in-process-analytics",
        "title": "DuckDB and the rise of in-process analytics",
        "author": "Marta Silva",
        "hours_ago": 60,
        "cluster": "related",
        "excerpt": "The embedded-database idea that made SQLite ubiquitous is now doing the same for analytical workloads.",
        "summary_short": "DuckDB is doing for analytics what SQLite did for app storage.",
        "summary": "**The thesis:** in-process is the most underrated deployment model in data engineering.\n\nDuckDB took SQLite's embed-everywhere playbook and applied it to columnar analytics — no server, no cluster, query Parquet in place. The post benchmarks it against a warehouse for sub-100GB workloads and the results are uncomfortable for the warehouse.",
    },
    {
        "feed": "wire-protocol",
        "slug": "io-uring-in-practice",
        "title": "io_uring in practice: async file I/O without the pain",
        "author": "Jonas Meier",
        "hours_ago": 68,
        "cluster": "related",
        "excerpt": "Where io_uring actually pays off for file-heavy services, and the sharp edges the tutorials skip.",
        "summary_short": "When io_uring pays off for file-heavy services — with production numbers.",
        "summary": "A practitioner's guide: registered buffers, SQPOLL trade-offs, and why the biggest wins show up in small-read-heavy services (metadata stores, thumbnail servers).\n\nIncludes a case study cutting p99 file-read latency 3x — plus the kernel-version gotchas that cost the author a weekend.",
    },
    # --- filler below the fold --------------------------------------------
    {
        "feed": "build-log",
        "slug": "case-for-boring-deploys",
        "title": "The case for boring deploys",
        "author": "Marta Silva",
        "hours_ago": 76,
        "excerpt": "Blue-green, canary, feature flags: pick two, and make the third impossible to need.",
        "summary_short": "Deploy excitement is an incident precursor — engineer it away.",
        "summary": "**The rule:** if a deploy is exciting, something upstream already failed.\n\nThe post argues for a small, rigid toolkit — immutable artifacts, one promotion path, flags for everything user-visible — and shows the incident graph before and after adopting it. Persuasive and short.",
    },
    {
        "feed": "orbital-weekly",
        "slug": "starship-flight-12",
        "title": "Starship Flight 12: what actually changed",
        "author": "Dana Okafor",
        "hours_ago": 84,
        "excerpt": "Beyond the launch coverage: the heat-shield iteration and the propellant-transfer test that matter for Artemis.",
        "summary_short": "Flight 12's real news: heat-shield tiles and the first ship-to-ship propellant demo.",
        "summary": "Skip the highlight reels — the substance is in two changes:\n\n- A third-generation tile attachment that survived reentry visibly intact\n- The first ship-to-ship propellant transfer demo, the long pole for Artemis\n\nThe piece grades both against NASA's milestone criteria.",
    },
    {
        "feed": "signal-path",
        "slug": "mixture-of-depths",
        "title": "Mixture-of-depths: routing compute where it matters",
        "author": "Ted Kowalski",
        "hours_ago": 92,
        "excerpt": "Not every token deserves every layer. A readable tour of dynamic-depth transformers.",
        "summary_short": "Dynamic-depth transformers spend compute only on the tokens that need it.",
        "summary": "A readable tour of an idea whose time keeps almost coming: let easy tokens skip layers.\n\nThe post explains the routing trick, why training stability was the historical blocker, and the new results that finally make it practical at scale. Good diagrams throughout.",
    },
    {
        "feed": "wire-protocol",
        "slug": "anycast-for-mortals",
        "title": "Anycast for mortals",
        "author": "Jonas Meier",
        "hours_ago": 100,
        "excerpt": "You don't need to be a CDN to benefit from anycast. A practical setup for a mid-size service.",
        "summary_short": "A practical anycast setup for teams that aren't CDNs.",
        "summary": "**The premise:** anycast stopped being CDN-only years ago; the tooling just never got friendly.\n\nA walkthrough of a two-region setup with BGP communities, health-driven withdrawal, and the debugging story when half of Comcast went to the wrong region. Practical and honest about the operational cost.",
    },
    {
        "feed": "orbital-weekly",
        "slug": "cubesat-radiation-budget",
        "title": "Budgeting radiation tolerance in cubesat design",
        "author": "Dana Okafor",
        "hours_ago": 110,
        "excerpt": "Commercial parts, careful shielding, and watchdogs: how student missions survive without rad-hard budgets.",
        "summary_short": "How cubesats survive radiation without rad-hard budgets.",
        "summary": "Rad-hard parts cost 100x; most cubesats fly without them. This guide covers the compensating controls — brownout-aware watchdogs, ECC everywhere, latch-up detection — and shares failure data from 40 university missions. A great systems-thinking read even if you never leave Earth.",
    },
]


def resolve_hn_item(query: str, fallback: int = 8863) -> dict:
    """Find the HN story for the flagship article via Algolia; fall back to a
    known-good item id if offline."""
    try:
        url = (
            "https://hn.algolia.com/api/v1/search?tags=story&query="
            + urllib.parse.quote(query)
        )
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.load(resp)
        hits = [h for h in data.get("hits", []) if h.get("num_comments")]
        best = max(hits, key=lambda h: h.get("num_comments") or 0)
        return {
            "id": int(best["objectID"]),
            "points": best.get("points"),
            "comments": best.get("num_comments"),
            "title": best.get("title"),
        }
    except Exception as exc:  # offline demo still works, minus live comments
        print(f"[seed] HN lookup failed ({exc}); using fallback item {fallback}")
        return {"id": fallback, "points": None, "comments": None, "title": None}


def unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def make_vectors():
    """Hand-crafted embedding geometry:
    - same_story articles sit at cosine distance ~0.08 from the flagship
      (< 0.35 => "SAME STORY" tier)
    - related articles at ~0.50 (< 0.60 display threshold => "related" tier)
    - everything else is a random gaussian vector, which in 256 dims is
      near-orthogonal (distance ~1.0) and never surfaces.
    """
    rng = random.Random(4242)

    def rand_vec():
        return unit([rng.gauss(0, 1) for _ in range(EMBED_DIM)])

    anchor = rand_vec()

    def at_cosine(cos_sim):
        noise = rand_vec()
        dot = sum(a * b for a, b in zip(noise, anchor))
        perp = unit([n - dot * a for n, a in zip(noise, anchor)])
        sin = math.sqrt(max(0.0, 1 - cos_sim * cos_sim))
        return unit([cos_sim * a + sin * p for a, p in zip(anchor, perp)])

    vectors = {}
    for art in ARTICLES:
        cluster = art.get("cluster")
        if cluster == "flagship":
            vectors[art["slug"]] = anchor
        elif cluster == "same_story":
            vectors[art["slug"]] = at_cosine(0.92)
        elif cluster == "related":
            vectors[art["slug"]] = at_cosine(0.50)
        else:
            vectors[art["slug"]] = rand_vec()
    return vectors


async def seed(manifest_path: Path):
    # Import the backend app with the same env the demo server uses.
    sys.path.insert(0, str(BACKEND_DIR))
    os.chdir(BACKEND_DIR)  # settings loads ../.env relative to the backend cwd
    os.environ.setdefault(
        "NEWSREAD_DATABASE_URL",
        "postgresql+asyncpg://newsread:newsread@localhost:5433/newsread_test",
    )

    from sqlalchemy import delete, select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app import models
    from app.config import settings
    from app.security import hash_password

    db_url = os.environ["NEWSREAD_DATABASE_URL"]
    if "newsread_test" not in db_url:
        raise SystemExit(
            f"refusing to seed non-test database: {db_url} — the demo must "
            "never touch the real newsread DB"
        )

    embedding_model = settings.openai_embedding_model
    print(f"[seed] db={db_url}")
    print(f"[seed] embedding model tag: {embedding_model}")

    hn = resolve_hn_item(
        next(a["hn_query"] for a in ARTICLES if a.get("hn_query"))
    )
    print(f"[seed] HN item: {hn}")

    vectors = make_vectors()
    now = datetime.now(timezone.utc)

    engine = create_async_engine(db_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        # ---- wipe previous demo data (idempotent re-runs) ----------------
        old_feed_ids = (
            await db.scalars(
                select(models.Feed.id).where(
                    models.Feed.url.like(DEMO_URL_PREFIX + "%")
                )
            )
        ).all()
        if old_feed_ids:
            old_article_ids = (
                await db.scalars(
                    select(models.Article.id).where(
                        models.Article.feed_id.in_(old_feed_ids)
                    )
                )
            ).all()
            if old_article_ids:
                await db.execute(
                    delete(models.ArticleEmbedding).where(
                        models.ArticleEmbedding.article_id.in_(old_article_ids)
                    )
                )
                await db.execute(
                    delete(models.UserArticleState).where(
                        models.UserArticleState.article_id.in_(old_article_ids)
                    )
                )
                await db.execute(
                    delete(models.Article).where(
                        models.Article.id.in_(old_article_ids)
                    )
                )
            await db.execute(
                delete(models.Subscription).where(
                    models.Subscription.feed_id.in_(old_feed_ids)
                )
            )
            await db.execute(
                delete(models.Feed).where(models.Feed.id.in_(old_feed_ids))
            )
            print(f"[seed] wiped {len(old_feed_ids)} old demo feeds")

        # ---- demo user (reused if present) --------------------------------
        user = (
            await db.scalars(
                select(models.User).where(models.User.username == DEMO_USERNAME)
            )
        ).first()
        if user is None:
            user = models.User(
                email=DEMO_EMAIL,
                username=DEMO_USERNAME,
                name=DEMO_NAME,
                password_hash=hash_password(DEMO_PASSWORD),
            )
            db.add(user)
            await db.flush()
            print(f"[seed] created demo user id={user.id}")
        else:
            user.password_hash = hash_password(DEMO_PASSWORD)
            print(f"[seed] reusing demo user id={user.id}")

        # Clean the demo user's read/scroll state so everything starts unread.
        await db.execute(
            delete(models.UserArticleState).where(
                models.UserArticleState.user_id == user.id
            )
        )
        await db.execute(
            delete(models.UserReadingPosition).where(
                models.UserReadingPosition.user_id == user.id
            )
        )
        await db.execute(
            delete(models.Subscription).where(
                models.Subscription.user_id == user.id
            )
        )

        # ---- feeds + subscriptions ----------------------------------------
        feed_rows = {}
        for f in FEEDS:
            feed = models.Feed(
                url=f"{DEMO_URL_PREFIX}{f['key']}.xml",
                title=f["title"],
                site_url=f["site_url"],
                description=f["description"],
                # Never let a stray worker poll touch these: pretend we just
                # fetched and don't need to again for ~70 days.
                last_fetched_at=now,
                refresh_interval_minutes=100_000,
                ai_enabled=True,
                image_gen_enabled=False,
            )
            db.add(feed)
            feed_rows[f["key"]] = feed
        await db.flush()
        for feed in feed_rows.values():
            db.add(models.Subscription(user_id=user.id, feed_id=feed.id))

        # ---- articles + embeddings -----------------------------------------
        flagship_id = None
        warmup_id = None
        for art in ARTICLES:
            feed = feed_rows[art["feed"]]
            published = now - timedelta(hours=art["hours_ago"])
            url = art.get(
                "url", f"{feed.site_url}/{art['slug']}"
            )
            comments_url = None
            if art.get("cluster") == "flagship":
                comments_url = f"https://news.ycombinator.com/item?id={hn['id']}"
            body = (
                f"<p>{art['excerpt']}</p>"
                "<p>This is demo content seeded for the README walkthrough "
                "video; open the original for the real article.</p>"
            )
            row = models.Article(
                feed_id=feed.id,
                guid=f"{DEMO_URL_PREFIX}{art['slug']}",
                url=url,
                comments_url=comments_url,
                title=art["title"],
                author=art["author"],
                published_at=published,
                fetched_at=published,
                excerpt=art["excerpt"],
                content_html=body,
                full_text=art["excerpt"],
                full_text_fetched_at=published,  # avoids the "enriching" spinner
                image_url=f"https://picsum.photos/seed/newsread-{art['slug']}/1200/800",
                summary_short=art["summary_short"],
                summary_medium=art["summary_short"],
                summary=art["summary"],
                summary_model="demo",
                summary_generated_at=published,
            )
            db.add(row)
            await db.flush()
            if art.get("cluster") == "flagship":
                flagship_id = row.id
            warmup_id = row.id  # ends as the oldest article — see manifest note
            db.add(
                models.ArticleEmbedding(
                    article_id=row.id,
                    model=embedding_model,
                    embedding=vectors[art["slug"]],
                    embedded_at=published,
                )
            )

        await db.commit()

    await engine.dispose()

    manifest = {
        "identifier": DEMO_USERNAME,
        "password": DEMO_PASSWORD,
        "flagship_article_id": flagship_id,
        # Opening an article marks it read, and the inbox defaults to the
        # UNREAD tab — so the recorder must warm the article route with a
        # bottom-of-list filler, never the flagship itself.
        "warmup_article_id": warmup_id,
        "article_count": len(ARTICLES),
        "hn_item": hn,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"[seed] done — flagship article id={flagship_id}")
    print(f"[seed] manifest written to {manifest_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="where to write the JSON manifest the recorder reads",
    )
    args = parser.parse_args()
    asyncio.run(seed(args.manifest))
