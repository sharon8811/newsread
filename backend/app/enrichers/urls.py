"""URL canonicalization and anchor extraction for entity matching."""

import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlsplit

from .base import CleanUrl

_TRACKING_PARAMS = re.compile(r"^(utm_\w+|fbclid|gclid|ref|ref_src|feature)$", re.IGNORECASE)

MAX_ANCHORS = 50


def clean_url(raw: str) -> CleanUrl | None:
    if not raw:
        return None
    try:
        parts = urlsplit(raw.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = parts.hostname or ""
    host = host.lower()
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    if not host:
        return None
    path = parts.path.rstrip("/")
    query = {
        k: v for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not _TRACKING_PARAMS.match(k)
    }
    return CleanUrl(raw=raw, host=host, path=path, query=query)


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or len(self.hrefs) >= MAX_ANCHORS:
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)
                break


_TEXT_URL_RE = re.compile(r"https?://[^\s<>\"')\]}]+")


def extract_text_links(text: str) -> list[str]:
    """Ordered, de-duplicated URLs found in plain text. The fetched full text
    is extracted prose with no anchor markup, so bare URLs are all we can
    recover; trailing punctuation that prose attaches to a URL is stripped."""
    if not text:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for match in _TEXT_URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?")
        if url and url not in seen:
            seen.add(url)
            ordered.append(url)
            if len(ordered) >= MAX_ANCHORS:
                break
    return ordered


def extract_links(content_html: str) -> list[str]:
    """Ordered, de-duplicated hrefs from sanitized article HTML."""
    if not content_html:
        return []
    parser = _AnchorParser()
    try:
        parser.feed(content_html)
    except Exception:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for href in parser.hrefs:
        if href not in seen:
            seen.add(href)
            ordered.append(href)
    return ordered
