"""PDF documents linked straight from a feed: read their text, not their bytes.

Plenty of feeds point an item at a paper, a spec or a zine rather than at a
page. The HTML path used to "succeed" on those — Scrapling downloads the file,
trafilatura extracts something from the bytes, and 24k characters of
`%PDF-1.7 /Filter /FlateDecode` reach the model, which then dutifully reports
that it was handed a binary. This module is the branch that keeps that from
happening: recognize the document, pull its text out with pypdf, and let the
caller treat the result exactly like article prose.
"""

import asyncio
import logging
import re
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# Every PDF starts with this signature, and it is the only reliable test:
# a URL may have no .pdf suffix (arxiv, content-negotiating CDNs) and a
# Content-Type header may say application/octet-stream.
MAGIC = b"%PDF-"

# The file is already in memory by the time we look at it (the fetcher does
# not stream), so this bounds pypdf's work and the row we store, not the
# download itself. Sized off a real refusal rather than a round number: the
# Paged Out zine ships at 42 MB, which a 40 MiB cap turned away — a document
# worth reading is not pathological just because it is large.
MAX_BYTES = 64 * 1024 * 1024

# Matches youtube.MAX_TRANSCRIPT_CHARS in spirit: clip_for_llm cuts to 24k
# before any model sees this, so the cap only keeps a 700-page proceedings
# volume from bloating the row. Extraction stops as soon as it is reached.
MAX_CHARS = 100_000

# Pages are read one at a time until MAX_CHARS; this is the backstop for a
# pathological file whose pages each yield almost nothing.
MAX_PAGES = 500

_PDF_PATH = re.compile(r"\.pdf$", re.IGNORECASE)

# Characters a document's text layer can carry that a Postgres text column
# cannot: NUL is rejected outright ("invalid byte sequence for encoding UTF8:
# 0x00") and takes the whole transaction with it, which left the article
# unstamped and re-fetched on every worker cycle. The rest of the C0 range and
# lone surrogates are stripped in the same pass — none of them is prose, and a
# surrogate would fail to encode on the way out.
_UNSTORABLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]")

# A PDF's /Title is whatever produced it, and half the time that is the
# authoring tool talking to itself — "Microsoft Word - draft3.docx", the
# InDesign file name, LaTeX's "untitled". Those must not become the article's
# headline (the URL importer adopts the title outright), and dropping one only
# leaves the feed's own title in place.
_PRODUCER_TITLE = re.compile(
    r"^(microsoft word|microsoft powerpoint|untitled|document\d*|print|slide\s*\d+)\b"
    r"|\.(docx?|pptx?|indd|tex|pages|rtf|qxd)$",
    re.IGNORECASE,
)


def looks_like_pdf(url: str) -> bool:
    """True when the URL's path ends in .pdf.

    A hint, not the test — `is_pdf` decides what a fetched body actually is.
    This exists for the paths that have no bytes to look at: deciding why a
    document came back empty, and choosing the skip reason for it.
    """
    if not url:
        return False
    try:
        return bool(_PDF_PATH.search(urlsplit(url).path))
    except ValueError:
        return False


def is_pdf(body: bytes | None, content_type: str | None = None) -> bool:
    """True when this response is a PDF document.

    The signature wins over the header: servers mislabel downloads far more
    often than a file lies about its own magic number.
    """
    if body and body[:1024].lstrip()[: len(MAGIC)] == MAGIC:
        return True
    return bool(content_type) and "application/pdf" in content_type.lower()


def _extract(body: bytes) -> tuple[str, str | None]:
    """Blocking: pypdf is synchronous and CPU-bound on big documents."""
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(body), strict=False)
    if reader.is_encrypted:
        # An owner-password PDF (printing/copying restricted, opening not)
        # decrypts with the empty user password; a real one raises, and there
        # is nothing further we can do with it.
        reader.decrypt("")

    title: str | None = None
    try:
        meta = reader.metadata
        if meta and meta.title:
            candidate = _UNSTORABLE.sub("", str(meta.title)).strip()
            if candidate and not _PRODUCER_TITLE.search(candidate):
                title = candidate
    except Exception:  # pypdf raises a variety of things on damaged metadata
        pass

    parts: list[str] = []
    total = 0
    for page in reader.pages[:MAX_PAGES]:
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            # One damaged page must not cost us the other four hundred.
            logger.debug("Skipping an unreadable PDF page: %s", exc)
            continue
        if not text:
            continue
        parts.append(text)
        total += len(text)
        if total >= MAX_CHARS:
            break

    joined = re.sub(r"[ \t]+", " ", _UNSTORABLE.sub("", "\n".join(parts)))
    return re.sub(r"\n{3,}", "\n\n", joined).strip()[:MAX_CHARS], title


async def extract_text(body: bytes) -> tuple[str, str | None]:
    """The document's text and its metadata title, or ("", None).

    Empty covers every way a PDF can be unreadable — encrypted, damaged, or
    scanned page images with no text layer. The caller can't act differently
    on any of them, so they share one outcome rather than three exceptions.
    """
    if len(body) > MAX_BYTES:
        logger.info("PDF is %d bytes, past the %d cap; not extracting", len(body), MAX_BYTES)
        return "", None
    try:
        return await asyncio.to_thread(_extract, body)
    except Exception as exc:
        logger.info("PDF could not be read: %s", exc)
        return "", None
