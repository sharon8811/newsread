"""Browser-history connections and synchronized capture policy."""

import base64
import hashlib
import json
import math
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .. import history_search
from ..config import settings
from ..deps import CurrentUser, DbSession
from ..history_auth import (
    BrowserConnectionAuth,
    generate_browser_token,
    require_browser_history_content_enabled,
    require_browser_history_enabled,
)
from ..history_content import HistoryContentError, decompress_history_document
from ..history_ingest import (
    HISTORY_CONTENT_CAPABILITY_REVISION as CONTENT_CAPABILITY_REVISION,
)
from ..history_ingest import (
    HistoryIngestError,
    HistoryIngestService,
    get_history_ingest_service,
    get_optional_history_ingest_service,
)
from ..history_policy import normalize_history_hostname, sanitize_capture_text
from ..history_sync import SyncRejection, persist_capture
from ..history_system_policy import (
    HISTORY_SYSTEM_POLICY_REVISION,
    HISTORY_SYSTEM_RULES,
    HISTORY_SYSTEM_RULES_BY_ID,
)
from ..models import (
    BrowserConnection,
    BrowserHistoryDeletion,
    BrowserHistoryDocument,
    BrowserHistoryDomainRule,
    BrowserHistoryImage,
    BrowserHistoryPage,
    BrowserHistoryPageConnection,
    BrowserHistorySettings,
    BrowserHistorySystemRuleOverride,
    User,
)
from ..schemas import (
    BrowserConnectionCreatedOut,
    BrowserConnectionCreateIn,
    BrowserConnectionOut,
    BrowserHistoryCaptureIn,
    BrowserHistoryClearIn,
    BrowserHistoryContentStatusIn,
    BrowserHistoryContentStatusOut,
    BrowserHistoryDeletionOut,
    BrowserHistoryDocumentUploadOut,
    BrowserHistoryDomainRuleIn,
    BrowserHistoryDomainRuleOut,
    BrowserHistoryExtensionOut,
    BrowserHistoryImageUploadOut,
    BrowserHistoryPageOut,
    BrowserHistorySettingsIn,
    BrowserHistorySettingsOut,
    BrowserHistorySummaryOut,
    BrowserHistorySyncAcceptedOut,
    BrowserHistorySyncIn,
    BrowserHistorySyncOut,
    BrowserHistorySyncRejectedOut,
    BrowserHistorySyncStatusOut,
    BrowserHistorySystemRuleIn,
    BrowserHistorySystemRuleOut,
)

router = APIRouter(
    prefix="/history",
    tags=["browser-history"],
    dependencies=[Depends(require_browser_history_enabled)],
)

TOKEN_CREATION_LIMIT = 10
TOKEN_CREATION_WINDOW = timedelta(hours=1)
MAX_SYNC_REQUEST_BYTES = 1024 * 1024
SYNC_RATE_LIMIT = 60
SYNC_RATE_WINDOW_SECONDS = 60
SYNC_REQUEST_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": BrowserHistorySyncIn.model_json_schema(),
            }
        },
    }
}


