"""Article image generation: prompt templating, config resolution, both wire
shapes, and the background store-and-log task."""

import base64
import types

import pytest
from sqlalchemy import select

from app import crypto, image_gen, llm
from app.models import Article, Feed, GeneratedImage, LLMUsage, UserAISettings


# --- prompt templating ---

def test_default_prompt_is_exact():
    assert image_gen.DEFAULT_IMAGE_PROMPT == (
        "{article_title} showcased in a gritty noir comic book splash page. "
        "High contrast chiaroscuro lighting, heavy ink lines, dramatic angle. "
        "Full bleed, edge-to-edge artwork, masterpiece."
    )


def test_render_prompt_replaces_tags():
    out = image_gen.render_prompt(
        "Draw {article_title} — context: {article_excerpt}",
        title="Big News", excerpt="something happened",
    )
    assert out == "Draw Big News — context: something happened"


def test_render_prompt_keeps_unknown_braces():
    out = image_gen.render_prompt("{article_title} in {style} braces", title="T")
    assert out == "T in {style} braces"


# --- config resolution ---

def test_system_config_from_env(monkeypatch):
    monkeypatch.setattr(image_gen.settings, "image_generation_api_key", "sk-img")
    monkeypatch.setattr(image_gen.settings, "image_generation_model", "gemini-image")
    monkeypatch.setattr(
        image_gen.settings, "image_generation_base_url", "https://openrouter.ai/api/v1"
    )
    config = image_gen.system_config()
    assert config.provider == "system"
    assert config.user_owned is False
    assert config.base_url == "https://openrouter.ai/api/v1"

    monkeypatch.setattr(image_gen.settings, "image_generation_api_key", "")
    assert image_gen.system_config() is None


async def test_resolve_config_prefers_user_image_block(session, users):
    user = await users.create()
    session.add(UserAISettings(
        user_id=user.id, provider="openai", model="gpt-5",
        api_key_enc=crypto.encrypt_token("sk-main-12345678"), key_hint="5678",
        image_provider="openai", image_model="gpt-image-1",
    ))
    await session.commit()
    config = await image_gen.resolve_config(session, user.id)
    assert config.user_owned is True
    assert config.model == "gpt-image-1"
    # No dedicated image key -> the main key serves (same provider, enforced at save).
    assert config.api_key == "sk-main-12345678"


async def test_resolve_config_uses_dedicated_image_key(session, users):
    user = await users.create()
    session.add(UserAISettings(
        user_id=user.id, provider="openai", model="gpt-5",
        api_key_enc=crypto.encrypt_token("sk-main-12345678"), key_hint="5678",
        image_provider="anthropic", image_model="img-model",
        image_api_key_enc=crypto.encrypt_token("sk-img-87654321"), image_key_hint="4321",
    ))
    await session.commit()
    config = await image_gen.resolve_config(session, user.id)
    assert config.api_key == "sk-img-87654321"
    assert config.base_url == llm.ANTHROPIC_COMPAT_BASE_URL


async def test_resolve_config_falls_back_to_system(session, users, monkeypatch):
    monkeypatch.setattr(image_gen.settings, "image_generation_api_key", "sk-img")
    monkeypatch.setattr(image_gen.settings, "image_generation_model", "gemini-image")
    user = await users.create()
    # A chat-only BYO row must not disable image generation.
    session.add(UserAISettings(
        user_id=user.id, provider="openai", model="gpt-5",
        api_key_enc=crypto.encrypt_token("sk-main-12345678"), key_hint="5678",
    ))
    await session.commit()
    config = await image_gen.resolve_config(session, user.id)
    assert config.provider == "system"
    assert config.user_owned is False


async def test_resolve_config_none_when_nothing(session, users, monkeypatch):
    monkeypatch.setattr(image_gen.settings, "image_generation_api_key", "")
    user = await users.create()
    assert await image_gen.resolve_config(session, user.id) is None


# --- generation wire shapes ---

PNG_BYTES = b"\x89PNG fake"
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode()


class _FakeClient:
    """AsyncOpenAI stand-in covering both wire shapes."""

    def __init__(self, *, chat_response=None, images_response=None, **kwargs):
        _FakeClient.constructed = dict(kwargs)

        async def chat_create(**call):
            _FakeClient.chat_call = call
            return chat_response

        async def images_generate(**call):
            _FakeClient.images_call = call
            return images_response

        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=chat_create)
        )
        self.images = types.SimpleNamespace(generate=images_generate)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _openrouter_config(**overrides):
    defaults = dict(
        provider="system", api_key="sk-img", model="google/gemini-2.5-flash-image",
        base_url="https://openrouter.ai/api/v1",
    )
    defaults.update(overrides)
    return llm.LLMConfig(**defaults)


