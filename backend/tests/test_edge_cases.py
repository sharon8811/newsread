"""Small branch-coverage edge cases across modules."""

import httpx
import pytest
import respx
from sqlalchemy import text

from app import db as app_db
from app.db import init_db
from app.enrichers.arxiv import ArxivEnricher, _parse_id
from app.enrichers.base import CleanUrl
from app.enrichers.github import GitHubEnricher
from app.enrichers.npm import NpmEnricher
from app.enrichers.urls import clean_url, extract_links
from app.fetcher import parse_xml_feed


def _clean(path, host="github.com", query=None):
    return CleanUrl(raw="x", host=host, path=path, query=query or {})


# --- github matcher branches ---

def test_github_reserved_owner():
    assert GitHubEnricher().matches(_clean("/issues/somerepo")) is None


def test_github_empty_repo_after_git_strip():
    assert GitHubEnricher().matches(_clean("/owner/.git")) is None


# --- npm scoped package without name ---

def test_npm_scoped_missing_name():
    assert NpmEnricher().matches(_clean("/package/@babel", host="npmjs.com")) is None


# --- arxiv ---

def test_arxiv_parse_id_prefix_but_bad_id():
    assert _parse_id("/abs/not-a-real-id") is None


@respx.mock
async def test_arxiv_fetch_respects_rate_limit(monkeypatch):
    import time as _time

    # Force the min-interval sleep branch: last request was "just now".
    monkeypatch.setattr("app.enrichers.arxiv._MIN_INTERVAL", 3.0)
    monkeypatch.setattr("app.enrichers.arxiv._last_request", _time.monotonic())

    slept = {}

    async def fake_sleep(seconds):
        slept["seconds"] = seconds

    monkeypatch.setattr("app.enrichers.arxiv.asyncio.sleep", fake_sleep)
    xml = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
           '<title>A Paper</title><summary>s</summary></entry></feed>')
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text=xml)
    )
    async with httpx.AsyncClient() as client:
        data = await ArxivEnricher().fetch("1706.03762", client)
    assert data["title"] == "A Paper"
    assert slept["seconds"] > 0  # the rate-limit sleep ran


# --- clean_url / extract_links error branches ---

def test_clean_url_malformed_raises_returns_none():
    # urlsplit raises ValueError on an invalid IPv6 literal.
    assert clean_url("http://[") is None


def test_extract_links_parser_error(monkeypatch):
    def boom(self, data):
        raise RuntimeError("parser blew up")

    monkeypatch.setattr("app.enrichers.urls._AnchorParser.feed", boom)
    assert extract_links("<a href='/x'>y</a>") == []


# --- fetcher: entry with neither link nor guid ---

def test_parse_xml_feed_skips_entry_without_guid():
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel><title>F</title>
      <item><title>No link no guid</title></item>
    </channel></rss>"""
    feed = parse_xml_feed(xml)
    assert feed.articles == []


# --- db: pgvector extension unavailable ---

async def test_init_db_without_pgvector(monkeypatch):
    real_engine = app_db.engine

    class FakeEngine:
        """Ping + create_all succeed via the real engine; only CREATE EXTENSION
        (the first engine.begin) fails, exercising the vector-disabled branch."""

        def __init__(self):
            self._begins = 0

        def connect(self):
            return real_engine.connect()

        def begin(self):
            self._begins += 1
            if self._begins == 1:
                raise RuntimeError("extension not permitted")
            return real_engine.begin()

    monkeypatch.setattr(app_db, "engine", FakeEngine())
    try:
        await init_db()
        assert app_db.vector_enabled is False
    finally:
        app_db.vector_enabled = True  # restore for other tests