async def _settings_for(session: DbSession, user_id: int) -> BrowserHistorySettings:
    await session.execute(
        pg_insert(BrowserHistorySettings)
        .values(user_id=user_id)
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    return await session.get(BrowserHistorySettings, user_id)


def _connection_out(connection: BrowserConnection) -> BrowserConnectionOut:
    return BrowserConnectionOut.model_validate(connection)


def _settings_out(history_settings: BrowserHistorySettings) -> BrowserHistorySettingsOut:
    return BrowserHistorySettingsOut(
        retention_days=history_settings.retention_days,
        sync_revision=history_settings.sync_revision,
    )


async def _system_rule_state(
    session: DbSession,
    user_id: int,
) -> tuple[set[str], list[BrowserHistorySystemRuleOut]]:
    overrides = {
        row.rule_id: row.enabled
        for row in await session.scalars(
            select(BrowserHistorySystemRuleOverride).where(
                BrowserHistorySystemRuleOverride.user_id == user_id
            )
        )
    }
    disabled = {rule_id for rule_id, enabled in overrides.items() if not enabled}
    return disabled, [
        BrowserHistorySystemRuleOut(
            id=rule.id,
            label=rule.label,
            description=rule.description,
            hosts=list(rule.hosts),
            path_match=rule.path_match,
            path=rule.path,
            enabled=overrides.get(rule.id, True),
        )
        for rule in HISTORY_SYSTEM_RULES
    ]


async def _read_bounded_body(request: Request, max_bytes: int, *, label: str) -> bytes:
    content_length = request.headers.get("Content-Length")
    if content_length:
        try:
            declared = int(content_length)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from None
        if declared < 0 or declared > max_bytes:
            raise HTTPException(status_code=413, detail=f"{label} exceeds its byte limit")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(status_code=413, detail=f"{label} exceeds its byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _record_id(raw: object, index: int) -> str:
    if isinstance(raw, dict) and isinstance(raw.get("record_id"), str):
        cleaned = sanitize_capture_text(raw["record_id"])[:128]
        if cleaned:
            return cleaned
    return f"record-{index}"


def _validation_detail(exc: ValidationError) -> str:
    error = exc.errors(include_url=False)[0]
    field = ".".join(str(part) for part in error.get("loc", ()))
    message = error.get("msg", "invalid capture")
    return f"{field}: {message}" if field else message


def require_sync_content_length(
    content_length: Annotated[int | None, Header(alias="Content-Length")] = None,
) -> None:
    if content_length is not None and content_length > MAX_SYNC_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail="History sync batch exceeds 1 MiB")


async def _write_deletion(
    session: DbSession,
    *,
    user_id: int,
    scope: str,
    scope_key: str,
    revision: int,
) -> None:
    statement = pg_insert(BrowserHistoryDeletion).values(
        user_id=user_id,
        scope=scope,
        scope_key=scope_key,
        revision=revision,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["user_id", "scope", "scope_key"],
            set_={
                "revision": statement.excluded.revision,
                "created_at": func.now(),
            },
        )
    )


