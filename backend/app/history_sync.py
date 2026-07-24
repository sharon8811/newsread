"""Idempotent persistence for extension browser-history batches."""

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .history_policy import (
    NormalizedHistoryUrl,
    clamp_history_timestamp,
    domain_matches,
    history_content_hash,
    validate_normalized_history_url,
)
from .models import (
    BrowserConnection,
    BrowserHistoryDeletion,
    BrowserHistoryDomainRule,
    BrowserHistoryPage,
    BrowserHistoryPageConnection,
)
from .schemas import BrowserHistoryCaptureIn


@dataclass(frozen=True)
class SyncRejection(Exception):
    code: str
    detail: str


def _configured_newsread_hosts() -> set[str]:
    hosts: set[str] = set()
    for value in (settings.frontend_base_url, settings.oauth_redirect_base):
        hostname = urlsplit(value).hostname
        if hostname:
            hosts.add(hostname.casefold())
    return hosts


def _capture_mode(
    normalized: NormalizedHistoryUrl,
    rules: list[BrowserHistoryDomainRule],
) -> str:
    mode = "full"
    for rule in rules:
        if not domain_matches(
            normalized.hostname,
            rule.hostname,
            rule.match_subdomains,
        ):
            continue
        if rule.mode == "exclude":
            return "exclude"
        if rule.mode == "metadata_only":
            mode = "metadata_only"
    return mode


def _is_stale(
    capture: BrowserHistoryCaptureIn,
    normalized: NormalizedHistoryUrl,
    deletions: list[BrowserHistoryDeletion],
) -> bool:
    for deletion in deletions:
        if deletion.revision <= capture.known_revision:
            continue
        if deletion.scope == "all":
            return True
        if deletion.scope == "page" and deletion.scope_key == normalized.url_hash:
            return True
        if deletion.scope == "domain" and domain_matches(
            normalized.hostname,
            deletion.scope_key,
        ):
            return True
        if deletion.scope == "host" and normalized.hostname == deletion.scope_key:
            return True
    return False


async def persist_capture(
    session: AsyncSession,
    connection: BrowserConnection,
    capture: BrowserHistoryCaptureIn,
    *,
    rules: list[BrowserHistoryDomainRule],
    deletions: list[BrowserHistoryDeletion],
    now: datetime,
) -> tuple[BrowserHistoryPage, NormalizedHistoryUrl]:
    normalized = validate_normalized_history_url(capture.url)
    if normalized.hostname in _configured_newsread_hosts():
        raise SyncRejection("excluded", "NewsRead pages are not captured")

    mode = _capture_mode(normalized, rules)
    if mode == "exclude":
        raise SyncRejection("excluded", "domain is excluded by server policy")
    if _is_stale(capture, normalized, deletions):
        raise SyncRejection(
            "stale_revision",
            "capture predates a server-side history deletion",
        )

    first_visited_at = clamp_history_timestamp(capture.first_visited_at, now)
    last_visited_at = clamp_history_timestamp(capture.last_visited_at, now)
    captured_at = clamp_history_timestamp(capture.captured_at, now) if capture.captured_at else None
    incoming_text = "" if mode == "metadata_only" else capture.text
    incoming_excerpt = "" if mode == "metadata_only" else capture.text_excerpt
    if incoming_text and not incoming_excerpt:
        incoming_excerpt = incoming_text[:400]
    content_hash = history_content_hash(
        capture.title,
        normalized.hostname,
        incoming_text,
    )

    insert_page = (
        pg_insert(BrowserHistoryPage)
        .values(
            user_id=connection.user_id,
            url_hash=normalized.url_hash,
            url=normalized.url,
            title=capture.title,
            hostname=normalized.hostname,
            text=incoming_text,
            text_excerpt=incoming_excerpt,
            content_hash=content_hash,
            first_visited_at=first_visited_at,
            last_visited_at=last_visited_at,
            visit_count=0,
            captured_at=captured_at if incoming_text else None,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "url_hash"])
        .returning(BrowserHistoryPage.id)
    )
    page_id = await session.scalar(insert_page)
    created = page_id is not None
    if created:
        page = await session.get(BrowserHistoryPage, page_id)
    else:
        page = await session.scalar(
            select(BrowserHistoryPage)
            .where(
                BrowserHistoryPage.user_id == connection.user_id,
                BrowserHistoryPage.url_hash == normalized.url_hash,
            )
            .with_for_update()
        )

    incoming_content_at = captured_at or last_visited_at
    if not created:
        current_content_at = page.captured_at
        newer = current_content_at is None or incoming_content_at > current_content_at
        changed = False
        if capture.title and (not page.title or newer):
            page.title = capture.title
            changed = True
        if incoming_text and (not page.text or newer):
            page.text = incoming_text
            page.text_excerpt = incoming_excerpt
            page.captured_at = incoming_content_at
            changed = True
        if changed:
            page.content_hash = history_content_hash(page.title, page.hostname, page.text)

    aggregate_insert = pg_insert(BrowserHistoryPageConnection).values(
        page_id=page.id,
        connection_id=connection.id,
        first_visited_at=first_visited_at,
        last_visited_at=last_visited_at,
        visit_count=capture.visit_count,
    )
    await session.execute(
        aggregate_insert.on_conflict_do_update(
            index_elements=["page_id", "connection_id"],
            set_={
                "first_visited_at": func.least(
                    BrowserHistoryPageConnection.first_visited_at,
                    aggregate_insert.excluded.first_visited_at,
                ),
                "last_visited_at": func.greatest(
                    BrowserHistoryPageConnection.last_visited_at,
                    aggregate_insert.excluded.last_visited_at,
                ),
                "visit_count": func.greatest(
                    BrowserHistoryPageConnection.visit_count,
                    aggregate_insert.excluded.visit_count,
                ),
                "updated_at": func.now(),
            },
        )
    )
    await session.flush()

    first_at, last_at, visit_count = (
        await session.execute(
            select(
                func.min(BrowserHistoryPageConnection.first_visited_at),
                func.max(BrowserHistoryPageConnection.last_visited_at),
                func.sum(BrowserHistoryPageConnection.visit_count),
            ).where(BrowserHistoryPageConnection.page_id == page.id)
        )
    ).one()
    page.first_visited_at = first_at
    page.last_visited_at = last_at
    page.visit_count = visit_count
    return page, normalized
