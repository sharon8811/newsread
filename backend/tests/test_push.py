import httpx
import respx
from sqlalchemy import select

from app import push
from app.models import Device


async def _device(session, user, token, platform="ios"):
    device = Device(user_id=user.id, push_token=token, platform=platform)
    session.add(device)
    await session.commit()
    return device


async def test_send_push_no_users():
    assert await push.send_push([], "t", "b") == 0


async def test_send_push_no_devices(users):
    user = await users.create()
    assert await push.send_push([user.id], "t", "b") == 0


@respx.mock
async def test_send_push_success(session, users):
    user = await users.create()
    await _device(session, user, "tok-1")
    await _device(session, user, "tok-2", platform="android")
    route = respx.post(push.EXPO_PUSH_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"status": "ok"}, {"status": "ok"}]})
    )
    sent = await push.send_push([user.id], "Title", "Body", data={"share_id": 1})
    assert sent == 2
    payload = route.calls.last.request.read()
    assert b"tok-1" in payload and b"tok-2" in payload
    assert b"Title" in payload


@respx.mock
async def test_send_push_prunes_unregistered_tokens(session, users):
    user = await users.create()
    await _device(session, user, "tok-live")
    await _device(session, user, "tok-dead")
    respx.post(push.EXPO_PUSH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"status": "ok"},
                    {"status": "error", "details": {"error": "DeviceNotRegistered"}},
                ]
            },
        )
    )
    sent = await push.send_push([user.id], "t", "b")
    assert sent == 1
    remaining = (await session.scalars(select(Device))).all()
    assert [d.push_token for d in remaining] == ["tok-live"]


@respx.mock
async def test_send_push_swallows_http_errors(session, users):
    user = await users.create()
    await _device(session, user, "tok-1")
    respx.post(push.EXPO_PUSH_URL).mock(return_value=httpx.Response(500))
    assert await push.send_push([user.id], "t", "b") == 0
    # The device is kept: a transient server error is not a dead token.
    assert len((await session.scalars(select(Device))).all()) == 1


@respx.mock
async def test_send_push_batches_over_expo_limit(session, users, monkeypatch):
    monkeypatch.setattr(push, "EXPO_BATCH", 2)
    user = await users.create()
    for n in range(3):
        await _device(session, user, f"tok-{n}")
    route = respx.post(push.EXPO_PUSH_URL).mock(
        side_effect=[
            httpx.Response(200, json={"data": [{"status": "ok"}, {"status": "ok"}]}),
            httpx.Response(200, json={"data": [{"status": "ok"}]}),
        ]
    )
    assert await push.send_push([user.id], "t", "b") == 3
    assert route.call_count == 2
