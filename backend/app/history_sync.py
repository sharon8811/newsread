"""Idempotent persistence for extension browser-history batches."""

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .history_embeddings import document_is_eligible as document_is_embedding_eligible
from .history_ingest import HistoryIngestService
from .history_policy import (
    NormalizedHistoryUrl,
    clamp_history_timestamp,
    domain_matches,
    history_content_hash,
    validate_normalized_history_url,
)
from .history_system_policy import matching_system_rule
from .models import (
    BrowserConnection,
    BrowserHistoryDeletion,
    BrowserHistoryDocument,
    BrowserHistoryDomainRule,
    BrowserHistoryImage,
    BrowserHistoryPage,
    BrowserHistoryPageConnection,
    BrowserHistoryPageDocument,
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
    disabled_system_rule_ids: set[str] | None = None,
    ingest: HistoryIngestService | None = None,
    content_pipeline_enabled: bool = False,
    legacy_inline_content_enabled: bool = True,
    newly_linked_document_ids: set[int] | None = None,
    now: datetime,
) -> tuple[BrowserHistoryPage, NormalizedHistoryUrl]:
    normalized = validate_normalized_history_url(capture.url)
    if normalized.hostname in _configured_newsread_hosts():
        raise SyncRejection("excluded", "NewsRead pages are not captured")
    system_rule = matching_system_rule(
        normalized,
        disabled_rule_ids=disabled_system_rule_ids or set(),
    )
    if system_rule is not None:
        raise SyncRejection("excluded", f"excluded by built-in rule: {system_rule.label}")

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
    document: BrowserHistoryDocument | None = None
    lead_image: BrowserHistoryImage | None = None
    favicon_image: BrowserHistoryImage | None = None
    if mode != "metadata_only" and content_pipeline_enabled:
        if capture.content_hash:
            document = await session.scalar(
                select(BrowserHistoryDocument).where(
                    BrowserHistoryDocument.user_id == connection.user_id,
                    BrowserHistoryDocument.content_hash == capture.content_hash,
                    BrowserHistoryDocument.storage_status == "ready",
                )
            )
            if document is None:
                raise SyncRejection("content_missing", "document must be uploaded before sync")
        elif capture.text and legacy_inline_content_enabled:
            if ingest is None:
                raise RuntimeError("history content ingest service is unavailable")
            document = await ingest.ingest_legacy_document(
                session,
                user_id=connection.user_id,
                text=capture.text,
            )
        image_hashes = {
            value for value in (capture.lead_image_hash, capture.favicon_image_hash) if value
        }
        images_by_hash = {
            image.image_hash: image
            for image in await session.scalars(
                select(BrowserHistoryImage).where(
                    BrowserHistoryImage.user_id == connection.user_id,
                    BrowserHistoryImage.image_hash.in_(image_hashes),
                    BrowserHistoryImage.storage_status == "ready",
                )
            )
        }
        if image_hashes - images_by_hash.keys():
            raise SyncRejection("content_missing", "images must be uploaded before sync")
        lead_image = images_by_hash.get(capture.lead_image_hash or "")
        favicon_image = images_by_hash.get(capture.favicon_image_hash or "")

    stores_inline_text = mode != "metadata_only" and not content_pipeline_enabled
    incoming_text = capture.text if stores_inline_text else ""
    incoming_excerpt = (
        capture.text_excerpt
        if stores_inline_text
        else document.text_excerpt
        if document is not None
        else ""
    )
    if incoming_text and not incoming_excerpt:
        incoming_excerpt = incoming_text[:400]
    content_hash = (
        history_content_hash(capture.title, normalized.hostname, incoming_text)
        if stores_inline_text
        else None
    )
    if lead_image is not None:
        if document is None:
            raise SyncRejection("invalid", "lead images require captured document content")
        if document.lead_image_id is None:
            document.lead_image_id = lead_image.id
        if lead_image.source_host is None:
            lead_image.source_host = normalized.hostname
    if favicon_image is not None and favicon_image.source_host is None:
        favicon_image.source_host = normalized.hostname

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
            current_document_id=document.id if document is not None else None,
            favicon_image_id=favicon_image.id if favicon_image is not None else None,
            first_visited_at=first_visited_at,
            last_visited_at=last_visited_at,
            visit_count=0,
            captured_at=captured_at if incoming_text or document is not None else None,
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
        if document is not None and (page.current_document_id is None or newer):
            page.current_document_id = document.id
            page.text_excerpt = document.text_excerpt
            page.captured_at = incoming_content_at
            changed = True
        if favicon_image is not None and (page.favicon_image_id is None or newer):
            page.favicon_image_id = favicon_image.id
            changed = True
        if changed:
            if document is None and stores_inline_text:
                page.content_hash = history_content_hash(page.title, page.hostname, page.text)

    if document is not None:
        # Serialize first-link creation for this immutable document. Without
        # the row lock, concurrent captures at different URLs could both
        # observe zero links and spend embedding quota twice.
        await session.scalar(
            select(BrowserHistoryDocument.id)
            .where(BrowserHistoryDocument.id == document.id)
            .with_for_update()
        )
        already_linked = (
            await session.scalar(
                select(BrowserHistoryPageDocument.id)
                .where(BrowserHistoryPageDocument.document_id == document.id)
                .limit(1)
            )
            is not None
        )
        page_document = pg_insert(BrowserHistoryPageDocument).values(
            page_id=page.id,
            document_id=document.id,
            first_seen_at=first_visited_at,
            last_seen_at=last_visited_at,
            captured_at=captured_at or last_visited_at,
        )
        link_id = await session.scalar(
            page_document.on_conflict_do_nothing(
                index_elements=["page_id", "document_id"],
            ).returning(BrowserHistoryPageDocument.id)
        )
        if link_id is not None:
            if (
                not already_linked
                and newly_linked_document_ids is not None
                and document_is_embedding_eligible(document)
            ):
                newly_linked_document_ids.add(document.id)
        else:
            await session.execute(
                page_document.on_conflict_do_update(
                    index_elements=["page_id", "document_id"],
                    set_={
                        "first_seen_at": func.least(
                            BrowserHistoryPageDocument.first_seen_at,
                            page_document.excluded.first_seen_at,
                        ),
                        "last_seen_at": func.greatest(
                            BrowserHistoryPageDocument.last_seen_at,
                            page_document.excluded.last_seen_at,
                        ),
                        "captured_at": func.greatest(
                            BrowserHistoryPageDocument.captured_at,
                            page_document.excluded.captured_at,
                        ),
                        "updated_at": func.now(),
                    },
                )
            )

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
