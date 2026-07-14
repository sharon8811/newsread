"""Enricher unit tests: URL cleaning, badge rendering, and per-site fetch()
with the outbound HTTP mocked by respx."""

import httpx
import pytest
import respx

from app.enrichers import (
    ENRICHERS,
    badge_for,
    clean_url,
    extract_links,
    extract_text_links,
    match_url,
)
from app.enrichers.arxiv import ArxivEnricher, _parse_id
from app.enrichers.base import EnrichError
from app.enrichers.github import GitHubEnricher
from app.enrichers.huggingface import (
    HFDatasetEnricher,
    HFModelEnricher,
    _license_of,
)
from app.enrichers.npm import NpmEnricher
from app.enrichers.pypi_pkg import PyPIEnricher, _normalize
from app.enrichers.youtube import YouTubeEnricher

# --- clean_url / extract_links ---


def test_clean_url_strips_www_and_tracking():
    u = clean_url("https://www.example.com/a/b/?utm_source=x&keep=1#frag")
    assert u.host == "example.com"
    assert u.path == "/a/b"
    assert u.query == {"keep": "1"}


def test_clean_url_strips_m_prefix():
    assert clean_url("https://m.youtube.com/watch").host == "youtube.com"


def test_clean_url_rejects_non_http():
    assert clean_url("mailto:x@y.com") is None
    assert clean_url("ftp://host/x") is None
    assert clean_url("") is None


def test_clean_url_rejects_empty_host():
    assert clean_url("http:///path") is None


def test_extract_links_dedupes_and_orders():
    html = '<a href="/a">1</a><a href="/b">2</a><a href="/a">dup</a>'
    assert extract_links(html) == ["/a", "/b"]


def test_extract_links_empty():
    assert extract_links("") == []


def test_extract_links_caps_at_50():
    html = "".join(f'<a href="/{i}">x</a>' for i in range(80))
    assert len(extract_links(html)) == 50


def test_extract_links_handles_anchor_without_href():
    assert extract_links('<a name="x">no href</a><a href="/y">y</a>') == ["/y"]


# --- extract_text_links ---


def test_extract_text_links_strips_prose_punctuation():
    text = (
        "The code is at https://github.com/a/b. See also "
        "(https://arxiv.org/abs/1706.03762) and https://github.com/a/b again."
    )
    assert extract_text_links(text) == [
        "https://github.com/a/b",
        "https://arxiv.org/abs/1706.03762",
    ]


def test_extract_text_links_empty_and_caps():
    assert extract_text_links("") == []
    assert extract_text_links("no urls here") == []
    text = " ".join(f"https://x.com/{i}" for i in range(80))
    assert len(extract_text_links(text)) == 50


# --- badge_for ---


def test_badge_for_unknown_kind():
    assert badge_for("nonexistent", {"x": 1}) == {}


def test_badge_for_empty_data():
    assert badge_for("github", {}) == {}


def test_badge_for_drops_none_values():
    b = badge_for("github", {"full_name": "a/b", "stargazers_count": None})
    assert b == {"label": "a/b"}


def test_badge_for_swallows_enricher_errors(monkeypatch):
    enricher = next(e for e in ENRICHERS if e.kind == "github")
    monkeypatch.setattr(enricher, "badge", lambda data: 1 / 0)
    assert badge_for("github", {"x": 1}) == {}


# --- GitHub ---


@respx.mock
async def test_github_fetch_success():
    respx.get("https://api.github.com/repos/pytorch/pytorch").mock(
        return_value=httpx.Response(
            200,
            json={
                "full_name": "pytorch/pytorch",
                "description": "Tensors",
                "stargazers_count": 80000,
                "forks_count": 2000,
                "open_issues_count": 100,
                "language": "Python",
                "license": {"spdx_id": "BSD-3-Clause"},
                "pushed_at": "2024-01-01T00:00:00Z",
                "archived": False,
                "topics": list(range(20)),
                "homepage": "https://pytorch.org",
                "subscribers_count": 500,
            },
        )
    )
    async with httpx.AsyncClient() as client:
        data = await GitHubEnricher().fetch("pytorch/pytorch", client)
    assert data["full_name"] == "pytorch/pytorch"
    assert data["license"] == "BSD-3-Clause"
    assert len(data["topics"]) == 8  # capped
    assert GitHubEnricher().badge(data)["stars"] == 80000


