import pytest
from sqlalchemy import func, select

from app import llm, translation
from app.models import SummaryTranslation

HEBREW = "ההצבעה בכנסת נדחתה בשבוע, והקואליציה מחפשת רוב חדש."


class _Recorder:
    """Stands in for llm.translate, remembering what it was asked to do."""

    def __init__(self, result="TRANSLATED"):
        self.result = result
        self.calls = []

    async def __call__(self, text, language, *, config=None, usage=None):
        self.calls.append({"text": text, "language": language, "config": config})
        return self.result


async def _article(data, *, summary="The vote was delayed by a week.", **kwargs):
    feed = await data.feed()
    return await data.article(feed, summary=summary, **kwargs)


# --- language table ---


def test_language_lookup_is_case_and_space_insensitive():
    assert translation.language_for(" HE ").name == "Hebrew"
    assert translation.language_for("nope") is None


def test_every_language_name_matches_the_detector_vocabulary():
    """The source language is compared by name against what lingua reports, so
    a typo here would translate Hebrew summaries into Hebrew forever."""
    from lingua import Language as LinguaLanguage

    known = {language.name.title() for language in LinguaLanguage.all()}
    assert {language.name for language in translation.LANGUAGES} <= known


# --- the model call ---


async def test_translate_prompt_names_the_target_language(monkeypatch):
    captured = {}

    async def fake_complete(messages, max_tokens, **kwargs):
        captured["system"] = messages[0]["content"]
        captured["user"] = messages[1]["content"]
        return "  la traduction  "

    monkeypatch.setattr(llm, "_complete", fake_complete)
    out = await llm.translate("the summary", "French")

    assert out == "la traduction"  # surrounding whitespace never reaches the reader
    assert "into French" in captured["system"]
    assert "markdown" in captured["system"]
    assert captured["user"] == "the summary"


async def test_create_refuses_a_cut_off_answer(monkeypatch):
    """The other half of the production bug: the reply was also truncated, so
    the stored "translation" ended mid-sentence."""

    class _Client:
        class chat:  # noqa: N801 - mirrors the SDK's shape
            class completions:  # noqa: N801
                @staticmethod
                async def create(**kwargs):
                    message = type("M", (), {"content": "half a transl"})()
                    choice = type("C", (), {"message": message, "finish_reason": "length"})()
                    return type("R", (), {"usage": None, "choices": [choice]})()

    with pytest.raises(llm.EmptyResponseError):
        await llm._create(_Client(), "m", [], 10, None, None, True)

    # Without the flag the same reply is returned — summaries tolerate it.
    assert await llm._create(_Client(), "m", [], 10, None) == "half a transl"


async def test_extra_params_ride_along_as_extra_body(monkeypatch):
    captured = {}

    class _Client:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                async def create(**kwargs):
                    captured.update(kwargs)
                    message = type("M", (), {"content": "ok"})()
                    choice = type("C", (), {"message": message, "finish_reason": "stop"})()
                    return type("R", (), {"usage": None, "choices": [choice]})()

    monkeypatch.setattr(llm, "user_client", lambda config: _AsyncCtx(_Client()))
    config = llm.LLMConfig(
        provider="custom",
        api_key="k",
        base_url="https://openrouter.ai/api/v1",
        model="m",
        extra_params={"reasoning": {"enabled": False}},
    )
    await llm._complete([{"role": "user", "content": "x"}], 10, config=config)
    assert captured["extra_body"] == {"reasoning": {"enabled": False}}


class _AsyncCtx:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *args):
        return False


async def test_translate_rejects_thinking_that_leaked_into_the_answer(monkeypatch):
    """The bug from production: a reasoning model streamed its deliberation as
    plain prose into the content, so the reader saw "We need to translate the
    given English summary into Hebrew, preserving markdown..." instead of a
    translation — and it got cached under the summary."""
    source = "The cabinet approved the package." * 3

    async def fake_complete(messages, max_tokens, **kwargs):
        return "We need to translate the given English summary into Hebrew, " * 40

    monkeypatch.setattr(llm, "_complete", fake_complete)
    with pytest.raises(llm.EmptyResponseError):
        await llm.translate(source, "Hebrew")


async def test_translate_accepts_a_translation_that_grew_a_little(monkeypatch):
    """The bloat guard must not fire on languages that legitimately expand."""
    source = "The cabinet approved a package to rebuild the north."

    async def fake_complete(messages, max_tokens, **kwargs):
        return "Das Kabinett hat ein Paket zum Wiederaufbau des Nordens genehmigt." * 2

    monkeypatch.setattr(llm, "_complete", fake_complete)
    assert await llm.translate(source, "German")


