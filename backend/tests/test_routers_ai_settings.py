"""Bring-your-own-key AI settings: CRUD, key secrecy, config resolution,
usage recording, and the live test endpoint."""

import types

from sqlalchemy import select

from app import crypto, llm
from app.models import LLMUsage, UserAISettings
from app.routers import ai_settings as ai_settings_router

BODY = {"provider": "openai", "model": "gpt-5", "api_key": "sk-test-12345678"}


async def _put(client, users, user, body=None):
    return await client.put("/api/ai/settings", json=body or BODY, headers=users.auth(user))


# --- GET ---

async def test_get_unconfigured(client, users):
    user = await users.create()
    resp = await client.get("/api/ai/settings", headers=users.auth(user))
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["system_available"] is False
    assert body["provider"] is None


async def test_get_reports_system_available(client, users, monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    user = await users.create()
    resp = await client.get("/api/ai/settings", headers=users.auth(user))
    assert resp.json()["system_available"] is True


# --- PUT ---

async def test_put_creates_settings(client, users):
    user = await users.create()
    resp = await _put(client, users, user)
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-5"
    assert body["key_hint"] == "5678"
    # The key itself never comes back.
    assert "sk-test" not in resp.text

    resp = await client.get("/api/ai/settings", headers=users.auth(user))
    assert resp.json()["configured"] is True


async def test_put_encrypts_key_at_rest(client, users, session):
    user = await users.create()
    await _put(client, users, user)
    row = await session.get(UserAISettings, user.id)
    assert row.api_key_enc != BODY["api_key"]
    assert crypto.decrypt_token(row.api_key_enc) == BODY["api_key"]


async def test_put_requires_key_on_create(client, users):
    user = await users.create()
    resp = await _put(client, users, user, {"provider": "openai", "model": "gpt-5"})
    assert resp.status_code == 422


async def test_put_custom_requires_base_url(client, users):
    user = await users.create()
    body = {**BODY, "provider": "custom"}
    assert (await _put(client, users, user, body)).status_code == 422
    body["base_url"] = "http://ollama.local/v1"
    resp = await _put(client, users, user, body)
    assert resp.status_code == 200
    assert resp.json()["base_url"] == "http://ollama.local/v1"


async def test_put_update_without_key_keeps_stored_key(client, users, session):
    user = await users.create()
    await _put(client, users, user)
    resp = await _put(client, users, user, {"provider": "openai", "model": "gpt-6"})
    assert resp.status_code == 200
    assert resp.json()["model"] == "gpt-6"
    assert resp.json()["key_hint"] == "5678"
    row = await session.get(UserAISettings, user.id)
    assert crypto.decrypt_token(row.api_key_enc) == BODY["api_key"]


async def test_put_image_same_provider_reuses_main_key(client, users):
    user = await users.create()
    body = {**BODY, "image": {"provider": "openai", "model": "gpt-image-1"}}
    resp = await _put(client, users, user, body)
    assert resp.status_code == 200
    image = resp.json()["image"]
    assert image["model"] == "gpt-image-1"
    assert image["key_hint"] == ""  # falls back to the main key at call time


async def test_put_image_other_provider_requires_key(client, users):
    user = await users.create()
    body = {**BODY, "image": {"provider": "anthropic", "model": "img-model"}}
    assert (await _put(client, users, user, body)).status_code == 422
    body["image"]["api_key"] = "sk-img-87654321"
    resp = await _put(client, users, user, body)
    assert resp.status_code == 200
    assert resp.json()["image"]["key_hint"] == "4321"


async def test_put_image_custom_requires_base_url(client, users):
    user = await users.create()
    body = {**BODY, "image": {"provider": "custom", "model": "m", "api_key": "sk-img-87654321"}}
    assert (await _put(client, users, user, body)).status_code == 422


async def test_put_without_image_clears_it(client, users):
    user = await users.create()
    await _put(client, users, user, {**BODY, "image": {"provider": "openai", "model": "gpt-image-1"}})
    resp = await _put(client, users, user)
    assert resp.json()["image"] is None


async def test_put_503_when_crypto_unconfigured(client, users, monkeypatch):
    monkeypatch.setattr(crypto, "is_configured", lambda: False)
    user = await users.create()
    assert (await _put(client, users, user)).status_code == 503


# --- DELETE ---

async def test_delete_reverts_to_system(client, users):
    user = await users.create()
    await _put(client, users, user)
    assert (await client.delete("/api/ai/settings", headers=users.auth(user))).status_code == 204
    resp = await client.get("/api/ai/settings", headers=users.auth(user))
    assert resp.json()["configured"] is False
    # Idempotent.
    assert (await client.delete("/api/ai/settings", headers=users.auth(user))).status_code == 204


# --- config resolution (llm.resolve_config) ---

async def test_resolve_config_prefers_user_key(session, users):
    user = await users.create()
    session.add(UserAISettings(
        user_id=user.id, provider="anthropic", model="claude-x",
        api_key_enc=crypto.encrypt_token("sk-ant-12345678"), key_hint="5678",
    ))
    await session.commit()
    config = await llm.resolve_config(session, user.id)
    assert config.user_owned is True
    assert config.api_key == "sk-ant-12345678"
    assert config.model == "claude-x"
    assert config.base_url == llm.ANTHROPIC_COMPAT_BASE_URL


async def test_resolve_config_falls_back_to_system(session, users, monkeypatch):
    monkeypatch.setattr(llm.settings, "openai_api_key", "sys-key")
    monkeypatch.setattr(llm.settings, "openai_model", "sys-model")
    user = await users.create()
    config = await llm.resolve_config(session, user.id)
    assert config.provider == "system"
    assert config.user_owned is False


async def test_resolve_config_none_when_nothing(session, users):
    user = await users.create()
    assert await llm.resolve_config(session, user.id) is None


def test_resolve_base_url():
    assert llm.resolve_base_url("openai", "ignored") is None
    assert llm.resolve_base_url("anthropic", None) == llm.ANTHROPIC_COMPAT_BASE_URL
    assert llm.resolve_base_url("custom", "http://x/v1") == "http://x/v1"
    assert llm.resolve_base_url("custom", "") is None


def test_user_client_builds_from_config():
    config = llm.LLMConfig(provider="custom", api_key="sk-a-12345678",
                           base_url="http://ollama.local/v1", model="m", user_owned=True)
    client = llm.user_client(config)
    assert client.api_key == "sk-a-12345678"
    assert str(client.base_url).startswith("http://ollama.local/v1")


# --- usage recording ---

async def test_record_usage_skips_system_config(session, users):
    user = await users.create()
    system = llm.LLMConfig(provider="system", api_key="k", base_url=None, model="m")
    await llm.record_usage(session, user_id=user.id, feature="summary", config=system)
    await llm.record_usage(session, user_id=user.id, feature="summary", config=None)
    assert (await session.scalars(select(LLMUsage))).all() == []


async def test_record_usage_writes_row_for_user_key(session, users):
    user = await users.create()
    config = llm.LLMConfig(provider="openai", api_key="k", base_url=None,
                           model="gpt-5", user_owned=True)
    usage = llm.TokenUsage()
    usage.add(100, 20)
    usage.add(None, 5)  # providers sometimes omit counts
    await llm.record_usage(
        session, user_id=user.id, feature="qa", config=config, usage=usage,
        duration_ms=1234, status="error", error="x" * 900,
    )
    row = (await session.scalars(select(LLMUsage))).one()
    assert row.user_id == user.id
    assert row.feature == "qa"
    assert row.provider == "openai"
    assert row.model == "gpt-5"
    assert row.prompt_tokens == 100
    assert row.completion_tokens == 25
    assert row.duration_ms == 1234
    assert row.status == "error"
    assert len(row.error) == 500  # truncated


# --- POST /ai/settings/test ---

class _FakeOpenAI:
    """Captures constructor args; create() behavior injected per test."""

    captured: dict = {}
    create_error: Exception | None = None

    def __init__(self, **kwargs):
        type(self).captured = dict(kwargs)

        async def create(**call_kwargs):
            type(self).captured["call"] = call_kwargs
            if type(self).create_error is not None:
                raise type(self).create_error
            return types.SimpleNamespace()

        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create)
        )

    async def close(self):
        type(self).captured["closed"] = True


