from sqlalchemy import select

from app.models import Device

TOKEN = "ExponentPushToken[abc123]"


async def test_register_device(client, users, session):
    user = await users.create()
    resp = await client.post(
        "/api/devices",
        json={"push_token": TOKEN, "platform": "ios"},
        headers=users.auth(user),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["push_token"] == TOKEN
    assert body["platform"] == "ios"
    device = await session.scalar(select(Device).where(Device.push_token == TOKEN))
    assert device.user_id == user.id


async def test_register_device_idempotent(client, users, session):
    user = await users.create()
    for _ in range(2):
        resp = await client.post(
            "/api/devices",
            json={"push_token": TOKEN, "platform": "android"},
            headers=users.auth(user),
        )
        assert resp.status_code == 201
    count = len((await session.scalars(select(Device))).all())
    assert count == 1


async def test_register_device_reassigns_to_new_account(client, users, session):
    alice = await users.create(username="alice")
    bob = await users.create(username="bob")
    await client.post(
        "/api/devices",
        json={"push_token": TOKEN, "platform": "ios"},
        headers=users.auth(alice),
    )
    resp = await client.post(
        "/api/devices",
        json={"push_token": TOKEN, "platform": "android"},
        headers=users.auth(bob),
    )
    assert resp.status_code == 201
    devices = (await session.scalars(select(Device))).all()
    assert len(devices) == 1
    assert devices[0].user_id == bob.id
    assert devices[0].platform == "android"


async def test_register_device_bad_platform(client, users):
    user = await users.create()
    resp = await client.post(
        "/api/devices",
        json={"push_token": TOKEN, "platform": "blackberry"},
        headers=users.auth(user),
    )
    assert resp.status_code == 422


async def test_register_device_unauthenticated(client):
    resp = await client.post("/api/devices", json={"push_token": TOKEN, "platform": "ios"})
    assert resp.status_code == 401


async def test_unregister_device(client, users, session):
    user = await users.create()
    await client.post(
        "/api/devices",
        json={"push_token": TOKEN, "platform": "ios"},
        headers=users.auth(user),
    )
    resp = await client.delete(
        "/api/devices", params={"push_token": TOKEN}, headers=users.auth(user)
    )
    assert resp.status_code == 204
    assert (await session.scalars(select(Device))).all() == []


async def test_unregister_device_not_found(client, users):
    user = await users.create()
    resp = await client.delete(
        "/api/devices", params={"push_token": "nope"}, headers=users.auth(user)
    )
    assert resp.status_code == 404


async def test_unregister_device_of_other_user(client, users, session):
    alice = await users.create(username="alice")
    bob = await users.create(username="bob")
    await client.post(
        "/api/devices",
        json={"push_token": TOKEN, "platform": "ios"},
        headers=users.auth(alice),
    )
    resp = await client.delete(
        "/api/devices", params={"push_token": TOKEN}, headers=users.auth(bob)
    )
    assert resp.status_code == 404
    assert len((await session.scalars(select(Device))).all()) == 1
