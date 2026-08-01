from io import BytesIO

import pytest

from app import pdf


def _document(*, pages: list[str] | None = None, title: str | None = None, password: str = ""):
    """A real PDF, built with pypdf's own writer.

    Handwritten byte fixtures would only prove that our parser matches our
    fixture; these go through the same reader a fetched document does.
    """
    from pypdf import PdfWriter
    from pypdf.generic import RectangleObject

    writer = PdfWriter()
    for text in pages or [""]:
        page = writer.add_blank_page(width=300, height=300)
        if text:
            # add_blank_page gives an empty content stream; a text layer is
            # what separates a readable document from a scan.
            page.mediabox = RectangleObject((0, 0, 300, 300))
            writer.pages[-1] = page
    if title:
        writer.add_metadata({"/Title": title})
    if password:
        writer.encrypt(password)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_looks_like_pdf_reads_the_path_only():
    assert pdf.looks_like_pdf("https://x/paper.pdf")
    assert pdf.looks_like_pdf("https://x/paper.PDF?download=1")
    # The query string is not the path: a viewer URL is a page, not a document.
    assert not pdf.looks_like_pdf("https://x/viewer?file=paper.pdf")
    assert not pdf.looks_like_pdf("https://arxiv.org/pdf/1706.03762")
    assert not pdf.looks_like_pdf("")
    assert not pdf.looks_like_pdf("http://[::1")  # unparseable, not a document


def test_is_pdf_trusts_the_signature_over_the_header():
    body = _document()
    # arxiv and friends serve documents as octet-stream; the bytes still say.
    assert pdf.is_pdf(body, "application/octet-stream")
    assert pdf.is_pdf(b"\n  %PDF-1.4 ...", None)
    assert not pdf.is_pdf(b"<!doctype html><html>", "text/html")
    assert not pdf.is_pdf(None, None)
    # And a header alone is enough when the body never arrived intact.
    assert pdf.is_pdf(b"", "application/pdf; charset=binary")


async def test_extract_text_returns_the_metadata_title():
    text, title = await pdf.extract_text(_document(title="A Paper"))
    assert title == "A Paper"
    assert text == ""  # blank pages carry no text layer


@pytest.mark.parametrize(
    "stored",
    [
        "Microsoft Word - draft3.docx",
        "untitled",
        "Q3-report.indd",
        "slide 1",
    ],
)
async def test_extract_text_drops_a_producer_title(stored):
    # The URL importer adopts this title outright; the authoring tool talking
    # to itself must not become the article's headline.
    _, title = await pdf.extract_text(_document(title=stored))
    assert title is None


async def test_extract_text_keeps_a_real_title_that_merely_mentions_a_tool():
    _, title = await pdf.extract_text(_document(title="Writing well in Microsoft Word"))
    assert title == "Writing well in Microsoft Word"


async def test_extract_text_is_empty_for_an_encrypted_document():
    # Nothing else to try: a real user password is not ours to guess.
    assert await pdf.extract_text(_document(password="secret")) == ("", None)


async def test_extract_text_is_empty_for_a_damaged_document():
    assert await pdf.extract_text(b"%PDF-1.7 truncated before anything useful") == ("", None)


async def test_extract_text_refuses_an_oversized_document(monkeypatch):
    monkeypatch.setattr(pdf, "MAX_BYTES", 10)
    assert await pdf.extract_text(_document(title="A Paper")) == ("", None)


async def test_extract_text_stops_at_the_character_cap(monkeypatch):
    calls = 0

    class _Page:
        def extract_text(self):
            nonlocal calls
            calls += 1
            return "x" * 60

    class _Reader:
        is_encrypted = False
        metadata = None
        pages = [_Page() for _ in range(50)]

        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(pdf, "MAX_CHARS", 100)
    monkeypatch.setattr("pypdf.PdfReader", _Reader)
    text, _ = await pdf.extract_text(b"%PDF-1.7 stand-in")
    assert len(text) == 100
    # A 700-page volume must not be read to the end for a 24k-character clip.
    assert calls == 2


async def test_extract_text_survives_one_damaged_page(monkeypatch):
    class _Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            if self.text is None:
                raise ValueError("damaged page")
            return self.text

    class _Reader:
        is_encrypted = False
        metadata = None
        pages = [_Page("first"), _Page(None), _Page("third")]

        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr("pypdf.PdfReader", _Reader)
    text, _ = await pdf.extract_text(b"%PDF-1.7 stand-in")
    assert text == "first\nthird"