async def test_test_endpoint_ok(client, users, monkeypatch):
    monkeypatch.setattr(ai_settings_router, "AsyncOpenAI", _FakeOpenAI)
    _FakeOpenAI.create_error = None
    user = await users.create()
    resp = await client.post("/api/ai/settings/test", json=BODY, headers=users.auth(user))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "detail": None, "model": "gpt-5"}
    assert _FakeOpenAI.captured["api_key"] == BODY["api_key"]
    assert _FakeOpenAI.captured["base_url"] is None
    assert _FakeOpenAI.captured["call"]["model"] == "gpt-5"


async def test_test_endpoint_maps_anthropic_base_url(client, users, monkeypatch):
    monkeypatch.setattr(ai_settings_router, "AsyncOpenAI", _FakeOpenAI)
    _FakeOpenAI.create_error = None
    user = await users.create()
    body = {**BODY, "provider": "anthropic"}
    await client.post("/api/ai/settings/test", json=body, headers=users.auth(user))
    assert _FakeOpenAI.captured["base_url"] == llm.ANTHROPIC_COMPAT_BASE_URL


async def test_test_endpoint_reports_failure(client, users, monkeypatch):
    monkeypatch.setattr(ai_settings_router, "AsyncOpenAI", _FakeOpenAI)
    _FakeOpenAI.create_error = RuntimeError("invalid api key")
    user = await users.create()
    resp = await client.post("/api/ai/settings/test", json=BODY, headers=users.auth(user))
    _FakeOpenAI.create_error = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "invalid api key" in body["detail"]


