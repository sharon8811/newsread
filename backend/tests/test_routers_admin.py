"""Instance administration APIs (#116): authorization, metrics aggregation,
user management, safeguards, privacy allowlists, and the audit log."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models import (
    AdminAuditLog,
    ArticleProcessingEvent,
    LLMUsage,
    ReadingActivity,
    UserActivityDay,
)

TODAY = datetime.now(UTC).date()


def _llm_row(user_id, *, billing="user", tokens=(10, 5), status="ok", feature="summary"):
    return LLMUsage(
        user_id=user_id,
        billing_source=billing,
        feature=feature,
        provider="openai",
        model="m",
        prompt_tokens=tokens[0],
        completion_tokens=tokens[1],
        status=status,
    )


# --- Authorization: the API is the boundary ---


GET_ENDPOINTS = ["/api/admin/overview", "/api/admin/trends", "/api/admin/users"]


@pytest.mark.parametrize("path", GET_ENDPOINTS)
async def test_admin_reads_require_auth(client, path):
    assert (await client.get(path)).status_code == 401


@pytest.mark.parametrize("path", GET_ENDPOINTS)
async def test_admin_reads_forbid_regular_users(client, users, path):
    user = await users.create()
    assert (await client.get(path, headers=users.auth(user))).status_code == 403


async def test_admin_reads_admit_admin(client, users):
    admin = await users.create(role="admin")
    for path in GET_ENDPOINTS:
        assert (await client.get(path, headers=users.auth(admin))).status_code == 200


async def test_role_change_is_owner_only(client, users):
    admin = await users.create(username="justadmin", role="admin")
    target = await users.create(username="target")
    resp = await client.patch(
        f"/api/admin/users/{target.id}/role", json={"role": "admin"}, headers=users.auth(admin)
    )
    assert resp.status_code == 403


async def test_status_change_forbids_regular_users(client, users):
    user = await users.create(username="pleb")
    target = await users.create(username="target")
    resp = await client.patch(
        f"/api/admin/users/{target.id}/status",
        json={"status": "suspended"},
        headers=users.auth(user),
    )
    assert resp.status_code == 403


# --- Overview ---


async def test_overview_counts(client, users, data, session):
    admin = await users.create(username="boss", role="admin")
    reader = await users.create(username="reader")
    await users.create(username="benched", status="suspended")
    feed = await data.feed()
    await data.subscribe(reader, feed)
    art = await data.article(feed, summary_generated_at=datetime.now(UTC))
    session.add(UserActivityDay(user_id=reader.id, day=TODAY))
    session.add(_llm_row(reader.id, billing="user", tokens=(10, 5)))
    session.add(_llm_row(None, billing="system", tokens=(100, 50), status="error"))
    session.add(
        ArticleProcessingEvent(article_id=art.id, stage="summarize", outcome="skipped", detail="x")
    )
    session.add(ArticleProcessingEvent(article_id=art.id, stage="enrich", outcome="failed"))
    await session.commit()

    body = (await client.get("/api/admin/overview", headers=users.auth(admin))).json()
    assert body["users_total"] == 3
    assert body["users_new_7d"] == 3
    assert body["users_suspended"] == 1
    # 2, not 1: the admin's own authenticated request records an activity
    # day too (user_activity in the auth path).
    assert body["active_today"] == 2
    assert body["active_7d"] == 2
    assert body["active_30d"] == 2
    assert body["subscriptions_total"] == 1
    assert body["articles_total"] == 1
    assert body["articles_ingested_24h"] == 1
    assert body["articles_summarized_24h"] == 1
    assert body["articles_skipped_24h"] == 1
    assert body["articles_failed_24h"] == 1
    assert body["llm_calls_7d"] == 2
    assert body["llm_tokens_7d"] == 165
    assert body["llm_errors_7d"] == 1
    assert body["llm_tokens_7d_user"] == 15
    assert body["llm_tokens_7d_system"] == 150


# --- Trends ---


async def test_trends_daily_series_and_breakdowns(client, users, data, session):
    admin = await users.create(username="boss", role="owner")
    reader = await users.create(username="reader")
    feed = await data.feed()
    await data.subscribe(reader, feed)
    art = await data.article(feed, summary_generated_at=datetime.now(UTC))
    await data.state(reader, art, is_read=True, read_at=datetime.now(UTC))
    session.add(UserActivityDay(user_id=reader.id, day=TODAY))
    session.add(
        ReadingActivity(user_id=reader.id, article_id=art.id, day=TODAY, source="web", seconds=120)
    )
    session.add(_llm_row(reader.id, billing="user", tokens=(10, 5)))
    session.add(_llm_row(None, billing="system", tokens=(100, 50), status="error", feature="ner"))
    session.add(
        ArticleProcessingEvent(article_id=art.id, stage="summarize", outcome="skipped", detail="x")
    )
    await session.commit()

    resp = await client.get("/api/admin/trends?range=week", headers=users.auth(admin))
    body = resp.json()
    assert body["range"] == "week"
    assert len(body["days"]) == 7
    today_row = body["days"][-1]
    assert today_row["day"] == TODAY.isoformat()
    assert today_row["new_users"] == 2
    assert today_row["active_users"] == 2  # reader + the admin's own request
    assert today_row["new_subscriptions"] == 1
    assert today_row["articles_ingested"] == 1
    assert today_row["articles_summarized"] == 1
    assert today_row["articles_skipped"] == 1
    assert today_row["articles_failed"] == 0
    assert today_row["articles_read"] == 1
    assert today_row["reading_seconds"] == 120
    assert today_row["llm_calls"] == 2
    assert today_row["llm_tokens"] == 165
    assert today_row["llm_errors"] == 1
    # Empty days stay zeroed, not missing.
    assert body["days"][0]["llm_calls"] == 0

    assert body["llm_tokens_user"] == 15
    assert body["llm_tokens_system"] == 150
    assert {f["feature"] for f in body["llm_by_feature"]} == {"summary", "ner"}
    assert body["llm_by_model"][0]["tokens"] == 165


async def test_trends_rejects_unknown_range(client, users):
    admin = await users.create(role="admin")
    resp = await client.get("/api/admin/trends?range=decade", headers=users.auth(admin))
    assert resp.status_code == 422


# --- User list ---


async def test_users_list_aggregates_and_privacy(client, users, data, session):
    admin = await users.create(username="boss", role="admin")
    reader = await users.create(username="reader", email="reader@example.com")
    feed = await data.feed()
    await data.subscribe(reader, feed)
    art = await data.article(feed)
    await data.state(reader, art, is_read=True, read_at=datetime.now(UTC))
    session.add(UserActivityDay(user_id=reader.id, day=TODAY))
    session.add(
        ReadingActivity(user_id=reader.id, article_id=art.id, day=TODAY, source="web", seconds=90)
    )
    session.add(_llm_row(reader.id, billing="user", tokens=(10, 5)))
    session.add(_llm_row(reader.id, billing="system", tokens=(20, 10)))
    await session.commit()

    body = (await client.get("/api/admin/users", headers=users.auth(admin))).json()
    assert body["total"] == 2
    by_name = {u["username"]: u for u in body["users"]}
    row = by_name["reader"]
    assert row["subscription_count"] == 1
    assert row["articles_read"] == 1
    assert row["reading_seconds"] == 90
    assert row["llm_tokens"] == 45
    assert row["llm_tokens_system"] == 30
    assert row["last_active_day"] == TODAY.isoformat()
    # Privacy allowlist: exactly these fields, nothing else ever.
    assert set(row) == {
        "id",
        "email",
        "username",
        "name",
        "role",
        "status",
        "created_at",
        "tier_key",
        "tier_name",
        "tier_assigned",
        "quota_allowance",
        "quota_used",
        "last_active_day",
        "subscription_count",
        "articles_read",
        "reading_seconds",
        "llm_tokens",
        "llm_tokens_system",
    }


async def test_users_list_filters_sort_and_pagination(client, users):
    admin = await users.create(username="boss", role="admin")
    await users.create(username="alpha", email="alpha@example.com")
    await users.create(username="zeta", email="zeta@example.com", status="suspended")
    headers = users.auth(admin)

    q = (await client.get("/api/admin/users?query=zeta@", headers=headers)).json()
    assert [u["username"] for u in q["users"]] == ["zeta"]

    suspended = (await client.get("/api/admin/users?status=suspended", headers=headers)).json()
    assert [u["username"] for u in suspended["users"]] == ["zeta"]

    admins = (await client.get("/api/admin/users?role=admin", headers=headers)).json()
    assert [u["username"] for u in admins["users"]] == ["boss"]

    sorted_page = (
        await client.get("/api/admin/users?sort=username&limit=2&offset=1", headers=headers)
    ).json()
    assert sorted_page["total"] == 3
    assert [u["username"] for u in sorted_page["users"]] == ["boss", "zeta"]

    assert (await client.get("/api/admin/users?limit=101", headers=headers)).status_code == 422


async def test_user_detail_and_404(client, users):
    admin = await users.create(role="admin")
    other = await users.create(username="somebody")
    detail = await client.get(f"/api/admin/users/{other.id}", headers=users.auth(admin))
    assert detail.status_code == 200
    assert detail.json()["username"] == "somebody"
    assert (
        await client.get("/api/admin/users/99999", headers=users.auth(admin))
    ).status_code == 404


# --- Role changes ---


async def test_owner_promotes_and_audit_logged(client, users, session):
    owner = await users.create(username="root", role="owner")
    target = await users.create(username="rising")
    resp = await client.patch(
        f"/api/admin/users/{target.id}/role", json={"role": "admin"}, headers=users.auth(owner)
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"
    entry = (await session.scalars(select(AdminAuditLog))).one()
    assert (entry.actor_id, entry.target_user_id, entry.action) == (
        owner.id,
        target.id,
        "role_change",
    )
    assert entry.payload == {"from": "user", "to": "admin"}


async def test_role_noop_writes_no_audit(client, users, session):
    owner = await users.create(username="root", role="owner")
    target = await users.create(username="steady")
    resp = await client.patch(
        f"/api/admin/users/{target.id}/role", json={"role": "user"}, headers=users.auth(owner)
    )
    assert resp.status_code == 200
    assert (await session.scalars(select(AdminAuditLog))).all() == []


async def test_final_owner_demotion_conflicts(client, users):
    owner = await users.create(username="solo", role="owner")
    resp = await client.patch(
        f"/api/admin/users/{owner.id}/role", json={"role": "user"}, headers=users.auth(owner)
    )
    assert resp.status_code == 409
    assert "owner" in resp.json()["detail"]


async def test_role_change_unknown_target_404(client, users):
    owner = await users.create(role="owner")
    resp = await client.patch(
        "/api/admin/users/99999/role", json={"role": "admin"}, headers=users.auth(owner)
    )
    assert resp.status_code == 404


async def test_role_change_rejects_unknown_role(client, users):
    owner = await users.create(role="owner")
    target = await users.create(username="t")
    resp = await client.patch(
        f"/api/admin/users/{target.id}/role", json={"role": "root"}, headers=users.auth(owner)
    )
    assert resp.status_code == 422


# --- Status changes ---


async def test_admin_suspends_user_and_it_takes_effect(client, users, session):
    admin = await users.create(username="boss", role="admin")
    target = await users.create(username="mallory")
    target_headers = users.auth(target)
    assert (await client.get("/api/auth/me", headers=target_headers)).status_code == 200

    resp = await client.patch(
        f"/api/admin/users/{target.id}/status",
        json={"status": "suspended"},
        headers=users.auth(admin),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "suspended"
    assert (await client.get("/api/auth/me", headers=target_headers)).status_code == 403

    entry = (await session.scalars(select(AdminAuditLog))).one()
    assert entry.action == "status_change"
    assert entry.payload == {"from": "active", "to": "suspended"}

    # And back: reactivation restores access.
    resp = await client.patch(
        f"/api/admin/users/{target.id}/status",
        json={"status": "active"},
        headers=users.auth(admin),
    )
    assert resp.status_code == 200
    assert (await client.get("/api/auth/me", headers=target_headers)).status_code == 200


async def test_admin_cannot_touch_admin_status(client, users):
    admin = await users.create(username="one", role="admin")
    peer = await users.create(username="two", role="admin")
    resp = await client.patch(
        f"/api/admin/users/{peer.id}/status",
        json={"status": "suspended"},
        headers=users.auth(admin),
    )
    assert resp.status_code == 403
    assert "Owner" in resp.json()["detail"]


async def test_owner_can_suspend_admin(client, users):
    owner = await users.create(username="root", role="owner")
    admin = await users.create(username="deputy", role="admin")
    resp = await client.patch(
        f"/api/admin/users/{admin.id}/status",
        json={"status": "suspended"},
        headers=users.auth(owner),
    )
    assert resp.status_code == 200


async def test_cannot_suspend_yourself(client, users):
    admin = await users.create(username="boss", role="admin")
    resp = await client.patch(
        f"/api/admin/users/{admin.id}/status",
        json={"status": "suspended"},
        headers=users.auth(admin),
    )
    assert resp.status_code == 409
    assert "your own" in resp.json()["detail"]


# --- Tier management ---


async def test_admin_assigns_tier_and_audit_logged(client, users, session):
    from app.models import Tier

    session.add(Tier(key="paid", name="Paid", price_cents=500, monthly_article_allowance=1000))
    await session.commit()
    admin = await users.create(username="boss", role="admin")
    target = await users.create(username="customer")
    headers = users.auth(admin)

    resp = await client.patch(
        f"/api/admin/users/{target.id}/tier", json={"tier": "paid"}, headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["tier_key"], body["tier_assigned"], body["quota_allowance"]) == (
        "paid",
        True,
        1000,
    )

    entry = (await session.scalars(select(AdminAuditLog))).one()
    assert entry.action == "tier_change"
    assert entry.payload == {"from": "default", "to": "paid"}

    # The list filters by effective tier.
    filtered = (await client.get("/api/admin/users?tier=paid", headers=headers)).json()
    assert [u["username"] for u in filtered["users"]] == ["customer"]

    # Revert to the instance default; audited with the sentinel.
    resp = await client.patch(
        f"/api/admin/users/{target.id}/tier", json={"tier": None}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["tier_assigned"] is False
    entries = (await session.scalars(select(AdminAuditLog).order_by(AdminAuditLog.id))).all()
    assert entries[-1].payload == {"from": "paid", "to": "default"}


async def test_tier_change_rejects_unknown_tier_and_regular_users(client, users):
    admin = await users.create(username="boss", role="admin")
    target = await users.create(username="customer")
    resp = await client.patch(
        f"/api/admin/users/{target.id}/tier", json={"tier": "platinum"}, headers=users.auth(admin)
    )
    assert resp.status_code == 422
    resp = await client.patch(
        f"/api/admin/users/{admin.id}/tier", json={"tier": "paid"}, headers=users.auth(target)
    )
    assert resp.status_code == 403
    unknown_filter = await client.get("/api/admin/users?tier=platinum", headers=users.auth(admin))
    assert unknown_filter.status_code == 422
