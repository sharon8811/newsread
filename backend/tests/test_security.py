import jwt
import pytest
from fastapi import HTTPException

from app.config import settings
from app.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)


def test_hash_and_verify_roundtrip():
    h = hash_password("s3cret-password")
    assert h != "s3cret-password"
    assert verify_password("s3cret-password", h)
    assert not verify_password("wrong", h)


def test_verify_password_bad_hash_returns_false():
    # bcrypt raises ValueError on a non-bcrypt hash; must degrade to False.
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_create_access_token_encodes_subject():
    token = create_access_token(42)
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    assert payload["sub"] == "42"
    assert "exp" in payload


async def test_get_current_user_returns_user(session, users):
    user = await users.create()
    creds = type("C", (), {"credentials": create_access_token(user.id)})()
    result = await get_current_user(credentials=creds, session=session)
    assert result.id == user.id


async def test_get_current_user_no_credentials(session):
    with pytest.raises(HTTPException) as exc:
        await get_current_user(credentials=None, session=session)
    assert exc.value.status_code == 401


async def test_get_current_user_bad_token(session):
    creds = type("C", (), {"credentials": "garbage.token.value"})()
    with pytest.raises(HTTPException) as exc:
        await get_current_user(credentials=creds, session=session)
    assert exc.value.status_code == 401


async def test_get_current_user_token_missing_sub(session):
    token = jwt.encode({"foo": "bar"}, settings.jwt_secret, algorithm="HS256")
    creds = type("C", (), {"credentials": token})()
    with pytest.raises(HTTPException) as exc:
        await get_current_user(credentials=creds, session=session)
    assert exc.value.status_code == 401


async def test_get_current_user_unknown_user(session):
    token = create_access_token(999999)
    creds = type("C", (), {"credentials": token})()
    with pytest.raises(HTTPException) as exc:
        await get_current_user(credentials=creds, session=session)
    assert exc.value.status_code == 401