def _history_cursor_signature(
    *,
    q: str | None,
    hostname: str | None,
    date_from: date | None,
    date_to: date | None,
    sort: str,
) -> str:
    value = json.dumps(
        {
            "q": q,
            "hostname": hostname,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "sort": sort,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _encode_history_cursor(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_history_cursor(cursor: str, signature: str) -> dict:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if not isinstance(payload, dict) or payload.get("signature") != signature:
            raise ValueError
        return payload
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=422, detail="Invalid history cursor") from None


async def _enforce_sync_rate_limit(
    session: DbSession,
    connection: BrowserConnection,
) -> None:
    locked = await session.scalar(
        select(BrowserConnection).where(BrowserConnection.id == connection.id).with_for_update()
    )
    now = datetime.now(UTC)
    window = timedelta(seconds=SYNC_RATE_WINDOW_SECONDS)
    if locked.sync_window_started_at is None or locked.sync_window_started_at + window <= now:
        locked.sync_window_started_at = now
        locked.sync_request_count = 0
    if locked.sync_request_count >= SYNC_RATE_LIMIT:
        retry_after = max(
            1,
            math.ceil((locked.sync_window_started_at + window - now).total_seconds()),
        )
        await session.rollback()
        raise HTTPException(
            status_code=429,
            detail="Too many history sync requests; retry later",
            headers={"Retry-After": str(retry_after)},
        )
    locked.sync_request_count += 1
    await session.commit()


@router.post(
    "/connections",
    response_model=BrowserConnectionCreatedOut,
    status_code=201,
)
async def create_connection(
    body: BrowserConnectionCreateIn,
    response: Response,
    user: CurrentUser,
    session: DbSession,
):
    window_start = datetime.now(UTC) - TOKEN_CREATION_WINDOW
    recent_tokens = await session.scalar(
        select(func.count())
        .select_from(BrowserConnection)
        .where(
            BrowserConnection.user_id == user.id,
            BrowserConnection.created_at >= window_start,
        )
    )
    if recent_tokens >= TOKEN_CREATION_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Too many browser connections created; try again later",
            headers={"Retry-After": str(int(TOKEN_CREATION_WINDOW.total_seconds()))},
        )

    await _settings_for(session, user.id)
    for _ in range(3):
        token, prefix, token_hash = generate_browser_token()
        exists = await session.scalar(
            select(BrowserConnection.id).where(BrowserConnection.token_prefix == prefix)
        )
        if exists is None:
            break
    else:  # pragma: no cover - cryptographically implausible without monkeypatching
        raise HTTPException(status_code=503, detail="Could not create a browser connection")

    connection = BrowserConnection(
        user_id=user.id,
        name=body.name,
        token_prefix=prefix,
        token_hash=token_hash,
    )
    session.add(connection)
    await session.commit()
    await session.refresh(connection)
    response.headers["Cache-Control"] = "no-store"
    return BrowserConnectionCreatedOut(
        **_connection_out(connection).model_dump(),
        token=token,
    )


@router.get("/connections", response_model=list[BrowserConnectionOut])
async def list_connections(user: CurrentUser, session: DbSession):
    connections = (
        await session.scalars(
            select(BrowserConnection)
            .where(BrowserConnection.user_id == user.id)
            .order_by(BrowserConnection.created_at.desc(), BrowserConnection.id.desc())
        )
    ).all()
    return [_connection_out(connection) for connection in connections]


@router.delete("/connections/{connection_id}", status_code=204)
async def revoke_connection(
    connection_id: int,
    user: CurrentUser,
    session: DbSession,
):
    connection = await session.scalar(
        select(BrowserConnection).where(
            BrowserConnection.id == connection_id,
            BrowserConnection.user_id == user.id,
        )
    )
    if connection is None:
        raise HTTPException(status_code=404, detail="Browser connection not found")
    if connection.revoked_at is None:
        connection.revoked_at = datetime.now(UTC)
        await session.commit()


@router.get("/settings", response_model=BrowserHistorySettingsOut)
async def get_history_settings(user: CurrentUser, session: DbSession):
    history_settings = await _settings_for(session, user.id)
    await session.commit()
    return _settings_out(history_settings)


@router.patch("/settings", response_model=BrowserHistorySettingsOut)
async def update_history_settings(
    body: BrowserHistorySettingsIn,
    user: CurrentUser,
    session: DbSession,
):
    history_settings = await _settings_for(session, user.id)
    if "retention_days" in body.model_fields_set:
        history_settings.retention_days = body.retention_days
    await session.commit()
    await session.refresh(history_settings)
    return _settings_out(history_settings)


@router.get("/domain-rules", response_model=list[BrowserHistoryDomainRuleOut])
async def list_domain_rules(user: CurrentUser, session: DbSession):
    return (
        await session.scalars(
            select(BrowserHistoryDomainRule)
            .where(BrowserHistoryDomainRule.user_id == user.id)
            .order_by(
                BrowserHistoryDomainRule.hostname,
                BrowserHistoryDomainRule.match_subdomains,
            )
        )
    ).all()


@router.post(
    "/domain-rules",
    response_model=BrowserHistoryDomainRuleOut,
    status_code=201,
)
async def upsert_domain_rule(
    body: BrowserHistoryDomainRuleIn,
    user: CurrentUser,
    session: DbSession,
):
    history_settings = await _settings_for(session, user.id)
    rule = await session.scalar(
        select(BrowserHistoryDomainRule).where(
            BrowserHistoryDomainRule.user_id == user.id,
            BrowserHistoryDomainRule.hostname == body.hostname,
            BrowserHistoryDomainRule.match_subdomains == body.match_subdomains,
        )
    )
    if rule is None:
        rule = BrowserHistoryDomainRule(
            user_id=user.id,
            hostname=body.hostname,
            match_subdomains=body.match_subdomains,
            mode=body.mode,
        )
        session.add(rule)
    else:
        rule.mode = body.mode
    history_settings.sync_revision += 1
    if body.delete_existing:
        # Deletion breadth mirrors the rule's own matching: an exact-host rule
        # must not purge or stale-reject subdomains it does not exclude.
        await _write_deletion(
            session,
            user_id=user.id,
            scope="domain" if body.match_subdomains else "host",
            scope_key=body.hostname,
            revision=history_settings.sync_revision,
        )
        hostname_predicate = (
            or_(
                BrowserHistoryPage.hostname == body.hostname,
                BrowserHistoryPage.hostname.endswith(f".{body.hostname}"),
            )
            if body.match_subdomains
            else BrowserHistoryPage.hostname == body.hostname
        )
        await session.execute(
            delete(BrowserHistoryPage).where(
                BrowserHistoryPage.user_id == user.id,
                hostname_predicate,
            )
        )
    await session.commit()
    await session.refresh(rule)
    return rule


@router.delete("/domain-rules/{rule_id}", status_code=204)
async def delete_domain_rule(
    rule_id: int,
    user: CurrentUser,
    session: DbSession,
):
    rule = await session.scalar(
        select(BrowserHistoryDomainRule).where(
            BrowserHistoryDomainRule.id == rule_id,
            BrowserHistoryDomainRule.user_id == user.id,
        )
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="Domain rule not found")
    history_settings = await _settings_for(session, user.id)
    history_settings.sync_revision += 1
    await session.delete(rule)
    await session.commit()


@router.get("/system-rules", response_model=list[BrowserHistorySystemRuleOut])
async def list_system_rules(user: CurrentUser, session: DbSession):
    _, rules = await _system_rule_state(session, user.id)
    return rules


@router.patch(
    "/system-rules/{rule_id}",
    response_model=BrowserHistorySystemRuleOut,
)
async def update_system_rule(
    rule_id: str,
    body: BrowserHistorySystemRuleIn,
    user: CurrentUser,
    session: DbSession,
):
    definition = HISTORY_SYSTEM_RULES_BY_ID.get(rule_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Built-in history rule not found")
    statement = pg_insert(BrowserHistorySystemRuleOverride).values(
        user_id=user.id,
        rule_id=rule_id,
        enabled=body.enabled,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["user_id", "rule_id"],
            set_={"enabled": statement.excluded.enabled, "updated_at": func.now()},
        )
    )
    history_settings = await _settings_for(session, user.id)
    history_settings.sync_revision += 1
    await session.commit()
    return BrowserHistorySystemRuleOut(
        id=definition.id,
        label=definition.label,
        description=definition.description,
        hosts=list(definition.hosts),
        path_match=definition.path_match,
        path=definition.path,
        enabled=body.enabled,
    )


def _extension_package_path() -> Path:
    if settings.extension_package:
        return Path(settings.extension_package)
    return Path(__file__).resolve().parents[3] / "extension" / "newsread-history-extension.zip"


# Version cache keyed by (path, mtime) so each build is read once.
_extension_version_cache: dict[tuple[str, float], str | None] = {}


def _extension_package_info() -> tuple[Path | None, str | None]:
    path = _extension_package_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None, None
    key = (str(path), mtime)
    if key not in _extension_version_cache:
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
            version = manifest.get("version")
        except (zipfile.BadZipFile, KeyError, ValueError, OSError):
            version = None
        _extension_version_cache.clear()
        _extension_version_cache[key] = version if isinstance(version, str) else None
    return path, _extension_version_cache[key]


@router.get("/extension", response_model=BrowserHistoryExtensionOut)
async def extension_package_status(user: CurrentUser):
    path, version = _extension_package_info()
    return BrowserHistoryExtensionOut(available=path is not None, version=version)


@router.get("/extension/download")
async def download_extension_package(user: CurrentUser):
    path, version = _extension_package_info()
    if path is None:
        raise HTTPException(status_code=404, detail="Extension package is not available")
    suffix = f"-{version}" if version else ""
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"newsread-history-extension{suffix}.zip",
    )