async def test_translate_asks_for_a_complete_answer(monkeypatch):
    """A truncated translation is a half-translated document, so the call is
    made with require_complete and the cut-off answer never reaches the cache."""
    captured = {}

    async def fake_complete(messages, max_tokens, **kwargs):
        captured.update(kwargs, max_tokens=max_tokens)
        return "fine"

    monkeypatch.setattr(llm, "_complete", fake_complete)
    await llm.translate("source", "Hebrew")
    assert captured["require_complete"] is True
    assert captured["max_tokens"] >= 3000


async def test_translate_drops_an_unclosed_think_block(monkeypatch):
    async def fake_complete(messages, max_tokens, **kwargs):
        return "<think>Hmm, the user wants Hebrew, let me consider the table"

    monkeypatch.setattr(llm, "_complete", fake_complete)
    with pytest.raises(llm.EmptyResponseError):
        await llm.translate("The vote was delayed.", "Hebrew")


def test_translation_config_turns_reasoning_off_on_openrouter(monkeypatch):
    """Free reasoning models are the point of this setting, and their thinking
    does not reliably stay inside a <think> block."""
    monkeypatch.setattr(llm.settings, "translation_model", "free/model")
    monkeypatch.setattr(llm.settings, "translation_api_key", "sk-or")
    monkeypatch.setattr(llm.settings, "translation_base_url", "https://openrouter.ai/api/v1")
    assert llm.translation_config().extra_params == {"reasoning": {"enabled": False}}


def test_translation_config_sends_no_openrouter_params_elsewhere(monkeypatch):
    """`reasoning` is an OpenRouter parameter; another endpoint could 400 on it."""
    monkeypatch.setattr(llm.settings, "translation_model", "local/model")
    monkeypatch.setattr(llm.settings, "translation_api_key", "sk-local")
    monkeypatch.setattr(llm.settings, "translation_base_url", "http://vllm.local/v1")
    assert llm.translation_config().extra_params == {}


async def test_translate_rejects_an_empty_answer(monkeypatch):
    async def fake_complete(messages, max_tokens, **kwargs):
        return "   "

    monkeypatch.setattr(llm, "_complete", fake_complete)
    with pytest.raises(llm.EmptyResponseError):
        await llm.translate("the summary", "French")


