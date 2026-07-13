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


def _fake_client_with_usage(content, usage):
    async def create(**kwargs):
        msg = types.SimpleNamespace(content=content)
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(choices=[choice], usage=usage)

    return types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )


async def test_complete_accumulates_usage(monkeypatch):
    usage_payload = types.SimpleNamespace(prompt_tokens=9, completion_tokens=4)
    monkeypatch.setattr(llm, "get_client", lambda: _fake_client_with_usage("out", usage_payload))
    usage = llm.TokenUsage()
    await llm._complete([{"role": "user", "content": "x"}], max_tokens=10, usage=usage)
    assert usage.prompt_tokens == 9
    assert usage.completion_tokens == 4


async def test_complete_system_config_uses_shared_client(monkeypatch):
    monkeypatch.setattr(llm, "get_client", lambda: _fake_client("shared"))
    config = llm.LLMConfig(provider="system", api_key="k", base_url=None, model="m")
    out = await llm._complete([{"role": "user", "content": "x"}], max_tokens=10, config=config)
    assert out == "shared"


async def test_complete_user_config_uses_scoped_client(monkeypatch):
    captured = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            captured["closed"] = False
            self.chat = _fake_client("scoped").chat

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            captured["closed"] = True
            return False

    monkeypatch.setattr(llm, "AsyncOpenAI", FakeAsyncOpenAI)
    config = llm.LLMConfig(provider="custom", api_key="sk-own-12345678",
                           base_url="http://ollama.local/v1", model="llama",
                           user_owned=True)
    out = await llm._complete([{"role": "user", "content": "x"}], max_tokens=10, config=config)
    assert out == "scoped"
    assert captured["api_key"] == "sk-own-12345678"
    assert captured["base_url"] == "http://ollama.local/v1"
    assert captured["closed"] is True  # the per-call client never outlives the call


async def test_share_message_polishes_draft(monkeypatch):
    captured = {}

    async def fake_complete(messages, max_tokens, **kwargs):
        captured["user"] = messages[1]["content"]
        return "note"

    monkeypatch.setattr(llm, "_complete", fake_complete)
    out = await llm.share_message(
        "T", "the summary", draft="my draft", tone="casual", target_name="#general"
    )
    assert out == "note"
    assert "Polish this draft" in captured["user"]
    assert "my draft" in captured["user"]
    assert "Tone: casual" in captured["user"]
    assert "#general" in captured["user"]
    assert "the summary" in captured["user"]


async def test_share_message_from_scratch(monkeypatch):
    captured = {}

    async def fake_complete(messages, max_tokens, **kwargs):
        captured["user"] = messages[1]["content"]
        return "note"

    monkeypatch.setattr(llm, "_complete", fake_complete)
    await llm.share_message("T", "")
    assert "Write the message from scratch." in captured["user"]
    assert "Article summary" not in captured["user"]


async def test_summarize_screenshot_sends_data_url(monkeypatch):
    captured = {}

    async def create(**kwargs):
        captured.update(kwargs)
        msg = types.SimpleNamespace(
            content="ONELINER: gist\nPARAGRAPH: para.\nFULL:\nfrom the image"
        )
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=msg)], usage=None
        )

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    monkeypatch.setattr(llm, "get_client", lambda: client)

    short, medium, full = await llm.summarize_screenshot("Title", b"\xff\xd8jpeg")
    assert (short, medium, full) == ("gist", "para.", "from the image")
    text_part, image_part = captured["messages"][1]["content"]
    assert text_part["type"] == "text"
    assert "Title" in text_part["text"]
    assert image_part["type"] == "image_url"
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_dislike_topics_parses_and_dedupes(monkeypatch):
    raw = ("Sure, here are topics:\n"
           "TOPIC: cryptocurrency price movements.\n"
           "TOPIC:   Cryptocurrency  Price Movements\n"
           "TOPIC: celebrity gossip\n"
           "TOPIC: US college sports\n"
           "not a topic line\n")

    async def fake_complete(messages, max_tokens, **kwargs):
        assert "not interested" in messages[0]["content"]
        assert "Article title: T" in messages[1]["content"]
        return raw

    monkeypatch.setattr(llm, "_complete", fake_complete)
    topics = await llm.dislike_topics("T", "summary")
    # Case-insensitive dedupe, trailing dot stripped, capped at 3.
    assert topics == ["cryptocurrency price movements", "celebrity gossip", "US college sports"]


async def test_dislike_topics_garbage_output(monkeypatch):
    async def fake_complete(messages, max_tokens, **kwargs):
        return "I could not determine topics for this article."

    monkeypatch.setattr(llm, "_complete", fake_complete)
    assert await llm.dislike_topics("T", "s") == []


async def test_dislike_topics_caps_phrase_length(monkeypatch):
    async def fake_complete(messages, max_tokens, **kwargs):
        return "TOPIC: " + "very " * 40 + "long topic"

    monkeypatch.setattr(llm, "_complete", fake_complete)
    [topic] = await llm.dislike_topics("T", "s")
    assert len(topic) <= 80


def test_parse_synthesis_full_structure():
    raw = ("OVERVIEW:\nBig thing happened [1]. Sources broadly agree [2].\n\n"
           "TIMELINE:\n- May 1 — it started [1]\n- May 3 — it escalated [2]\n\n"
           "PERSPECTIVES:\n- [2] frames it as a fluke\n- [3] sees a trend")
    parsed = llm._parse_synthesis(raw)
    assert parsed.overview == "Big thing happened [1]. Sources broadly agree [2]."
    assert "- May 1 — it started [1]" in parsed.timeline_raw
    assert "PERSPECTIVES" not in parsed.timeline_raw
    assert parsed.perspectives.startswith("- [2] frames")


def test_parse_synthesis_overview_only():
    parsed = llm._parse_synthesis("OVERVIEW:\nJust the gist [1].")
    assert parsed.overview == "Just the gist [1]."
    assert parsed.timeline_raw is None
    assert parsed.perspectives is None


def test_parse_synthesis_missing_labels_falls_back():
    parsed = llm._parse_synthesis("The model ignored the format entirely.")
    assert parsed.overview == "The model ignored the format entirely."
    assert parsed.timeline_raw is None


def test_parse_timeline_dash_variants_and_garbage():
    raw = ("- May 1 — em dash [1]\n"
           "- May 2 – en dash [2]\n"
           "- May 3 -- double hyphen [3]\n"
           "not a timeline line\n"
           "- no separator here\n")
    items = llm.parse_timeline(raw)
    assert items == [
        {"when": "May 1", "what": "em dash [1]"},
        {"when": "May 2", "what": "en dash [2]"},
        {"when": "May 3", "what": "double hyphen [3]"},
    ]
    assert llm.parse_timeline("free text only") is None
    assert llm.parse_timeline(None) is None
    assert llm.parse_timeline("") is None


async def test_synthesize_related_numbers_sources(monkeypatch):
    captured = {}

    async def fake_complete(messages, max_tokens, **kwargs):
        captured["messages"] = messages
        return "OVERVIEW:\nAll good [2]."

    monkeypatch.setattr(llm, "_complete", fake_complete)
    result = await llm.synthesize_related([("Main", "sum A"), ("Other", "sum B")])
    assert result.overview == "All good [2]."
    user_msg = captured["messages"][1]["content"]
    assert "[1] Main\nsum A" in user_msg
    assert "[2] Other\nsum B" in user_msg
    assert "Source [1] is the article the reader is on" in user_msg
    assert "cite them inline" in captured["messages"][0]["content"]