async def test_test_endpoint_uses_stored_settings(client, users, monkeypatch):
    monkeypatch.setattr(ai_settings_router, "AsyncOpenAI", _FakeOpenAI)
    _FakeOpenAI.create_error = None
    user = await users.create()
    await _put(client, users, user)
    resp = await client.post("/api/ai/settings/test", json={}, headers=users.auth(user))
    assert resp.json()["ok"] is True
    # Stored key was decrypted and used.
    assert _FakeOpenAI.captured["api_key"] == BODY["api_key"]


async def test_test_endpoint_422_without_settings(client, users):
    user = await users.create()
    resp = await client.post("/api/ai/settings/test", json={}, headers=users.auth(user))
    assert resp.status_code == 422


async def test_put_update_keeps_stored_image_key(client, users):
    user = await users.create()
    body = {**BODY, "image": {"provider": "anthropic", "model": "img",
                              "api_key": "sk-img-87654321"}}
    await _put(client, users, user, body)
    del body["image"]["api_key"]
    resp = await _put(client, users, user, body)
    assert resp.status_code == 200
    assert resp.json()["image"]["key_hint"] == "4321"


async def test_test_endpoint_undecryptable_stored_key_503(client, users, monkeypatch):
    user = await users.create()
    await _put(client, users, user)

    def broken(ciphertext):
        raise crypto.TokenCryptoError("key changed")

    monkeypatch.setattr(crypto, "decrypt_token", broken)
    resp = await client.post("/api/ai/settings/test", json={}, headers=users.auth(user))
    assert resp.status_code == 503


async def test_test_endpoint_closes_client(client, users, monkeypatch):
    monkeypatch.setattr(ai_settings_router, "AsyncOpenAI", _FakeOpenAI)
    _FakeOpenAI.create_error = None
    user = await users.create()
    await client.post("/api/ai/settings/test", json=BODY, headers=users.auth(user))
    assert _FakeOpenAI.captured["closed"] is True


async def test_test_endpoint_never_sends_stored_key_elsewhere(client, users, monkeypatch):
    """The stored (write-only) key must not be combined with a caller-supplied
    endpoint — that would exfiltrate it to an attacker-chosen host."""
    monkeypatch.setattr(ai_settings_router, "AsyncOpenAI", _FakeOpenAI)
    _FakeOpenAI.create_error = None
    user = await users.create()
    await _put(client, users, user)
    resp = await client.post("/api/ai/settings/test",
                             json={"base_url": "https://evil.example/v1"},
                             headers=users.auth(user))
    assert resp.status_code == 422
    resp = await client.post("/api/ai/settings/test",
                             json={"provider": "custom"}, headers=users.auth(user))
    assert resp.status_code == 422


