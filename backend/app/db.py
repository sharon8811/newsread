import asyncio
import html
import logging
from pathlib import Path

from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from alembic import command as alembic_command

from .config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


# The worker holds one session per item across whole remote calls, so its peak
# demand is the sum of every module-level stage gate:
#
#   worker.py    enrich 4 + summarize 8 + NER 2       = 14
#   pipeline.py  entity extract 4 + entity refresh 4  =  8
#                                                       --
#                                                       22
#
# plus the short-lived sessions the batch queries open. Each gate must stay
# module-level for this arithmetic to hold — a per-invocation semaphore is
# multiplied by however many jobs arq runs at once (max_jobs, default 10).
# SQLAlchemy's defaults (5 + 10 overflow) sit under that ceiling, and
# exhausting the pool raises a checkout timeout rather than merely queueing;
# worse, an ungated stage starves the gated ones through the shared pool.
# 10 + 20 leaves headroom while staying well inside Postgres' default
# max_connections across both the API and worker processes.
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Set by init_db once the pgvector extension is confirmed. Embedding writes and
# semantic search check this so the app still runs on a plain Postgres.
vector_enabled = False


async def get_session():
    async with SessionLocal() as session:
        yield session


# Schema lives in Alembic revisions (backend/alembic/). init_db upgrades to
# head on every boot; pre-Alembic databases (built by the old create_all +
# MIGRATIONS path) are stamped at baseline on first contact.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent

# Serializes schema setup across concurrently booting processes (the API and
# the arq worker start together in docker-compose). Arbitrary but stable.
_SCHEMA_LOCK_KEY = 0x6E657773


def alembic_config() -> AlembicConfig:
    return AlembicConfig(str(_BACKEND_ROOT / "alembic.ini"))


