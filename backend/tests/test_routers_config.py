"""GET /api/config — deployment feature flags served to web/mobile clients."""

from app.config import settings


async def test_config_is_unauthenticated(client):
    resp = await client.get("/api/config")
    assert resp.status_code == 200
    assert set(resp.json()) == {
        "allow_signup",
        "messaging_enabled",
        "browser_history_enabled",
    }


async def test_config_reflects_open_flags(client):
    # conftest pins allow_signup / messaging_enabled to true for the suite.
    resp = await client.get("/api/config")
    assert resp.json() == {
        "allow_signup": True,
        "messaging_enabled": True,
        "browser_history_enabled": True,
    }


async def test_signups_closed_still_open_for_first_user(client, monkeypatch):
    monkeypatch.setattr(settings, "allow_signup", False)
    resp = await client.get("/api/config")
    assert resp.json()["allow_signup"] is True  # no accounts yet: owner setup


async def test_signups_closed_once_a_user_exists(client, users, monkeypatch):
    monkeypatch.setattr(settings, "allow_signup", False)
    await users.create()
    resp = await client.get("/api/config")
    assert resp.json()["allow_signup"] is False


async def test_messaging_flag_reflected(client, monkeypatch):
    monkeypatch.setattr(settings, "messaging_enabled", False)
    resp = await client.get("/api/config")
    assert resp.json()["messaging_enabled"] is False


async def test_browser_history_flag_reflected(client, monkeypatch):
    monkeypatch.setattr(settings, "browser_history_enabled", False)
    resp = await client.get("/api/config")
    assert resp.json()["browser_history_enabled"] is False
