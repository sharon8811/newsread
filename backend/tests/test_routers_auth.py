from app.config import settings

REGISTRATION = {
    "email": "alice@example.com",
    "username": "alice",
    "name": "Alice",
    "password": "password123",
}


async def test_register_success(client):
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": "Alice@Example.com",
            "username": "alice",
            "name": "Alice",
            "password": "password123",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == "alice@example.com"  # lowercased
    assert body["user"]["username"] == "alice"


async def test_register_duplicate_email(client, users):
    await users.create(username="bob", email="bob@example.com")
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": "bob@example.com",
            "username": "different",
            "name": "Bob2",
            "password": "password123",
        },
    )
    assert resp.status_code == 409
    assert "email" in resp.json()["detail"]


async def test_register_duplicate_username(client, users):
    await users.create(username="carol", email="carol@example.com")
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": "other@example.com",
            "username": "Carol",
            "name": "Carol2",
            "password": "password123",
        },
    )
    assert resp.status_code == 409
    assert "username" in resp.json()["detail"]


async def test_register_validation_error(client):
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": "not-an-email",
            "username": "x",  # too short
            "name": "",
            "password": "short",
        },
    )
    assert resp.status_code == 422


async def test_login_with_email(client, users):
    await users.create(username="dave", email="dave@example.com", password="hunter2xx")
    resp = await client.post(
        "/api/auth/login",
        json={
            "identifier": "dave@example.com",
            "password": "hunter2xx",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["username"] == "dave"


async def test_login_with_username_case_insensitive(client, users):
    await users.create(username="eve", email="eve@example.com", password="hunter2xx")
    resp = await client.post(
        "/api/auth/login",
        json={
            "identifier": "  EVE ",
            "password": "hunter2xx",
        },
    )
    assert resp.status_code == 200


async def test_login_wrong_password(client, users):
    await users.create(username="frank", password="correct-horse")
    resp = await client.post(
        "/api/auth/login",
        json={
            "identifier": "frank",
            "password": "wrong",
        },
    )
    assert resp.status_code == 401


async def test_login_unknown_user(client):
    resp = await client.post(
        "/api/auth/login",
        json={
            "identifier": "ghost",
            "password": "whatever12",
        },
    )
    assert resp.status_code == 401


async def test_me_authenticated(client, users):
    user = await users.create(username="grace")
    resp = await client.get("/api/auth/me", headers=users.auth(user))
    assert resp.status_code == 200
    assert resp.json()["username"] == "grace"


async def test_me_unauthenticated(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_refresh_returns_new_working_token(client, users):
    user = await users.create(username="heidi")
    resp = await client.post("/api/auth/refresh", headers=users.auth(user))
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["username"] == "heidi"
    # The minted token must itself authenticate.
    me = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200


async def test_refresh_unauthenticated(client):
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401


async def test_register_first_user_allowed_when_signups_closed(client, monkeypatch):
    monkeypatch.setattr(settings, "allow_signup", False)
    resp = await client.post("/api/auth/register", json=REGISTRATION)
    assert resp.status_code == 201


async def test_register_forbidden_when_signups_closed_and_user_exists(client, users, monkeypatch):
    monkeypatch.setattr(settings, "allow_signup", False)
    await users.create(username="owner", email="owner@example.com")
    resp = await client.post("/api/auth/register", json=REGISTRATION)
    assert resp.status_code == 403
    assert "disabled" in resp.json()["detail"]