# Data repairs and backfills. Each named group runs exactly once per
# database (tracked in one_shot_migrations) and must stay idempotent anyway:
# groups that predate the Alembic switch already ran on old databases via the
# retired MIGRATIONS list, then were re-claimed (as no-ops) under this
# mechanism.
ONE_SHOT_MIGRATIONS: dict[str, list[str]] = {
    # Recover HN thread references for rows ingested before content-based
    # discussion detection. Strict host/path matching avoids generic HN links;
    # anchoring on the hnrss 'Comments URL:' label skips other HN threads a
    # self-post's body may link before it.
    "backfill_hn_comments_url": [
        "UPDATE articles SET comments_url = 'https://news.ycombinator.com/item?id=' || "
        "substring(url from 'news\\.ycombinator\\.com/item\\?id=([0-9]+)') "
        "WHERE comments_url IS NULL AND url ~* '^https?://news\\.ycombinator\\.com/item\\?id=[0-9]+'",
        "UPDATE articles SET comments_url = 'https://news.ycombinator.com/item?id=' || "
        "substring(substring(content_html from '(?i)Comments URL:.*') "
        "from 'news\\.ycombinator\\.com/item\\?id=([0-9]+)') "
        "WHERE comments_url IS NULL "
        "AND content_html ~* 'Comments URL:.*news\\.ycombinator\\.com/item\\?id=[0-9]+'",
    ],
    # The zen view was replaced by the cards view; remap stored preferences.
    "remap_zen_view_to_cards": [
        "UPDATE users SET default_view = 'cards' WHERE default_view = 'zen'",
        "UPDATE subscriptions SET view_override = 'cards' WHERE view_override = 'zen'",
    ],
    # Ticket threads: legacy per-pin notes become each thread's first comment
    # (author = the pin's adder, timestamp = pin time), then the notes are
    # cleared. The pair is idempotent because the second statement empties
    # what the first selects; both run in init_db's single transaction.
    "migrate_pin_notes_to_comments": [
        "INSERT INTO project_article_comments (project_id, article_id, author_id, body, created_at) "
        "SELECT project_id, article_id, added_by_user_id, note, created_at FROM project_articles "
        "WHERE note IS NOT NULL AND btrim(note) <> ''",
        "UPDATE project_articles SET note = NULL WHERE note IS NOT NULL",
    ],
    # Generated-image URLs were briefly stored absolute (built from the OAuth
    # redirect base, which may point at a tunnel browsers can't reach);
    # relative paths survive any deployment host. Idempotent: already-relative
    # rows are excluded by the NOT LIKE.
    "relativize_generated_image_urls": [
        "UPDATE articles SET image_url = '/api/articles/' || id || '/generated-image' "
        "WHERE image_url LIKE '%/api/articles/%/generated-image' AND image_url NOT LIKE '/api/%'",
    ],
    # Sources that predate content-type routing were read as HTML pages: a
    # video kept its watch-page footer ("About Press Copyright Contact us…")
    # as full text, and a PDF kept its own bytes — which then reached the
    # model, whose summary duly reports having been handed a binary. Clearing
    # the text and the fetch stamp returns both to the enrichment queue, where
    # they now take the transcript and document branches; the summary goes
    # with them because it was written from the wrong source.
    "reprocess_video_and_pdf_sources": [
        # Videos: a transcript runs to thousands of characters, so anything
        # short and non-empty under a video URL is page furniture. Re-fetching
        # a genuinely brief transcript costs one caption request.
        "UPDATE articles SET full_text = '', full_text_fetched_at = NULL, summary_short = '', "
        "summary_medium = '', summary = '', summary_model = NULL, summary_generated_at = NULL, "
        "summary_language = NULL, summary_skipped_reason = NULL "
        "WHERE full_text <> '' AND length(full_text) < 400 AND url ~* "
        "'^https?://((www\\.|m\\.|music\\.)?(youtube\\.com|youtube-nocookie\\.com)/"
        "(watch\\?|shorts/|embed/|live/|v/)|youtu\\.be/)'",
        # Documents: the stored text opens with the file signature. No LIKE
        # escaping games — the first five characters either are '%PDF-' or
        # aren't.
        "UPDATE articles SET full_text = '', full_text_fetched_at = NULL, summary_short = '', "
        "summary_medium = '', summary = '', summary_model = NULL, summary_generated_at = NULL, "
        "summary_language = NULL, summary_skipped_reason = NULL "
        "WHERE left(ltrim(full_text), 5) = '%PDF-'",
        # Documents whose fetch yielded nothing at all: only the stamp and the
        # skip reason are cleared, so a summary written from the feed's own
        # abstract survives.
        "UPDATE articles SET full_text_fetched_at = NULL, summary_skipped_reason = NULL "
        "WHERE full_text = '' AND summary = '' AND url ~* '\\.pdf($|\\?)'",
    ],
    # The sweep above keyed on stored text that *starts* with the PDF
    # signature, and trafilatura often dropped the header — it began mid-way
    # into a compressed stream instead, leaving mojibake with no `%PDF-` and
    # sometimes not even an `endobj` to match on. Those rows were summarized
    # too; because lingua reads a language off the mojibake, two of them on
    # this instance were summarized *in Esperanto*.
    #
    # The tell that survives every variant is the decode damage itself: text
    # decoded from binary is roughly half U+FFFD replacement characters, and
    # real prose has none. Measured on this instance the two groups sit at
    # 37–50% and 0.0%, so 5% is nowhere near either. Deliberately not keyed on
    # the URL — this catches a document served from any path.
    "reprocess_binary_full_text": [
        "UPDATE articles SET full_text = '', full_text_fetched_at = NULL, summary_short = '', "
        "summary_medium = '', summary = '', summary_model = NULL, summary_generated_at = NULL, "
        "summary_language = NULL, summary_skipped_reason = NULL "
        "WHERE full_text <> '' AND "
        "(length(full_text) - length(replace(full_text, chr(65533), ''))) * 20 > length(full_text)",
        # And re-attempt the documents the first sweep left empty: some were
        # refused by a size cap that has since been raised.
        "UPDATE articles SET full_text_fetched_at = NULL, summary_skipped_reason = NULL "
        "WHERE full_text = '' AND summary = '' AND url ~* '\\.pdf($|\\?)'",
    ],
    # Summaries generated before the UNUSABLE contract sometimes describe the
    # fetch failure itself ("The provided URL leads to a dead end… a standard
    # 404 error screen") instead of the story. Clear the conservative matches
    # so the worker regenerates them under the new prompt — a false positive
    # (an article genuinely about 404s or paywalls) merely costs one
    # re-summarization, which either succeeds again or lands on
    # summary_skipped_reason = 'unusable_page'.
    "clear_error_page_summaries": [
        "UPDATE articles SET summary_short = '', summary_medium = '', summary = '', "
        "summary_model = NULL, summary_generated_at = NULL, summary_language = NULL, "
        "summary_skipped_reason = NULL "
        "WHERE summary <> '' AND summary ~* "
        "'(404 (error|page)|(error|page) 404"
        "|page (was |could )?not (be )?found"
        "|(leads?|led) (to )?a dead end"
        "|error (page|screen|message)"
        "|(cannot|can not|could not|couldn.t|can.t) be (accessed|retrieved|loaded|reached|displayed)"
        "|unable to (access|load|retrieve|reach)"
        "|the (provided|given|requested) (url|link|page)"
        "|verify (that )?(you are|you.re) (a )?human|captcha|cookie consent"
        "|(content|article|page) (is )?(behind|requires) (a )?(paywall|subscription|login|sign.?in)"
        "|no (article|actual|accessible|readable) (content|text)"
        "|javascript (is )?(required|disabled))'",
    ],
}