@respx.mock
async def test_github_fetch_noassertion_license():
    respx.get("https://api.github.com/repos/a/b").mock(
        return_value=httpx.Response(200, json={"license": {"spdx_id": "NOASSERTION"}})
    )
    async with httpx.AsyncClient() as client:
        data = await GitHubEnricher().fetch("a/b", client)
    assert data["license"] is None


@respx.mock
@pytest.mark.parametrize("status", [404, 451])
async def test_github_fetch_not_found(status):
    respx.get("https://api.github.com/repos/a/b").mock(return_value=httpx.Response(status))
    async with httpx.AsyncClient() as client:
        with pytest.raises(EnrichError):
            await GitHubEnricher().fetch("a/b", client)


@respx.mock
@pytest.mark.parametrize("status", [403, 429])
async def test_github_fetch_rate_limited(status):
    respx.get("https://api.github.com/repos/a/b").mock(return_value=httpx.Response(status))
    async with httpx.AsyncClient() as client:
        with pytest.raises(EnrichError):
            await GitHubEnricher().fetch("a/b", client)


@respx.mock
async def test_github_fetch_sends_token(monkeypatch):
    monkeypatch.setattr("app.enrichers.github.settings.github_token", "tok123")
    route = respx.get("https://api.github.com/repos/a/b").mock(
        return_value=httpx.Response(200, json={})
    )
    async with httpx.AsyncClient() as client:
        await GitHubEnricher().fetch("a/b", client)
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok123"


def test_github_entity_url():
    assert GitHubEnricher().entity_url("a/b") == "https://github.com/a/b"


# --- arxiv ---


def test_parse_id_variants():
    assert _parse_id("/abs/1706.03762v3") == "1706.03762"
    assert _parse_id("/pdf/2301.12345.pdf") == "2301.12345"
    assert _parse_id("/abs/cs/9901002") == "cs/9901002"
    assert _parse_id("/list/cs.CL/recent") is None
    assert _parse_id("/abs") is None


ARXIV_ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <title>Attention Is All You Need</title>
    <summary>We  propose   the Transformer.</summary>
    <published>2017-06-12T00:00:00Z</published>
    <updated>2017-06-13T00:00:00Z</updated>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <arxiv:primary_category term="cs.CL"/>
    <category term="cs.CL"/>
    <category term="cs.LG"/>
  </entry>
