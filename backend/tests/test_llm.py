import types

import pytest

from app import llm


def test_is_configured(monkeypatch):
    monkeypatch.setattr(llm.settings, "openai_api_key", "")
    monkeypatch.setattr(llm.settings, "openai_model", "")
    assert not llm.is_configured()
    monkeypatch.setattr(llm.settings, "openai_api_key", "k")
    monkeypatch.setattr(llm.settings, "openai_model", "m")
    assert llm.is_configured()


def test_clean_strips_think_tags():
    assert llm._clean("<think>reasoning</think>  answer ") == "answer"
    assert llm._clean("no tags") == "no tags"


def test_get_client_is_cached(monkeypatch):
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(llm.settings, "openai_api_key", "k")
    monkeypatch.setattr(llm.settings, "openai_base_url", "")
    c1 = llm.get_client()
    c2 = llm.get_client()
    assert c1 is c2


def test_parse_levels_full_structure():
    raw = (
        "ONELINER: A short gist here\n"
        "PARAGRAPH: First sentence.\nSecond sentence.\n"
        "FULL:\nCore takeaway.\n\n• point one\n• point two"
    )
    short, medium, full = llm._parse_levels(raw)
    assert short == "A short gist here"
    assert medium == "First sentence. Second sentence."
    assert "point one" in full


def test_parse_levels_fallback_to_raw_when_no_full():
    short, medium, full = llm._parse_levels("just some unstructured text")
    assert full == "just some unstructured text"
    assert short == ""


def _fake_client(content):
    async def create(**kwargs):
        msg = types.SimpleNamespace(content=content)
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(choices=[choice])

    return types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )


async def test_summarize(monkeypatch):
    raw = "ONELINER: gist\nPARAGRAPH: para text.\nFULL:\nfull body"
    monkeypatch.setattr(llm, "get_client", lambda: _fake_client(raw))
    short, medium, full = await llm.summarize("Title", "body text")
    assert short == "gist"
    assert medium == "para text."
    assert full == "full body"


async def test_complete_handles_none_content(monkeypatch):
    monkeypatch.setattr(llm, "get_client", lambda: _fake_client(None))
    out = await llm._complete([{"role": "user", "content": "x"}], max_tokens=10)
    assert out == ""
