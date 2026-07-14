"""LLM usage endpoints: window aggregation, breakdowns, and the event log."""

from datetime import UTC, datetime, timedelta

from app import crypto
from app.models import LLMUsage, UserAISettings


def _utc_now():
    return datetime.now(UTC)


async def _row(
    session,
    user,
    *,
    days_ago=0,
    feature="qa",
    provider="openai",
    model="gpt-5",
    prompt=100,
    completion=20,
    status="ok",
    error=None,
    duration_ms=500,
):
    row = LLMUsage(
        user_id=user.id,
        feature=feature,
        provider=provider,
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        duration_ms=duration_ms,
        status=status,
        error=error,
        created_at=_utc_now() - timedelta(days=days_ago),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


# --- summary ---


async def test_summary_empty(client, users):
    user = await users.create()
    resp = await client.get("/api/usage/summary", headers=users.auth(user))
    assert resp.status_code == 200
    body = resp.json()
    assert body["range"] == "week"
    assert body["configured"] is False
    assert body["total_calls"] == 0
    assert body["total_tokens"] == 0
    assert body["error_count"] == 0
    assert len(body["days"]) == 7
    assert all(d["calls"] == 0 and d["tokens"] == 0 for d in body["days"])
    assert body["by_feature"] == []
    assert body["by_model"] == []


async def test_summary_aggregates_window(client, users, session):
    user = await users.create()
    await _row(session, user, feature="qa", prompt=100, completion=20)
    await _row(session, user, feature="qa", days_ago=1, prompt=50, completion=10)
    await _row(
        session,
        user,
        feature="summary",
        days_ago=2,
        model="gpt-6",
        prompt=30,
        completion=5,
        status="error",
        error="boom",
    )
    # Outside the week window — lands in the previous one instead.
    await _row(session, user, days_ago=10, prompt=1000, completion=0)

    resp = await client.get("/api/usage/summary?range=week", headers=users.auth(user))
    body = resp.json()
    assert body["total_calls"] == 3
    assert body["total_tokens"] == 215
    assert body["prev_total_tokens"] == 1000
    assert body["error_count"] == 1
    assert body["days"][-1]["tokens"] == 120  # today
    assert body["days"][-2]["tokens"] == 60

    features = {f["feature"]: f for f in body["by_feature"]}
    assert features["qa"]["calls"] == 2
    assert features["qa"]["tokens"] == 180
    assert features["summary"]["tokens"] == 35
    # Ordered by tokens, descending.
    assert body["by_feature"][0]["feature"] == "qa"

    models = {m["model"]: m for m in body["by_model"]}
    assert models["gpt-5"]["calls"] == 2
    assert models["gpt-6"]["provider"] == "openai"


async def test_summary_scoped_to_user(client, users, session):
    user = await users.create()
    other = await users.create()
    await _row(session, other, prompt=999, completion=1)
    resp = await client.get("/api/usage/summary", headers=users.auth(user))
    assert resp.json()["total_tokens"] == 0


async def test_summary_reports_configured_key(client, users, session):
    user = await users.create()
    session.add(
        UserAISettings(
            user_id=user.id,
            provider="openai",
            model="gpt-5",
            api_key_enc=crypto.encrypt_token("sk-own-12345678"),
            key_hint="5678",
        )
    )
    await session.commit()
    resp = await client.get("/api/usage/summary", headers=users.auth(user))
    assert resp.json()["configured"] is True


async def test_summary_year_range(client, users, session):
    user = await users.create()
    await _row(session, user, days_ago=100, prompt=10, completion=0)
    resp = await client.get("/api/usage/summary?range=year", headers=users.auth(user))
    body = resp.json()
    assert len(body["days"]) == 365
    assert body["total_tokens"] == 10


async def test_summary_rejects_bad_range(client, users):
    user = await users.create()
    resp = await client.get("/api/usage/summary?range=decade", headers=users.auth(user))
    assert resp.status_code == 422


# --- events ---


async def test_events_newest_first_and_paginated(client, users, session):
    user = await users.create()
    rows = [await _row(session, user, model=f"m{i}") for i in range(5)]

    resp = await client.get("/api/usage/events?limit=2", headers=users.auth(user))
    body = resp.json()
    assert [e["id"] for e in body] == [rows[4].id, rows[3].id]
    assert body[0]["model"] == "m4"
    assert body[0]["prompt_tokens"] == 100
    assert body[0]["status"] == "ok"

    resp = await client.get(
        f"/api/usage/events?limit=2&before_id={body[-1]['id']}", headers=users.auth(user)
    )
    assert [e["id"] for e in resp.json()] == [rows[2].id, rows[1].id]


async def test_events_scoped_to_user(client, users, session):
    user = await users.create()
    other = await users.create()
    await _row(session, other)
    resp = await client.get("/api/usage/events", headers=users.auth(user))
    assert resp.json() == []


async def test_events_includes_error_detail(client, users, session):
    user = await users.create()
    await _row(session, user, status="error", error="rate limited")
    body = (await client.get("/api/usage/events", headers=users.auth(user))).json()
    assert body[0]["status"] == "error"
    assert body[0]["error"] == "rate limited"