async def test_translation_runs_on_its_own_endpoint(monkeypatch):
    """The shared client is bound to the server-wide endpoint, so a separately
    configured translation model must get a client of its own."""
    captured = {}

    class FakeAsyncOpenAI:
        def __init__(self, api_key, base_url, timeout):
            captured.update(api_key=api_key, base_url=base_url)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        class chat:  # noqa: N801 - mirrors the SDK's shape
            class completions:  # noqa: N801
                @staticmethod
                async def create(**kwargs):
                    captured["model"] = kwargs["model"]

                    class _Response:
                        usage = None
                        choices = [
                            type(
                                "C",
                                (),
                                {
                                    "message": type("M", (), {"content": "ok"})(),
                                    "finish_reason": "stop",
                                },
                            )()
                        ]

                    return _Response()

    monkeypatch.setattr(llm, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setattr(
        llm, "get_client", lambda: pytest.fail("used the server-wide client for translation")
    )
    config = llm.LLMConfig(
        provider="custom",
        api_key="sk-free",
        base_url="https://openrouter.ai/api/v1",
        model="nvidia/nemotron:free",
    )

    assert await llm.translate("summary", "Hebrew", config=config) == "ok"
    assert captured["api_key"] == "sk-free"
    assert captured["base_url"] == "https://openrouter.ai/api/v1"
    assert captured["model"] == "nvidia/nemotron:free"


# --- caching ---


async def test_translates_and_caches(data, monkeypatch):
    art = await _article(data, summary_language="English")
    translate = _Recorder("הצבעה נדחתה")
    monkeypatch.setattr(llm, "translate", translate)

    result = await translation.translate_summary(
        data.session, art, "he", config=llm.LLMConfig("custom", "k", None, "free-model")
    )

    assert result.text == "הצבעה נדחתה"
    assert result.translated is True
    assert result.cached is False
    assert result.model == "free-model"
    assert translate.calls[0]["language"] == "Hebrew"
    stored = (await data.session.execute(select(SummaryTranslation))).scalars().all()
    assert len(stored) == 1
    assert stored[0].language == "he"
    assert stored[0].source_hash == translation.source_hash(art.summary)


async def test_second_request_serves_the_cache(data, monkeypatch):
    art = await _article(data, summary_language="English")
    translate = _Recorder()
    monkeypatch.setattr(llm, "translate", translate)

    await translation.translate_summary(data.session, art, "he")
    second = await translation.translate_summary(data.session, art, "he")

    assert second.cached is True
    assert second.text == "TRANSLATED"
    assert len(translate.calls) == 1  # the model was called exactly once


async def test_cache_is_shared_between_users(client, users, data, monkeypatch):
    """The point of a global cache: the second reader wanting Hebrew pays
    nothing, even though a different user populated it."""
    feed = await data.feed()
    art = await data.article(feed, summary="The vote was delayed.", summary_language="English")
    first, second = await users.create(), await users.create(email="b@example.com")
    await data.subscribe(first, feed)
    await data.subscribe(second, feed)
    translate = _Recorder()
    monkeypatch.setattr(llm, "translate", translate)
    monkeypatch.setattr(llm, "translation_config", lambda: llm.LLMConfig("custom", "k", None, "m"))

    for user in (first, second):
        resp = await client.post(
            f"/api/articles/{art.id}/translate",
            json={"language": "he"},
            headers=users.auth(user),
        )
        assert resp.status_code == 200

    assert len(translate.calls) == 1
    assert resp.json()["cached"] is True


async def test_regenerated_summary_misses_the_cache(data, monkeypatch):
    """A new summary hashes differently, so the stale translation is never
    served again — no invalidation step to forget."""
    art = await _article(data, summary_language="English")
    translate = _Recorder("first")
    monkeypatch.setattr(llm, "translate", translate)
    await translation.translate_summary(data.session, art, "he")

    art.summary = "A completely different summary after regeneration."
    await data.session.commit()
    translate.result = "second"
    result = await translation.translate_summary(data.session, art, "he")

    assert result.text == "second"
    assert len(translate.calls) == 2
    rows = (await data.session.execute(select(func.count(SummaryTranslation.id)))).scalar_one()
    assert rows == 2


async def test_languages_are_cached_separately(data, monkeypatch):
    art = await _article(data, summary_language="English")
    translate = _Recorder()
    monkeypatch.setattr(llm, "translate", translate)

    await translation.translate_summary(data.session, art, "he")
    await translation.translate_summary(data.session, art, "fr")

    assert [call["language"] for call in translate.calls] == ["Hebrew", "French"]


async def test_concurrent_insert_conflict_is_conceded(data, monkeypatch):
    """Two readers asking for the same language at once: the loser's insert is
    dropped rather than raising, since both answers are equally good."""
    art = await _article(data, summary_language="English")
    monkeypatch.setattr(llm, "translate", _Recorder("mine"))
    digest = translation.source_hash(art.summary)
    data.session.add(
        SummaryTranslation(
            article_id=art.id, language="he", source_hash=digest, text="theirs", model="other"
        )
    )
    await data.session.commit()

    await translation._store(data.session, art.id, "he", digest, "mine", "m")

    rows = (await data.session.execute(select(SummaryTranslation))).scalars().all()
    assert [row.text for row in rows] == ["theirs"]


# --- no-op and error paths ---


async def test_same_language_skips_the_model(data, monkeypatch):
    art = await _article(data, summary=HEBREW, summary_language="Hebrew")
    translate = _Recorder()
    monkeypatch.setattr(llm, "translate", translate)

    result = await translation.translate_summary(data.session, art, "he")

    assert result.translated is False
    assert result.text == HEBREW
    assert translate.calls == []


async def test_missing_summary_language_is_detected_and_stored(data, monkeypatch):
    """Articles summarized before the column existed: detect once, write back."""
    art = await _article(data, summary=HEBREW, summary_language=None)
    monkeypatch.setattr(llm, "translate", _Recorder())

    result = await translation.translate_summary(data.session, art, "he")

    assert result.translated is False  # detected as Hebrew, so nothing to do
    assert art.summary_language == "Hebrew"


async def test_unknown_language_rejected(data):
    art = await _article(data)
    with pytest.raises(translation.UnknownLanguage):
        await translation.translate_summary(data.session, art, "xx")


async def test_article_without_a_summary_rejected(data):
    art = await _article(data, summary="")
    with pytest.raises(translation.NothingToTranslate):
        await translation.translate_summary(data.session, art, "he")


# --- API ---


async def _subscribed(users, data, **kwargs):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    art = await data.article(feed, summary="The vote was delayed.", **kwargs)
    return user, art


async def test_translate_endpoint(client, users, data, monkeypatch):
    user, art = await _subscribed(users, data, summary_language="English")
    monkeypatch.setattr(llm, "translate", _Recorder("הצבעה נדחתה"))
    monkeypatch.setattr(
        llm, "translation_config", lambda: llm.LLMConfig("custom", "k", None, "free-model")
    )

    resp = await client.post(
        f"/api/articles/{art.id}/translate", json={"language": "he"}, headers=users.auth(user)
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "language": "he",
        "text": "הצבעה נדחתה",
        "model": "free-model",
        "cached": False,
        "translated": True,
        "source_language": "English",
    }


async def test_translate_failure_leaves_the_summary_intact(client, users, data, monkeypatch):
    """The acceptance criterion that matters most: a failed translation must
    not cost the reader the summary they already had."""
    user, art = await _subscribed(users, data, summary_language="English")

    async def boom(*args, **kwargs):
        raise RuntimeError("provider is rate limiting free models")

    monkeypatch.setattr(llm, "translate", boom)
    monkeypatch.setattr(llm, "translation_config", lambda: llm.LLMConfig("custom", "k", None, "m"))

    resp = await client.post(
        f"/api/articles/{art.id}/translate", json={"language": "he"}, headers=users.auth(user)
    )

    assert resp.status_code == 502
    await data.session.refresh(art)
    assert art.summary == "The vote was delayed."
    rows = (await data.session.execute(select(func.count(SummaryTranslation.id)))).scalar_one()
    assert rows == 0


async def test_translate_unconfigured_is_503(client, users, data, monkeypatch):
    user, art = await _subscribed(users, data)
    monkeypatch.setattr(llm, "translation_config", lambda: None)
    resp = await client.post(
        f"/api/articles/{art.id}/translate", json={"language": "he"}, headers=users.auth(user)
    )
    assert resp.status_code == 503


async def test_translate_unknown_language_is_422(client, users, data, monkeypatch):
    user, art = await _subscribed(users, data)
    monkeypatch.setattr(llm, "translation_config", lambda: llm.LLMConfig("custom", "k", None, "m"))
    resp = await client.post(
        f"/api/articles/{art.id}/translate", json={"language": "xx"}, headers=users.auth(user)
    )
    assert resp.status_code == 422


async def test_translate_without_a_summary_is_422(client, users, data, monkeypatch):
    user, art = await _subscribed(users, data)
    art.summary = ""
    await data.session.commit()
    monkeypatch.setattr(llm, "translation_config", lambda: llm.LLMConfig("custom", "k", None, "m"))
    resp = await client.post(
        f"/api/articles/{art.id}/translate", json={"language": "he"}, headers=users.auth(user)
    )
    assert resp.status_code == 422


async def test_translate_requires_access_to_the_article(client, users, data, monkeypatch):
    """An unsubscribed reader can't spend a call on an article they can't see."""
    _, art = await _subscribed(users, data)
    outsider = await users.create(email="outsider@example.com")
    monkeypatch.setattr(llm, "translation_config", lambda: llm.LLMConfig("custom", "k", None, "m"))
    resp = await client.post(
        f"/api/articles/{art.id}/translate", json={"language": "he"}, headers=users.auth(outsider)
    )
    assert resp.status_code == 404


async def test_languages_endpoint(client, users):
    user = await users.create()
    resp = await client.get("/api/translation/languages", headers=users.auth(user))
    assert resp.status_code == 200
    body = resp.json()
    hebrew = next(item for item in body if item["code"] == "he")
    assert hebrew == {"code": "he", "name": "Hebrew", "native_name": "עברית", "rtl": True}
    assert next(item for item in body if item["code"] == "fr")["rtl"] is False


async def test_ai_status_reports_translation(client, users, monkeypatch):
    monkeypatch.setattr(llm, "translation_config", lambda: llm.LLMConfig("custom", "k", None, "m"))
    user = await users.create()
    resp = await client.get("/api/ai/status", headers=users.auth(user))
    assert resp.json()["translation"] is True

    monkeypatch.setattr(llm, "translation_config", lambda: None)
    resp = await client.get("/api/ai/status", headers=users.auth(user))
    assert resp.json()["translation"] is False


# --- the saved default language ---


async def test_saving_and_clearing_the_default_language(client, users):
    user = await users.create()
    resp = await client.patch(
        "/api/users/me", json={"translation_language": "he"}, headers=users.auth(user)
    )
    assert resp.status_code == 200
    assert resp.json()["translation_language"] == "he"

    resp = await client.patch(
        "/api/users/me", json={"translation_language": None}, headers=users.auth(user)
    )
    assert resp.json()["translation_language"] is None


async def test_default_language_is_stored_canonically(client, users):
    """language_for() accepts " HE ", but both clients match the saved value
    against exact codes — so what lands in the column must be "he"."""
    user = await users.create()
    resp = await client.patch(
        "/api/users/me", json={"translation_language": " HE "}, headers=users.auth(user)
    )
    assert resp.status_code == 200
    assert resp.json()["translation_language"] == "he"


async def test_default_language_must_be_one_we_offer(client, users):
    user = await users.create()
    resp = await client.patch(
        "/api/users/me", json={"translation_language": "xx"}, headers=users.auth(user)
    )
    assert resp.status_code == 422


async def test_other_preferences_do_not_clear_the_language(client, users):
    """Presence-based PATCH: an omitted field is unchanged, not reset."""
    user = await users.create()
    await client.patch(
        "/api/users/me", json={"translation_language": "fr"}, headers=users.auth(user)
    )
    resp = await client.patch(
        "/api/users/me", json={"default_view": "list"}, headers=users.auth(user)
    )
    assert resp.json()["translation_language"] == "fr"


# --- config resolution ---


def test_translation_config_prefers_its_own_endpoint(monkeypatch):
    monkeypatch.setattr(llm.settings, "translation_model", "free/model")
    monkeypatch.setattr(llm.settings, "translation_api_key", "sk-translate")
    monkeypatch.setattr(llm.settings, "translation_base_url", "https://openrouter.ai/api/v1")
    config = llm.translation_config()
    assert config.model == "free/model"
    assert config.api_key == "sk-translate"
    assert config.base_url == "https://openrouter.ai/api/v1"
    assert config.user_owned is False  # never billed to a reader's own key


def test_translation_config_borrows_the_main_key_on_the_same_endpoint(monkeypatch):
    monkeypatch.setattr(llm.settings, "translation_model", "free/model")
    monkeypatch.setattr(llm.settings, "translation_api_key", "")
    monkeypatch.setattr(llm.settings, "translation_base_url", "https://openrouter.ai/api/v1/")
    monkeypatch.setattr(llm.settings, "openai_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(llm.settings, "openai_api_key", "sk-main")
    assert llm.translation_config().api_key == "sk-main"


def test_translation_config_never_sends_the_main_key_to_another_provider(monkeypatch):
    """A key issued for api.openai.com must not be posted to OpenRouter just
    because TRANSLATION_MODEL was set without a key of its own."""
    monkeypatch.setattr(llm.settings, "translation_model", "free/model")
    monkeypatch.setattr(llm.settings, "translation_api_key", "")
    monkeypatch.setattr(llm.settings, "translation_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(llm.settings, "openai_base_url", "")  # the OpenAI default
    monkeypatch.setattr(llm.settings, "openai_api_key", "sk-main")
    assert llm.translation_config() is None


def test_translation_config_borrows_the_main_key_on_the_openai_default(monkeypatch):
    """Both unset means both talk to api.openai.com — still the same endpoint."""
    monkeypatch.setattr(llm.settings, "translation_model", "gpt-4o-mini")
    monkeypatch.setattr(llm.settings, "translation_api_key", "")
    monkeypatch.setattr(llm.settings, "translation_base_url", "")
    monkeypatch.setattr(llm.settings, "openai_base_url", "")
    monkeypatch.setattr(llm.settings, "openai_api_key", "sk-main")
    assert llm.translation_config().api_key == "sk-main"


def test_translation_config_none_without_any_key(monkeypatch):
    monkeypatch.setattr(llm.settings, "translation_model", "free/model")
    monkeypatch.setattr(llm.settings, "translation_api_key", "")
    monkeypatch.setattr(llm.settings, "translation_base_url", "")
    monkeypatch.setattr(llm.settings, "openai_base_url", "")
    monkeypatch.setattr(llm.settings, "openai_api_key", "")
    assert llm.translation_config() is None


def test_translation_falls_back_to_the_system_model(monkeypatch):
    monkeypatch.setattr(llm.settings, "translation_model", "")
    monkeypatch.setattr(llm.settings, "openai_api_key", "sk-main")
    monkeypatch.setattr(llm.settings, "openai_model", "gpt-main")
    assert llm.translation_config().model == "gpt-main"


def test_translation_unconfigured_when_nothing_is_set(monkeypatch):
    monkeypatch.setattr(llm.settings, "translation_model", "")
    monkeypatch.setattr(llm.settings, "openai_api_key", "")
    assert llm.translation_config() is None