</feed>"""


@respx.mock
async def test_arxiv_fetch_success(monkeypatch):
    monkeypatch.setattr("app.enrichers.arxiv._last_request", 0.0)
    monkeypatch.setattr("app.enrichers.arxiv._MIN_INTERVAL", 0.0)
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text=ARXIV_ATOM)
    )
    async with httpx.AsyncClient() as client:
        data = await ArxivEnricher().fetch("1706.03762", client)
    assert data["title"] == "Attention Is All You Need"
    assert data["abstract"] == "We propose the Transformer."
    assert data["primary_category"] == "cs.CL"
    assert data["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    badge = ArxivEnricher().badge(data)
    assert badge["authors_short"] == "Vaswani et al."


@respx.mock
async def test_arxiv_fetch_not_found(monkeypatch):
    monkeypatch.setattr("app.enrichers.arxiv._MIN_INTERVAL", 0.0)
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(EnrichError):
            await ArxivEnricher().fetch("9999.99999", client)


@respx.mock
async def test_arxiv_fetch_error_title(monkeypatch):
    monkeypatch.setattr("app.enrichers.arxiv._MIN_INTERVAL", 0.0)
    xml = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Error</title></entry></feed>'
    respx.get("https://export.arxiv.org/api/query").mock(return_value=httpx.Response(200, text=xml))
    async with httpx.AsyncClient() as client:
        with pytest.raises(EnrichError):
            await ArxivEnricher().fetch("bad", client)


def test_arxiv_badge_single_author():
    assert ArxivEnricher().badge({"authors": ["Jane Doe"]})["authors_short"] == "Doe"


def test_arxiv_badge_no_authors():
    assert ArxivEnricher().badge({"title": "T"})["authors_short"] is None


def test_arxiv_entity_url():
    assert ArxivEnricher().entity_url("1706.03762") == "https://arxiv.org/abs/1706.03762"


# --- Hugging Face ---


def test_license_of_from_carddata():
    assert _license_of({"cardData": {"license": "mit"}}) == "mit"
    assert _license_of({"cardData": {"license": ["apache-2.0", "mit"]}}) == "apache-2.0"


def test_license_of_from_tags():
    assert _license_of({"tags": ["license:bsd-3-clause", "x"]}) == "bsd-3-clause"


def test_license_of_none():
    assert _license_of({"tags": ["not-a-license"]}) is None


@respx.mock
async def test_hf_model_fetch():
    respx.get("https://huggingface.co/api/models/Qwen/Qwen2.5-7B").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "Qwen/Qwen2.5-7B",
                "downloads": 1000,
                "likes": 50,
                "pipeline_tag": "text-generation",
                "lastModified": "2024-01-01",
                "library_name": "transformers",
                "gated": False,
                "cardData": {"license": "apache-2.0"},
                "safetensors": {"total": 7_000_000_000},
            },
        )
    )
    async with httpx.AsyncClient() as client:
        data = await HFModelEnricher().fetch("Qwen/Qwen2.5-7B", client)
    assert data["license"] == "apache-2.0"
    assert data["params"] == 7_000_000_000
    assert HFModelEnricher().badge(data)["downloads"] == 1000


@respx.mock
async def test_hf_dataset_fetch():
    respx.get("https://huggingface.co/api/datasets/allenai/c4").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "allenai/c4",
                "downloads": 500,
                "likes": 20,
                "lastModified": "2024-01-01",
                "gated": False,
                "cardData": {"task_categories": ["text"], "size_categories": ["1B"]},
            },
        )
    )
    async with httpx.AsyncClient() as client:
        data = await HFDatasetEnricher().fetch("allenai/c4", client)
    assert data["task_categories"] == ["text"]
    assert HFDatasetEnricher().badge(data)["label"] == "allenai/c4"


@respx.mock
@pytest.mark.parametrize("status", [401, 403, 404])
async def test_hf_fetch_unavailable(status):
    respx.get("https://huggingface.co/api/models/a/b").mock(return_value=httpx.Response(status))
    async with httpx.AsyncClient() as client:
        with pytest.raises(EnrichError):
            await HFModelEnricher().fetch("a/b", client)


@respx.mock
async def test_hf_fetch_rate_limited():
    respx.get("https://huggingface.co/api/models/a/b").mock(return_value=httpx.Response(429))
    async with httpx.AsyncClient() as client:
        with pytest.raises(EnrichError):
            await HFModelEnricher().fetch("a/b", client)


@respx.mock
async def test_hf_sends_token(monkeypatch):
    monkeypatch.setattr("app.enrichers.huggingface.settings.hf_token", "hf_xxx")
    route = respx.get("https://huggingface.co/api/models/a/b").mock(
        return_value=httpx.Response(200, json={})
    )
    async with httpx.AsyncClient() as client:
        await HFModelEnricher().fetch("a/b", client)
    assert route.calls.last.request.headers["Authorization"] == "Bearer hf_xxx"


def test_hf_entity_urls():
    assert HFModelEnricher().entity_url("a/b") == "https://huggingface.co/a/b"
    assert HFDatasetEnricher().entity_url("a/b") == "https://huggingface.co/datasets/a/b"


# --- PyPI ---


def test_pypi_normalize():
    assert _normalize("Typing_Extensions") == "typing-extensions"
    assert _normalize("a.b_c") == "a-b-c"


@respx.mock
async def test_pypi_fetch():
    respx.get("https://pypi.org/pypi/requests/json").mock(
        return_value=httpx.Response(
            200,
            json={
                "info": {
                    "name": "requests",
                    "version": "2.31.0",
                    "summary": "HTTP",
                    "requires_python": ">=3.8",
                    "license_expression": "Apache-2.0",
                    "project_urls": {"Homepage": "https://requests.readthedocs.io"},
                },
                "urls": [{"upload_time_iso_8601": "2023-05-22T00:00:00Z"}],
            },
        )
    )
    async with httpx.AsyncClient() as client:
        data = await PyPIEnricher().fetch("requests", client)
    assert data["version"] == "2.31.0"
    assert data["license"] == "Apache-2.0"
    assert data["released_at"] == "2023-05-22T00:00:00Z"
    assert PyPIEnricher().badge(data)["requires_python"] == ">=3.8"


@respx.mock
async def test_pypi_fetch_long_license_dropped():
    respx.get("https://pypi.org/pypi/x/json").mock(
        return_value=httpx.Response(200, json={"info": {"license": "L" * 200}, "urls": []})
    )
    async with httpx.AsyncClient() as client:
        data = await PyPIEnricher().fetch("x", client)
    assert data["license"] is None
    assert data["released_at"] is None


@respx.mock
async def test_pypi_fetch_not_found():
    respx.get("https://pypi.org/pypi/nope/json").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        with pytest.raises(EnrichError):
            await PyPIEnricher().fetch("nope", client)


def test_pypi_entity_url():
    assert PyPIEnricher().entity_url("requests") == "https://pypi.org/project/requests/"


# --- npm ---


@respx.mock
async def test_npm_fetch_with_downloads():
    respx.get("https://registry.npmjs.org/react/latest").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "react",
                "version": "18.2.0",
                "description": "UI",
                "license": "MIT",
                "homepage": "https://react.dev",
            },
        )
    )
    respx.get("https://api.npmjs.org/downloads/point/last-week/react").mock(
        return_value=httpx.Response(200, json={"downloads": 20_000_000})
    )
    async with httpx.AsyncClient() as client:
        data = await NpmEnricher().fetch("react", client)
    assert data["version"] == "18.2.0"
    assert data["downloads_last_week"] == 20_000_000
    assert NpmEnricher().badge(data)["label"] == "react"


@respx.mock
async def test_npm_fetch_license_dict_and_downloads_fail():
    respx.get("https://registry.npmjs.org/x/latest").mock(
        return_value=httpx.Response(200, json={"name": "x", "license": {"type": "ISC"}})
    )
    respx.get("https://api.npmjs.org/downloads/point/last-week/x").mock(
        side_effect=httpx.ConnectError("boom")
    )
    async with httpx.AsyncClient() as client:
        data = await NpmEnricher().fetch("x", client)
    assert data["license"] == "ISC"
    assert "downloads_last_week" not in data


@respx.mock
async def test_npm_fetch_downloads_non_200():
    respx.get("https://registry.npmjs.org/x/latest").mock(
        return_value=httpx.Response(200, json={"name": "x"})
    )
    respx.get("https://api.npmjs.org/downloads/point/last-week/x").mock(
        return_value=httpx.Response(404)
    )
    async with httpx.AsyncClient() as client:
        data = await NpmEnricher().fetch("x", client)
    assert "downloads_last_week" not in data


@respx.mock
async def test_npm_fetch_not_found():
    respx.get("https://registry.npmjs.org/nope/latest").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        with pytest.raises(EnrichError):
            await NpmEnricher().fetch("nope", client)


@respx.mock
async def test_npm_scoped_package_quoting():
    route = respx.get("https://registry.npmjs.org/@babel/core/latest").mock(
        return_value=httpx.Response(200, json={"name": "@babel/core"})
    )
    respx.get("https://api.npmjs.org/downloads/point/last-week/@babel/core").mock(
        return_value=httpx.Response(200, json={"downloads": 1})
    )
    async with httpx.AsyncClient() as client:
        await NpmEnricher().fetch("@babel/core", client)
    assert route.called


def test_npm_entity_url():
    assert NpmEnricher().entity_url("react") == "https://www.npmjs.com/package/react"


# --- YouTube ---


@respx.mock
async def test_youtube_fetch():
    respx.get("https://www.youtube.com/oembed").mock(
        return_value=httpx.Response(
            200,
            json={
                "title": "Never Gonna Give You Up",
                "author_name": "Rick Astley",
                "author_url": "https://youtube.com/@rick",
                "thumbnail_url": "https://i.ytimg.com/x.jpg",
            },
        )
    )
    async with httpx.AsyncClient() as client:
        data = await YouTubeEnricher().fetch("dQw4w9WgXcQ", client)
    assert data["channel"] == "Rick Astley"
    assert YouTubeEnricher().badge(data)["label"] == "Never Gonna Give You Up"


@respx.mock
@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_youtube_fetch_unavailable(status):
    respx.get("https://www.youtube.com/oembed").mock(return_value=httpx.Response(status))
    async with httpx.AsyncClient() as client:
        with pytest.raises(EnrichError):
            await YouTubeEnricher().fetch("dQw4w9WgXcQ", client)


def test_youtube_entity_url():
    assert YouTubeEnricher().entity_url("abc") == "https://www.youtube.com/watch?v=abc"


# --- registry dispatch ---


def test_match_url_dataset_before_model():
    enricher, key = match_url("https://huggingface.co/datasets/allenai/c4")
    assert enricher.kind == "hf_dataset"
    assert key == "allenai/c4"


def test_match_url_no_host_match():
    assert match_url("https://example.com/foo/bar") is None
