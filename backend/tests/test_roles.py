"""Instance roles: bootstrap, suspended-account rejection, admin/owner gates,
and the final-owner safeguards."""

import pytest
from fastapi import HTTPException

from app.config import settings
from app.roles import FinalOwnerError, change_role, change_status
from app.security import get_current_admin, get_current_owner

REGISTRATION = {
    "email": "alice@example.com",
    "username": "alice",
    "name": "Alice",
    "password": "password123",
}
SECOND_REGISTRATION = {
    "email": "bob@example.com",
    "username": "bob",
    "name": "Bob",
    "password": "password123",
}


# --- Bootstrap: first account and the owner role ---


async def test_first_account_becomes_owner_when_enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "first_account_owner", True)
    resp = await client.post("/api/auth/register", json=REGISTRATION)
    assert resp.status_code == 201
    assert resp.json()["user"]["role"] == "owner"


async def test_second_account_is_regular_user(client, monkeypatch):
    monkeypatch.setattr(settings, "first_account_owner", True)
    await client.post("/api/auth/register", json=REGISTRATION)
    resp = await client.post("/api/auth/register", json=SECOND_REGISTRATION)
    assert resp.status_code == 201
    assert resp.json()["user"]["role"] == "user"


async def test_hosted_signup_never_mints_an_owner(client, monkeypatch):
    monkeypatch.setattr(settings, "first_account_owner", False)
    resp = await client.post("/api/auth/register", json=REGISTRATION)
    assert resp.status_code == 201
    assert resp.json()["user"]["role"] == "user"


async def test_me_includes_role(client, users):
    admin = await users.create(username="root", role="admin")
    resp = await client.get("/api/auth/me", headers=users.auth(admin))
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


# --- Suspended accounts ---


async def test_suspended_user_rejected_with_valid_token(client, users):
    user = await users.create(username="mallory")
    headers = users.auth(user)
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200

    user.status = "suspended"
    await users.session.commit()

    resp = await client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 403
    assert "suspended" in resp.json()["detail"].lower()


async def test_suspended_user_cannot_login(client, users):
    await users.create(username="mallory", password="hunter2xx", status="suspended")
    resp = await client.post(
        "/api/auth/login", json={"identifier": "mallory", "password": "hunter2xx"}
    )
    assert resp.status_code == 403
    assert "suspended" in resp.json()["detail"].lower()


async def test_suspended_user_cannot_refresh(client, users):
    user = await users.create(username="mallory", status="suspended")
    resp = await client.post("/api/auth/refresh", headers=users.auth(user))
    assert resp.status_code == 403


async def test_suspended_user_browser_token_rejected(client, users):
    """Suspension also covers the extension's scoped nrh_ tokens — a paired
    browser must not keep syncing history for a suspended account."""
    user = await users.create(username="mallory")
    paired = await client.post(
        "/api/history/connections", json={"name": "Chrome"}, headers=users.auth(user)
    )
    assert paired.status_code == 201
    token = paired.json()["token"]

    browser_headers = {"Authorization": f"Bearer {token}"}
    assert (
        await client.get("/api/history/sync/status", headers=browser_headers)
    ).status_code == 200

    user.status = "suspended"
    await users.session.commit()

    resp = await client.get("/api/history/sync/status", headers=browser_headers)
    assert resp.status_code == 403
    assert "suspended" in resp.json()["detail"].lower()


# --- Admin / owner dependencies ---


async def test_admin_gate_rejects_regular_user(users):
    user = await users.create(username="pleb")
    with pytest.raises(HTTPException) as exc:
        await get_current_admin(user)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("role", ["admin", "owner"])
async def test_admin_gate_admits_admin_and_owner(users, role):
    user = await users.create(username=f"boss_{role}", role=role)
    assert await get_current_admin(user) is user


async def test_owner_gate_rejects_admin(users):
    admin = await users.create(username="justadmin", role="admin")
    with pytest.raises(HTTPException) as exc:
        await get_current_owner(admin)
    assert exc.value.status_code == 403


async def test_owner_gate_admits_owner(users):
    owner = await users.create(username="realowner", role="owner")
    assert await get_current_owner(owner) is owner


# --- Final-owner safeguards (roles.py) ---


async def test_cannot_demote_only_owner(session, users):
    owner = await users.create(username="solo", role="owner")
    with pytest.raises(FinalOwnerError):
        await change_role(session, owner, "user")
    assert owner.role == "owner"


async def test_demote_allowed_with_second_owner(session, users):
    owner = await users.create(username="first", role="owner")
    await users.create(username="second", role="owner")
    await change_role(session, owner, "admin")
    await session.commit()
    assert owner.role == "admin"


async def test_suspended_owner_does_not_count_as_backup(session, users):
    owner = await users.create(username="active", role="owner")
    await users.create(username="benched", role="owner", status="suspended")
    with pytest.raises(FinalOwnerError):
        await change_role(session, owner, "user")


async def test_cannot_suspend_only_owner(session, users):
    owner = await users.create(username="solo", role="owner")
    with pytest.raises(FinalOwnerError):
        await change_status(session, owner, "suspended")
    assert owner.status == "active"


async def test_suspend_regular_user_and_reactivate(session, users):
    user = await users.create(username="pleb")
    await change_status(session, user, "suspended")
    assert user.status == "suspended"
    await change_status(session, user, "active")
    assert user.status == "active"


async def test_change_role_rejects_unknown_role(session, users):
    user = await users.create(username="pleb")
    with pytest.raises(ValueError):
        await change_role(session, user, "superuser")


async def test_change_status_rejects_unknown_status(session, users):
    user = await users.create(username="pleb")
    with pytest.raises(ValueError):
        await change_status(session, user, "banned")


# --- set_role.py bootstrap script ---


async def test_set_role_script_promotes_and_guards(users, monkeypatch, capsys):
    import argparse

    from scripts import set_role

    async def no_init():
        return None

    monkeypatch.setattr(set_role, "init_db", no_init)
    await users.create(username="operator", email="op@example.com")

    args = argparse.Namespace(user="op@example.com", role="owner", list=False)
    assert await set_role.run(args) == 0
    assert "operator" in capsys.readouterr().out

    # Demoting the only owner is refused with a non-zero exit.
    args = argparse.Namespace(user="operator", role="user", list=False)
    assert await set_role.run(args) == 1
    assert "refused" in capsys.readouterr().err

    # Unknown accounts fail cleanly; --list shows the promoted owner.
    args = argparse.Namespace(user="ghost", role="admin", list=False)
    assert await set_role.run(args) == 1
    assert await set_role.run(argparse.Namespace(user=None, role=None, list=True)) == 0
    assert "owner" in capsys.readouterr().out