async def _clean_hnrss_content(conn) -> None:
    """Use the ingestion cleaner to repair HNRSS rows already in the DB."""
    from .fetcher import derive_excerpt, strip_hnrss_boilerplate

    rows = (
        await conn.execute(
            text(
                "SELECT id, content_html, comments_url FROM articles "
                "WHERE comments_url ~* '^https?://news\\.ycombinator\\.com/item\\?id=[0-9]+' "
                "AND content_html ILIKE '%Comments URL:%'"
            )
        )
    ).mappings()
    for row in rows:
        cleaned = strip_hnrss_boilerplate(row["content_html"], row["comments_url"])
        if cleaned == row["content_html"]:
            continue
        await conn.execute(
            text(
                "UPDATE articles SET content_html = :content_html, excerpt = :excerpt "
                "WHERE id = :id"
            ),
            {
                "id": row["id"],
                "content_html": cleaned,
                "excerpt": derive_excerpt(cleaned),
            },
        )


async def _skip_existing_short_summaries(conn) -> None:
    """Apply the short-source policy to summaries generated before it existed.

    Entity links and NER stamps intentionally stay intact. Clearing the summary
    also makes an existing embedding stale, so the normal embedding worker will
    converge it back to the title/excerpt input.
    """
    from .extractor import is_too_short_to_summarize
    from .fetcher import strip_html

    rows = (
        await conn.execute(
            text(
                "SELECT id, full_text, content_html FROM articles "
                "WHERE summary <> '' AND summary_skipped_reason IS NULL"
            )
        )
    ).mappings()
    for row in rows:
        source = row["full_text"] or strip_html(row["content_html"])
        if not is_too_short_to_summarize(source):
            continue
        await conn.execute(
            text(
                "UPDATE articles SET summary_short = '', summary_medium = '', summary = '', "
                "summary_model = NULL, summary_generated_at = NULL, "
                "summary_skipped_reason = 'too_short' WHERE id = :id"
            ),
            {"id": row["id"]},
        )


_HTML_ENTITY_SQL_RE = r"&(#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);"