@router.get("/summary", response_model=BrowserHistorySummaryOut)
async def history_summary(user: CurrentUser, session: DbSession):
    active_connections = await session.scalar(
        select(func.count())
        .select_from(BrowserConnection)
        .where(
            BrowserConnection.user_id == user.id,
            BrowserConnection.revoked_at.is_(None),
        )
    )
    total_connections = await session.scalar(
        select(func.count())
        .select_from(BrowserConnection)
        .where(BrowserConnection.user_id == user.id)
    )
    history_count = await session.scalar(
        select(func.count())
        .select_from(BrowserHistoryPage)
        .where(BrowserHistoryPage.user_id == user.id)
    )
    return BrowserHistorySummaryOut(
        active_connection_count=active_connections,
        total_connection_count=total_connections,
        history_count=history_count,
        has_active_connection=active_connections > 0,
        has_history=history_count > 0,
    )


@router.get("", response_model=list[BrowserHistoryPageOut])
async def list_history(
    response: Response,
    user: CurrentUser,
    session: DbSession,
    q: str | None = Query(default=None, max_length=200),
    hostname: str | None = Query(default=None, max_length=253),
    date_from: date | None = None,
    date_to: date | None = None,
    sort: Literal["recent", "relevance"] = "recent",
    limit: int = Query(default=50, ge=1, le=50),
    cursor: str | None = Query(default=None, max_length=500),
):
    query = q.strip() if q and q.strip() else None
    normalized_hostname = None
    if hostname:
        try:
            normalized_hostname = normalize_history_hostname(hostname)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    signature = _history_cursor_signature(
        q=query,
        hostname=normalized_hostname,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
    )
    payload = _decode_history_cursor(cursor, signature) if cursor else None

    ranked_ids: list[int] | None = None
    if query:
        ranked_ids = await history_search.hybrid_search_ids(
            session,
            user_id=user.id,
            query=query,
            hostname=normalized_hostname,
            date_from=date_from,
            date_to=date_to,
        )
        if not ranked_ids:
            return []

    if sort == "relevance" and ranked_ids is not None:
        offset = 0
        if payload:
            if payload.get("mode") != "ranked" or not isinstance(payload.get("offset"), int):
                raise HTTPException(status_code=422, detail="Invalid history cursor")
            offset = payload["offset"]
            if offset < 0 or offset > history_search.HISTORY_SEARCH_POOL:
                raise HTTPException(status_code=422, detail="Invalid history cursor")
        page_ids = ranked_ids[offset : offset + limit + 1]
        has_more = len(page_ids) > limit
        page_ids = page_ids[:limit]
        unordered = list(
            await session.scalars(
                select(BrowserHistoryPage).where(
                    BrowserHistoryPage.user_id == user.id,
                    BrowserHistoryPage.id.in_(page_ids),
                )
            )
        )
        by_id = {page.id: page for page in unordered}
        pages = [by_id[page_id] for page_id in page_ids if page_id in by_id]
        if has_more:
            response.headers["X-Next-Cursor"] = _encode_history_cursor(
                {
                    "mode": "ranked",
                    "offset": offset + limit,
                    "signature": signature,
                }
            )
    else:
        statement = history_search.scoped_pages(
            user.id,
            hostname=normalized_hostname,
            date_from=date_from,
            date_to=date_to,
        )
        if ranked_ids is not None:
            statement = statement.where(BrowserHistoryPage.id.in_(ranked_ids))
        if payload:
            if (
                payload.get("mode") != "recent"
                or not isinstance(payload.get("last_visited_at"), str)
                or not isinstance(payload.get("id"), int)
            ):
                raise HTTPException(status_code=422, detail="Invalid history cursor")
            try:
                cursor_time = datetime.fromisoformat(payload["last_visited_at"])
                if cursor_time.utcoffset() is None:
                    raise ValueError
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid history cursor",
                ) from None
            statement = statement.where(
                or_(
                    BrowserHistoryPage.last_visited_at < cursor_time,
                    and_(
                        BrowserHistoryPage.last_visited_at == cursor_time,
                        BrowserHistoryPage.id < payload["id"],
                    ),
                )
            )
        statement = statement.order_by(
            BrowserHistoryPage.last_visited_at.desc(),
            BrowserHistoryPage.id.desc(),
        )
        pages = list(await session.scalars(statement.limit(limit + 1)))
        has_more = len(pages) > limit
        pages = pages[:limit]
        if has_more:
            last_page = pages[-1]
            response.headers["X-Next-Cursor"] = _encode_history_cursor(
                {
                    "mode": "recent",
                    "last_visited_at": last_page.last_visited_at.isoformat(),
                    "id": last_page.id,
                    "signature": signature,
                }
            )
    if not pages:
        return []

    sources: dict[int, list[str]] = {page.id: [] for page in pages}
    source_rows = await session.execute(
        select(BrowserHistoryPageConnection.page_id, BrowserConnection.name)
        .join(
            BrowserConnection,
            BrowserConnection.id == BrowserHistoryPageConnection.connection_id,
        )
        .where(BrowserHistoryPageConnection.page_id.in_(sources))
        .order_by(BrowserConnection.name)
    )
    for page_id, name in source_rows:
        if name not in sources[page_id]:
            sources[page_id].append(name)
    return [
        BrowserHistoryPageOut(
            id=page.id,
            url=page.url,
            title=page.title,
            hostname=page.hostname,
            text_excerpt=page.text_excerpt,
            first_visited_at=page.first_visited_at,
            last_visited_at=page.last_visited_at,
            visit_count=page.visit_count,
            captured_at=page.captured_at,
            source_browsers=sources[page.id],
        )
        for page in pages
    ]