async def test_test_endpoint_custom_requires_base_url(client, users):
    user = await users.create()
    resp = await client.post("/api/ai/settings/test",
                             json={**BODY, "provider": "custom"}, headers=users.auth(user))
    assert resp.status_code == 422


async def test_test_endpoint_key_alone_422(client, users):
    user = await users.create()
    resp = await client.post("/api/ai/settings/test",
                             json={"api_key": "sk-test-12345678"}, headers=users.auth(user))
    assert resp.status_code == 422


async def test_put_custom_update_keeps_stored_base_url(client, users):
    user = await users.create()
    body = {**BODY, "provider": "custom", "base_url": "http://ollama.local/v1"}
    await _put(client, users, user, body)
    resp = await _put(client, users, user,
                      {"provider": "custom", "model": "llama3"})
    assert resp.status_code == 200
    assert resp.json()["base_url"] == "http://ollama.local/v1"
    assert resp.json()["model"] == "llama3"


async def test_put_provider_switch_requires_new_key(client, users):
    """A stored key is never silently reattached to a different provider."""
    user = await users.create()
    await _put(client, users, user)
    resp = await _put(client, users, user, {"provider": "anthropic", "model": "claude-x"})
    assert resp.status_code == 422


async def test_put_image_provider_switch_requires_new_key(client, users):
    user = await users.create()
    await _put(client, users, user, {**BODY, "image": {
        "provider": "anthropic", "model": "img", "api_key": "sk-img-87654321"}})
    resp = await _put(client, users, user, {**BODY, "image": {
        "provider": "custom", "model": "img", "base_url": "http://img.local/v1"}})
    assert resp.status_code == 422


async def test_base_url_must_be_http(client, users):
    user = await users.create()
    body = {**BODY, "provider": "custom", "base_url": "ollama.local/v1"}
    assert (await _put(client, users, user, body)).status_code == 422


# --- article image prompt (users.image_prompt via /users/me) ---

async def test_get_exposes_image_prompt_defaults(client, users, monkeypatch):
    from app import image_gen

    monkeypatch.setattr(image_gen.settings, "image_generation_api_key", "")
    monkeypatch.setattr(image_gen.settings, "image_generation_model", "")
    user = await users.create()
    body = (await client.get("/api/ai/settings", headers=users.auth(user))).json()
    assert body["image_prompt"] is None
    assert body["default_image_prompt"] == image_gen.DEFAULT_IMAGE_PROMPT
    assert body["image_generation_available"] is False


async def test_image_generation_available_via_system_env(client, users, monkeypatch):
    from app import image_gen

    monkeypatch.setattr(image_gen.settings, "image_generation_api_key", "sk-img")
    monkeypatch.setattr(image_gen.settings, "image_generation_model", "img-model")
    user = await users.create()
    body = (await client.get("/api/ai/settings", headers=users.auth(user))).json()
    assert body["image_generation_available"] is True


async def test_image_generation_available_via_user_block(client, users, monkeypatch):
    from app import image_gen

    monkeypatch.setattr(image_gen.settings, "image_generation_api_key", "")
    user = await users.create()
    await _put(client, users, user, {**BODY, "image": {"provider": "openai", "model": "gpt-image-1"}})
    body = (await client.get("/api/ai/settings", headers=users.auth(user))).json()
    assert body["image_generation_available"] is True


async def test_patch_image_prompt_roundtrip(client, users):
    user = await users.create()
    resp = await client.patch("/api/users/me", json={"image_prompt": "  Draw {article_title}  "},
                              headers=users.auth(user))
    assert resp.status_code == 200
    body = (await client.get("/api/ai/settings", headers=users.auth(user))).json()
    assert body["image_prompt"] == "Draw {article_title}"

    # Empty string resets to the default.
    await client.patch("/api/users/me", json={"image_prompt": ""}, headers=users.auth(user))
    body = (await client.get("/api/ai/settings", headers=users.auth(user))).json()
    assert body["image_prompt"] is None