async def _unescape_plain_text_entities(conn) -> None:
    """Decode HTML entities that ingestion used to leave in plain-text fields.

    strip_html once returned nh3's entity-escaped output, so a headline like
    "S&P" was stored as "S&amp;P" (issue #75). One unescape pass converges each
    row to exactly what the fixed ingestion path would store, including feeds
    whose publishers double-escape.
    """
    rows = (
        await conn.execute(
            text("SELECT id, title, excerpt FROM articles WHERE title ~ :re OR excerpt ~ :re"),
            {"re": _HTML_ENTITY_SQL_RE},
        )
    ).mappings()
    for row in rows:
        await conn.execute(
            text("UPDATE articles SET title = :title, excerpt = :excerpt WHERE id = :id"),
            {
                "id": row["id"],
                "title": html.unescape(row["title"] or ""),
                "excerpt": html.unescape(row["excerpt"] or ""),
            },
        )
    rows = (
        await conn.execute(
            text("SELECT id, description FROM feeds WHERE description ~ :re"),
            {"re": _HTML_ENTITY_SQL_RE},
        )
    ).mappings()
    for row in rows:
        await conn.execute(
            text("UPDATE feeds SET description = :description WHERE id = :id"),
            {"id": row["id"], "description": html.unescape(row["description"])},
        )


ONE_SHOT_REPAIRS = {
    "clean_hnrss_boilerplate_lxml": _clean_hnrss_content,
    "skip_existing_short_summaries": _skip_existing_short_summaries,
    "unescape_plain_text_entities": _unescape_plain_text_entities,
}


async def init_db(max_attempts: int = 30) -> None:
    """Migrate the schema to head, waiting for the database to accept connections."""
    global vector_enabled

    from . import models  # noqa: F401  (register mappings)
    from .seeds import seed_catalog, seed_tiers

    for attempt in range(1, max_attempts + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            break
        except Exception as exc:
            if attempt == max_attempts:
                raise
            logger.warning("Database not ready (attempt %d/%d): %s", attempt, max_attempts, exc)
            await asyncio.sleep(1)

    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    except Exception as exc:
        raise RuntimeError(
            "The pgvector extension is required (the compose stack ships "
            "pgvector/pgvector:pg16) but could not be created"
        ) from exc
    vector_enabled = True

    async with engine.begin() as conn:
        # Both the API and the arq worker run init_db at boot; the lock makes
        # the loser wait instead of racing the DDL. Transaction-scoped, so it
        # releases even on failure.
        await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SCHEMA_LOCK_KEY})
        stamped = await conn.scalar(text("SELECT to_regclass('public.alembic_version')"))
        populated = await conn.scalar(text("SELECT to_regclass('public.users')"))

        def _migrate(sync_conn) -> None:
            config = alembic_config()
            config.attributes["connection"] = sync_conn
            if stamped is None and populated is not None:
                # Pre-Alembic database built by the retired create_all +
                # MIGRATIONS path; its schema equals the baseline revision.
                alembic_command.stamp(config, "head")
            else:
                alembic_command.upgrade(config, "head")

        await conn.run_sync(_migrate)
        # Redundant on fresh databases (the baseline creates it); pre-Alembic
        # databases got it from the old init path, so this is belt-and-braces.
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS one_shot_migrations "
                "(name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
        )
        for name, statements in ONE_SHOT_MIGRATIONS.items():
            claimed = await conn.execute(
                text(
                    "INSERT INTO one_shot_migrations (name) VALUES (:name) "
                    "ON CONFLICT (name) DO NOTHING"
                ),
                {"name": name},
            )
            if claimed.rowcount:
                for statement in statements:
                    await conn.execute(text(statement))
        for name, repair in ONE_SHOT_REPAIRS.items():
            claimed = await conn.execute(
                text(
                    "INSERT INTO one_shot_migrations (name) VALUES (:name) "
                    "ON CONFLICT (name) DO NOTHING"
                ),
                {"name": name},
            )
            if claimed.rowcount:
                await repair(conn)
        await seed_catalog(conn)
        await seed_tiers(conn)