@router.delete("/{page_id}", status_code=204)
async def delete_history_page(
    page_id: int,
    user: CurrentUser,
    session: DbSession,
):
    page = await session.scalar(
        select(BrowserHistoryPage).where(
            BrowserHistoryPage.id == page_id,
            BrowserHistoryPage.user_id == user.id,
        )
    )
    if page is None:
        raise HTTPException(status_code=404, detail="History page not found")
    history_settings = await _settings_for(session, user.id)
    history_settings.sync_revision += 1
    await _write_deletion(
        session,
        user_id=user.id,
        scope="page",
        scope_key=page.url_hash,
        revision=history_settings.sync_revision,
    )
    await session.delete(page)
    await session.commit()


@router.delete("", response_model=BrowserHistoryDeletionOut)
async def clear_history(
    body: BrowserHistoryClearIn,
    user: CurrentUser,
    session: DbSession,
):
    history_settings = await _settings_for(session, user.id)
    history_settings.sync_revision += 1
    scope = "domain" if body.hostname else "all"
    scope_key = body.hostname or ""
    await _write_deletion(
        session,
        user_id=user.id,
        scope=scope,
        scope_key=scope_key,
        revision=history_settings.sync_revision,
    )
    condition = BrowserHistoryPage.user_id == user.id
    if body.hostname:
        condition = condition & or_(
            BrowserHistoryPage.hostname == body.hostname,
            BrowserHistoryPage.hostname.endswith(f".{body.hostname}"),
        )
    result = await session.execute(delete(BrowserHistoryPage).where(condition))
    await session.commit()
    return BrowserHistoryDeletionOut(
        deleted_count=result.rowcount,
        sync_revision=history_settings.sync_revision,
    )