async def test_generate_via_openrouter_chat_modalities(monkeypatch):
    message = types.SimpleNamespace(
        images=[{"type": "image_url", "image_url": {"url": DATA_URL}}],
        model_extra={},
    )
    response = types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=message)],
        usage=types.SimpleNamespace(prompt_tokens=12, completion_tokens=1290),
    )
    monkeypatch.setattr(
        image_gen, "AsyncOpenAI",
        lambda **kw: _FakeClient(chat_response=response, **kw),
    )
    data, content_type, usage = await image_gen.generate(_openrouter_config(), "a prompt")
    assert data == PNG_BYTES
    assert content_type == "image/png"
    assert usage.completion_tokens == 1290
    assert _FakeClient.chat_call["extra_body"] == {"modalities": ["image", "text"]}
    assert _FakeClient.chat_call["messages"] == [{"role": "user", "content": "a prompt"}]


async def test_generate_openrouter_no_image_raises(monkeypatch):
    message = types.SimpleNamespace(images=[], model_extra={})
    response = types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=message)], usage=None
    )
    monkeypatch.setattr(
        image_gen, "AsyncOpenAI",
        lambda **kw: _FakeClient(chat_response=response, **kw),
    )
    with pytest.raises(RuntimeError, match="no image"):
        await image_gen.generate(_openrouter_config(), "p")


async def test_generate_via_images_api(monkeypatch):
    response = types.SimpleNamespace(
        data=[types.SimpleNamespace(b64_json=base64.b64encode(PNG_BYTES).decode(), url=None)]
    )
    monkeypatch.setattr(
        image_gen, "AsyncOpenAI",
        lambda **kw: _FakeClient(images_response=response, **kw),
    )
    config = _openrouter_config(provider="openai", base_url=None)
    data, content_type, usage = await image_gen.generate(config, "a prompt")
    assert data == PNG_BYTES
    assert content_type == "image/png"
    assert _FakeClient.images_call["prompt"] == "a prompt"


# --- background task ---

async def _article(session):
    feed = Feed(url="https://feed/img")
    session.add(feed)
    await session.flush()
    article = Article(feed_id=feed.id, guid="g-img", url="https://x/a", title="T")
    session.add(article)
    await session.commit()
    await session.refresh(article)
    return article


async def test_generate_for_article_stores_and_logs(session, users, monkeypatch):
    user = await users.create()
    article = await _article(session)
    usage = llm.TokenUsage()
    usage.add(10, 1000)

    async def fake_generate(config, prompt):
        return PNG_BYTES, "image/png", usage

    monkeypatch.setattr(image_gen, "generate", fake_generate)
    config = _openrouter_config(provider="openai", user_owned=True)
    await image_gen.generate_for_article(article.id, user.id, config, "p")

    image = await session.get(GeneratedImage, article.id)
    assert image.data == PNG_BYTES
    await session.refresh(article)
    assert article.image_url.endswith(f"/api/articles/{article.id}/generated-image")
    row = (await session.scalars(select(LLMUsage))).one()
    assert row.feature == "image"
    assert row.completion_tokens == 1000


async def test_generate_for_article_keeps_existing_image_url(session, users, monkeypatch):
    user = await users.create()
    article = await _article(session)
    article.image_url = "https://original/og.png"
    await session.commit()

    async def fake_generate(config, prompt):
        return PNG_BYTES, "image/png", llm.TokenUsage()

    monkeypatch.setattr(image_gen, "generate", fake_generate)
    await image_gen.generate_for_article(
        article.id, user.id, _openrouter_config(), "p"
    )
    await session.refresh(article)
    assert article.image_url == "https://original/og.png"


async def test_generate_for_article_failure_logs_error(session, users, monkeypatch):
    user = await users.create()
    article = await _article(session)

    async def boom(config, prompt):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(image_gen, "generate", boom)
    config = _openrouter_config(user_owned=True)
    await image_gen.generate_for_article(article.id, user.id, config, "p")

    assert await session.get(GeneratedImage, article.id) is None
    row = (await session.scalars(select(LLMUsage))).one()
    assert row.status == "error"
    assert "model unavailable" in row.error
