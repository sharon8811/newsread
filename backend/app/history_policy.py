"""Defensive normalization and sanitization for untrusted browser captures."""

import hashlib
import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlsplit, urlunsplit

MAX_HISTORY_URL_CHARS = 2048
MAX_HISTORY_TITLE_CHARS = 512
MAX_HISTORY_TEXT_CHARS = 6000
MAX_HISTORY_EXCERPT_CHARS = 500
MAX_HISTORY_VISIT_COUNT = 1_000_000
EARLIEST_HISTORY_AT = datetime(2000, 1, 1, tzinfo=UTC)

_HOST_LABEL = re.compile(r"^[a-z0-9-]+$")
_BAD_PERCENT_ESCAPE = re.compile(r"%(?![0-9a-fA-F]{2})")
_SENSITIVE_QUERY_NAME = re.compile(
    r"(^|[_-])("
    r"access_?token|token|auth|authorization|session|session_?id|sid|"
    r"code|key|api_?key|signature|sig|secret|password|passwd"
    r")([_-]|$)"
)
_TRACKING_QUERY_NAMES = {
    "dclid",
    "fbclid",
    "gclid",
    "igshid",
    "msclkid",
    "twclid",
}
_RESERVED_HOST_SUFFIXES = {
    "example",
    "home",
    "home.arpa",
    "internal",
    "invalid",
    "lan",
    "local",
    "localhost",
    "onion",
    "test",
}


class HistoryCaptureError(ValueError):
    """Safe validation failure that can be returned for one sync record."""


@dataclass(frozen=True)
class NormalizedHistoryUrl:
    url: str
    hostname: str
    url_hash: str


def normalize_history_hostname(value: str) -> str:
    hostname = value.strip().lower().rstrip(".")
    if hostname.startswith("*."):
        hostname = hostname[2:]
    if not hostname or any(char in hostname for char in "/:@[]"):
        raise ValueError("enter a hostname without a scheme, path, or port")
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("enter a valid hostname") from exc
    if len(hostname) > 253:
        raise ValueError("hostname is too long")
    labels = hostname.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not _HOST_LABEL.fullmatch(label)
        for label in labels
    ):
        raise ValueError("enter a valid hostname")
    return hostname


def _public_hostname(hostname: str) -> tuple[str, bool]:
    """Return the normalized host and whether it is an IP literal."""
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if all(label.isdigit() for label in hostname.split(".")):
            raise HistoryCaptureError("enter a valid public hostname") from None
        try:
            normalized = normalize_history_hostname(hostname)
        except ValueError as exc:
            raise HistoryCaptureError(str(exc)) from exc
        if "." not in normalized:
            raise HistoryCaptureError("single-label hostnames are not captured") from None
        if any(
            normalized == suffix or normalized.endswith(f".{suffix}")
            for suffix in _RESERVED_HOST_SUFFIXES
        ):
            raise HistoryCaptureError(
                "reserved and private-network hostnames are not captured"
            ) from None
        return normalized, False
    if not address.is_global:
        raise HistoryCaptureError("local and private-network URLs are not captured")
    return address.compressed.lower(), True


def _query_name_is_private(name: str) -> bool:
    lowered = name.casefold()
    return (
        lowered.startswith(("utm_", "mc_"))
        or lowered in _TRACKING_QUERY_NAMES
        or bool(_SENSITIVE_QUERY_NAME.search(lowered))
    )


def validate_normalized_history_url(value: str) -> NormalizedHistoryUrl:
    """Validate the extension-owned final URL without silently rewriting it."""
    if not value or len(value) > MAX_HISTORY_URL_CHARS:
        raise HistoryCaptureError("URL is empty or too long")
    if any(char.isspace() or unicodedata.category(char).startswith("C") for char in value):
        raise HistoryCaptureError("URL contains whitespace or control characters")
    if "\\" in value or _BAD_PERCENT_ESCAPE.search(value):
        raise HistoryCaptureError("URL contains invalid escaping")

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise HistoryCaptureError("URL is malformed") from exc
    if parsed.scheme not in {"http", "https"}:
        raise HistoryCaptureError("only http and https URLs are captured")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise HistoryCaptureError("URL must have a public host and no credentials")
    if parsed.fragment:
        raise HistoryCaptureError("URL fragments must be removed before sync")

    hostname, is_ip = _public_hostname(parsed.hostname)
    if (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443):
        raise HistoryCaptureError("default ports must be removed before sync")
    if port is not None and not 1 <= port <= 65535:
        raise HistoryCaptureError("URL port is invalid")

    for name, _ in parse_qsl(parsed.query, keep_blank_values=True):
        if _query_name_is_private(name):
            raise HistoryCaptureError("tracking and sensitive query parameters must be removed")

    display_host = f"[{hostname}]" if is_ip and ":" in hostname else hostname
    netloc = f"{display_host}:{port}" if port is not None else display_host
    path = parsed.path or "/"
    normalized = urlunsplit((parsed.scheme, netloc, path, parsed.query, ""))
    if value != normalized:
        raise HistoryCaptureError(
            "URL must already use lowercase scheme/host, punycode, and a normalized path"
        )
    return NormalizedHistoryUrl(
        url=normalized,
        hostname=hostname,
        url_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    )


def sanitize_capture_text(value: str) -> str:
    """Strip control/bidi/zero-width characters and collapse whitespace."""
    cleaned = []
    for char in value:
        if char.isspace():
            cleaned.append(" ")
        elif unicodedata.category(char).startswith("C"):
            continue
        else:
            cleaned.append(char)
    return " ".join("".join(cleaned).split())


def clamp_history_timestamp(value: datetime, now: datetime) -> datetime:
    value = value.astimezone(UTC)
    return min(max(value, EARLIEST_HISTORY_AT), now)


def history_embedding_text(title: str, hostname: str, text: str) -> str:
    return f"{title}\n\n{hostname}\n\n{text}"[:MAX_HISTORY_TEXT_CHARS]


def history_content_hash(title: str, hostname: str, text: str) -> str:
    content = history_embedding_text(title, hostname, text)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def domain_matches(hostname: str, rule_hostname: str, match_subdomains: bool = True) -> bool:
    return hostname == rule_hostname or (
        match_subdomains and hostname.endswith(f".{rule_hostname}")
    )