@router.post(
    "/sync/content-status",
    response_model=BrowserHistoryContentStatusOut,
    dependencies=[Depends(require_browser_history_content_enabled)],
)
async def history_content_status(
    body: BrowserHistoryContentStatusIn,
    connection: BrowserConnectionAuth,
    session: DbSession,
):
    history_settings = await _settings_for(session, connection.user_id)
    domain_rules = list(
        await session.scalars(
            select(BrowserHistoryDomainRule)
            .where(BrowserHistoryDomainRule.user_id == connection.user_id)
            .order_by(
                BrowserHistoryDomainRule.hostname,
                BrowserHistoryDomainRule.match_subdomains,
            )
        )
    )
    _, system_rules = await _system_rule_state(session, connection.user_id)
    document_hashes = set(
        await session.scalars(
            select(BrowserHistoryDocument.content_hash).where(
                BrowserHistoryDocument.user_id == connection.user_id,
                BrowserHistoryDocument.storage_status == "ready",
                BrowserHistoryDocument.content_hash.in_(body.documents),
            )
        )
    )
    image_hashes = set(
        await session.scalars(
            select(BrowserHistoryImage.image_hash).where(
                BrowserHistoryImage.user_id == connection.user_id,
                BrowserHistoryImage.storage_status == "ready",
                BrowserHistoryImage.image_hash.in_(body.images),
            )
        )
    )
    return BrowserHistoryContentStatusOut(
        documents={value: value in document_hashes for value in body.documents},
        images={value: value in image_hashes for value in body.images},
        sync_revision=history_settings.sync_revision,
        domain_rules=[BrowserHistoryDomainRuleOut.model_validate(rule) for rule in domain_rules],
        system_policy_revision=HISTORY_SYSTEM_POLICY_REVISION,
        system_rules=system_rules,
        content_capability_revision=CONTENT_CAPABILITY_REVISION,
    )


