"""Canonical history-document and raster validation.

The browser supplies untrusted bytes and a claimed digest. This module is the
single server-side authority for canonicalization, hashing, bounded previews,
and safe image metadata.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import unicodedata
import warnings
from dataclasses import dataclass
from typing import Any, Literal

from PIL import Image, UnidentifiedImageError

from .history_policy import sanitize_capture_text

HISTORY_CONTENT_HASH_PREFIX = b"newsread-history-content-v1\0"
HISTORY_SCHEMA_VERSION = 1
MAX_HISTORY_BLOCKS = 512
MAX_HISTORY_BLOCK_CHARS = 8_000
MAX_HISTORY_DOCUMENT_CHARS = 200_000
MAX_HISTORY_LANGUAGE_CHARS = 35
MAX_HISTORY_IMAGE_DIMENSION = 640
MAX_HISTORY_IMAGE_PIXELS = MAX_HISTORY_IMAGE_DIMENSION**2

HistoryBlockKind = Literal["heading", "paragraph", "list_item", "quote", "code"]
_BLOCK_KINDS = {"heading", "paragraph", "list_item", "quote", "code"}
_EXTRACTION_VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,34}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class HistoryContentError(ValueError):
    """Safe validation failure for one untrusted history object."""


@dataclass(frozen=True)
class CanonicalHistoryDocument:
    value: dict[str, Any]
    canonical_bytes: bytes
    compressed_bytes: bytes
    content_hash: str
    character_count: int
    text_excerpt: str
    search_text: str
    extraction_version: str


@dataclass(frozen=True)
class ValidatedHistoryImage:
    image_hash: str
    format: Literal["png", "jpeg", "webp"]
    content_type: str
    width: int
    height: int
    byte_size: int


def require_history_hash(value: str, *, label: str = "history object hash") -> str:
    if not _HASH_RE.fullmatch(value):
        raise HistoryContentError(f"{label} must be 64 lowercase hex characters")
    return value


def canonicalize_history_document(payload: bytes) -> CanonicalHistoryDocument:
    try:
        raw = json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise HistoryContentError("history document must be valid UTF-8 JSON") from exc
    return canonicalize_history_document_value(raw)


def canonicalize_history_document_value(raw: Any) -> CanonicalHistoryDocument:
    if not isinstance(raw, dict):
        raise HistoryContentError("history document must be a JSON object")
    if set(raw) != {
        "schema_version",
        "extraction_version",
        "content_type",
        "language",
        "blocks",
    }:
        raise HistoryContentError("history document has unknown or missing fields")
    if raw["schema_version"] != HISTORY_SCHEMA_VERSION:
        raise HistoryContentError("unsupported history document schema version")

    extraction_version = raw["extraction_version"]
    if not isinstance(extraction_version, str) or not _EXTRACTION_VERSION_RE.fullmatch(
        extraction_version
    ):
        raise HistoryContentError("history document extraction version is invalid")
    content_type = raw["content_type"]
    if content_type not in {"article", "page", "legacy"}:
        raise HistoryContentError("history document content type is invalid")
    language = raw["language"]
    if not isinstance(language, str) or (language and not _LANGUAGE_RE.fullmatch(language)):
        raise HistoryContentError("history document language is invalid")
    language = language.lower()

    raw_blocks = raw["blocks"]
    if not isinstance(raw_blocks, list) or not 1 <= len(raw_blocks) <= MAX_HISTORY_BLOCKS:
        raise HistoryContentError("history document must contain 1 to 512 blocks")

    blocks: list[dict[str, str]] = []
    total_characters = 0
    for index, raw_block in enumerate(raw_blocks, start=1):
        if not isinstance(raw_block, dict) or set(raw_block) != {"id", "kind", "text"}:
            raise HistoryContentError("history document block has invalid fields")
        block_id = f"b{index:04d}"
        if raw_block["id"] != block_id:
            raise HistoryContentError("history document block ids must be sequential")
        kind = raw_block["kind"]
        if kind not in _BLOCK_KINDS:
            raise HistoryContentError("history document block kind is invalid")
        text = raw_block["text"]
        if not isinstance(text, str):
            raise HistoryContentError("history document block text must be a string")
        normalized = _normalize_block_text(text, kind)
        if not normalized:
            raise HistoryContentError("history document blocks must not be empty")
        if len(normalized) > MAX_HISTORY_BLOCK_CHARS:
            raise HistoryContentError("history document block is too long")
        total_characters += len(normalized)
        if total_characters > MAX_HISTORY_DOCUMENT_CHARS:
            raise HistoryContentError("history document has too much text")
        blocks.append({"id": block_id, "kind": kind, "text": normalized})

    value = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "extraction_version": extraction_version,
        "content_type": content_type,
        "language": language,
        "blocks": blocks,
    }
    canonical_bytes = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    search_text = "\n".join(block["text"] for block in blocks)
    return CanonicalHistoryDocument(
        value=value,
        canonical_bytes=canonical_bytes,
        compressed_bytes=gzip.compress(canonical_bytes, compresslevel=6, mtime=0),
        content_hash=hashlib.sha256(HISTORY_CONTENT_HASH_PREFIX + canonical_bytes).hexdigest(),
        character_count=total_characters,
        text_excerpt=sanitize_capture_text(search_text)[:500],
        search_text=search_text,
        extraction_version=extraction_version,
    )


def legacy_history_document(text: str) -> CanonicalHistoryDocument:
    normalized = sanitize_capture_text(text)
    if not normalized:
        raise HistoryContentError("legacy history document has no text")
    return canonicalize_history_document_value(
        {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "extraction_version": "history-inline-v1",
            "content_type": "legacy",
            "language": "",
            "blocks": [{"id": "b0001", "kind": "paragraph", "text": normalized}],
        }
    )


def decompress_history_document(value: bytes, *, max_bytes: int) -> bytes:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(value)) as source:
            decoded = source.read(max_bytes + 1)
    except (gzip.BadGzipFile, EOFError, OSError) as exc:
        raise HistoryContentError("stored history document is corrupt") from exc
    if len(decoded) > max_bytes:
        raise HistoryContentError("stored history document exceeds its size limit")
    return decoded


def validate_history_image(payload: bytes, *, max_bytes: int) -> ValidatedHistoryImage:
    if not payload or len(payload) > max_bytes:
        raise HistoryContentError("history image exceeds its byte limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                width, height = image.size
                detected_format = (image.format or "").upper()
                if detected_format not in {"PNG", "JPEG", "WEBP"}:
                    raise HistoryContentError("history image format is not allowed")
                if (
                    width < 1
                    or height < 1
                    or width > MAX_HISTORY_IMAGE_DIMENSION
                    or height > MAX_HISTORY_IMAGE_DIMENSION
                    or width * height > MAX_HISTORY_IMAGE_PIXELS
                ):
                    raise HistoryContentError("history image dimensions exceed the limit")
                if getattr(image, "is_animated", False):
                    raise HistoryContentError("animated history images are not allowed")
                image.verify()
    except HistoryContentError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise HistoryContentError("history image is not a valid raster") from exc

    normalized_format = detected_format.lower()
    content_type = {
        "png": "image/png",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
    }[normalized_format]
    return ValidatedHistoryImage(
        image_hash=hashlib.sha256(payload).hexdigest(),
        format=normalized_format,
        content_type=content_type,
        width=width,
        height=height,
        byte_size=len(payload),
    )


def _normalize_block_text(value: str, kind: str) -> str:
    value = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    cleaned: list[str] = []
    for char in value:
        if char in {"\n", "\t"}:
            cleaned.append(char)
        elif unicodedata.category(char).startswith("C"):
            continue
        else:
            cleaned.append(char)
    value = "".join(cleaned)
    if kind != "code":
        return " ".join(value.split())
    lines = [" ".join(line.split()) for line in value.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HistoryContentError("history document contains duplicate JSON keys")
        result[key] = value
    return result