@router.put(
    "/sync/content/{content_hash}",
    response_model=BrowserHistoryDocumentUploadOut,
    dependencies=[Depends(require_browser_history_content_enabled)],
)
async def upload_history_content(
    content_hash: str,
    request: Request,
    connection: BrowserConnectionAuth,
    session: DbSession,
    ingest: Annotated[HistoryIngestService, Depends(get_history_ingest_service)],
):
    content_encoding = request.headers.get("Content-Encoding", "").strip().casefold()
    if content_encoding not in {"", "identity", "gzip"}:
        raise HTTPException(status_code=415, detail="Unsupported history content encoding")
    payload = await _read_bounded_body(
        request,
        (
            settings.history_object_compressed_max_bytes
            if content_encoding == "gzip"
            else settings.history_object_max_bytes
        ),
        label="History document",
    )
    if content_encoding == "gzip":
        try:
            payload = decompress_history_document(
                payload,
                max_bytes=settings.history_object_max_bytes,
            )
        except HistoryContentError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        document = await ingest.ingest_document(
            session,
            user_id=connection.user_id,
            claimed_hash=content_hash,
            payload=payload,
        )
    except HistoryIngestError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    connection.last_seen_at = datetime.now(UTC)
    await session.commit()
    return BrowserHistoryDocumentUploadOut(
        document_id=document.id,
        content_hash=document.content_hash,
        storage_status=document.storage_status,
    )


@router.put(
    "/sync/image/{image_hash}",
    response_model=BrowserHistoryImageUploadOut,
    dependencies=[Depends(require_browser_history_content_enabled)],
)
async def upload_history_image(
    image_hash: str,
    request: Request,
    connection: BrowserConnectionAuth,
    session: DbSession,
    ingest: Annotated[HistoryIngestService, Depends(get_history_ingest_service)],
):
    payload = await _read_bounded_body(
        request,
        settings.history_image_max_bytes,
        label="History image",
    )
    try:
        image = await ingest.ingest_image(
            session,
            user_id=connection.user_id,
            claimed_hash=image_hash,
            payload=payload,
        )
    except HistoryIngestError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    connection.last_seen_at = datetime.now(UTC)
    await session.commit()
    return BrowserHistoryImageUploadOut(
        image_id=image.id,
        image_hash=image.image_hash,
        storage_status=image.storage_status,
    )


@router.post(
    "/sync",
    response_model=BrowserHistorySyncOut,
    dependencies=[Depends(require_sync_content_length)],
    openapi_extra=SYNC_REQUEST_OPENAPI,
)
async def sync_history(
    request: Request,
    connection: BrowserConnectionAuth,
    session: DbSession,
    ingest: Annotated[
        HistoryIngestService | None,
        Depends(get_optional_history_ingest_service),
    ],
):
    await _enforce_sync_rate_limit(session, connection)
    raw_body = await request.body()
    if len(raw_body) > MAX_SYNC_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail="History sync batch exceeds 1 MiB")
    try:
        decoded = json.loads(raw_body)
        body = BrowserHistorySyncIn.model_validate(decoded)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail="Invalid history sync body") from exc

    history_settings = await _settings_for(session, connection.user_id)
    rules = list(
        await session.scalars(
            select(BrowserHistoryDomainRule)
            .where(BrowserHistoryDomainRule.user_id == connection.user_id)
            .order_by(
                BrowserHistoryDomainRule.hostname,
                BrowserHistoryDomainRule.match_subdomains,
            )
        )
    )
    deletions = list(
        await session.scalars(
            select(BrowserHistoryDeletion).where(
                BrowserHistoryDeletion.user_id == connection.user_id
            )
        )
    )
    disabled_system_rule_ids, system_rules = await _system_rule_state(
        session,
        connection.user_id,
    )
    accepted: list[BrowserHistorySyncAcceptedOut] = []
    rejected: list[BrowserHistorySyncRejectedOut] = []
    now = datetime.now(UTC)

    for index, raw in enumerate(body.records):
        record_id = _record_id(raw, index)
        try:
            capture = BrowserHistoryCaptureIn.model_validate(raw)
        except ValidationError as exc:
            rejected.append(
                BrowserHistorySyncRejectedOut(
                    record_id=record_id,
                    code="invalid",
                    detail=_validation_detail(exc),
                )
            )
            continue
        try:
            page, normalized = await persist_capture(
                session,
                connection,
                capture,
                rules=rules,
                deletions=deletions,
                disabled_system_rule_ids=disabled_system_rule_ids,
                ingest=ingest,
                content_pipeline_enabled=(
                    settings.browser_history_content_enabled and CONTENT_CAPABILITY_REVISION >= 2
                ),
                legacy_inline_content_enabled=settings.browser_history_legacy_inline_enabled,
                now=now,
            )
        except SyncRejection as exc:
            rejected.append(
                BrowserHistorySyncRejectedOut(
                    record_id=capture.record_id,
                    code=exc.code,
                    detail=exc.detail,
                )
            )
            continue
        accepted.append(
            BrowserHistorySyncAcceptedOut(
                record_id=capture.record_id,
                page_id=page.id,
                url_hash=normalized.url_hash,
            )
        )

    connection.last_seen_at = now
    await session.commit()
    return BrowserHistorySyncOut(
        accepted=accepted,
        rejected=rejected,
        sync_revision=history_settings.sync_revision,
        domain_rules=[BrowserHistoryDomainRuleOut.model_validate(rule) for rule in rules],
        system_policy_revision=HISTORY_SYSTEM_POLICY_REVISION,
        system_rules=system_rules,
        content_capability_revision=CONTENT_CAPABILITY_REVISION,
        server_time=now,
    )


@router.get("/sync/status", response_model=BrowserHistorySyncStatusOut)
async def sync_status(
    connection: BrowserConnectionAuth,
    session: DbSession,
):
    history_settings = await _settings_for(session, connection.user_id)
    user_name = await session.scalar(select(User.name).where(User.id == connection.user_id))
    rules = (
        await session.scalars(
            select(BrowserHistoryDomainRule)
            .where(BrowserHistoryDomainRule.user_id == connection.user_id)
            .order_by(
                BrowserHistoryDomainRule.hostname,
                BrowserHistoryDomainRule.match_subdomains,
            )
        )
    ).all()
    _, system_rules = await _system_rule_state(session, connection.user_id)
    connection.last_seen_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(connection)
    return BrowserHistorySyncStatusOut(
        connection=_connection_out(connection),
        user_name=user_name,
        settings=_settings_out(history_settings),
        domain_rules=[BrowserHistoryDomainRuleOut.model_validate(rule) for rule in rules],
        system_policy_revision=HISTORY_SYSTEM_POLICY_REVISION,
        system_rules=system_rules,
        content_capability_revision=CONTENT_CAPABILITY_REVISION,
    )
