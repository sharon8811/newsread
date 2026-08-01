"""Tiers and monthly article allowances (#119): charge-once semantics,
row-locked concurrency, resets, tier changes, refunds, worker gating, and
the read-only quota endpoint."""

import asyncio
from datetime import date

import pytest
from sqlalchemy import select

from app import db as app_db
from app import llm, quota
from app.models import Article, Tier, User, UserArticleCharge, UserQuotaPeriod
from app.routers import ai as ai_router
from app.summarizer import SummarySkipped


async def _tier(session, *, key="free", name="Free", allowance=2, price=0) -> Tier:
    tier = Tier(key=key, name=name, price_cents=price, monthly_article_allowance=allowance)
    session.add(tier)
    await session.commit()
    await session.refresh(tier)
    return tier


async def _used(session, user) -> int:
    return (
        await session.scalar(
            select(UserQuotaPeriod.used).where(
                UserQuotaPeriod.user_id == user.id,
                UserQuotaPeriod.period == quota.current_period(),
            )
        )
    ) or 0


# --- try_charge ---


async def test_charge_counts_up_to_allowance_then_denies(session, users, data):
    tier = await _tier(session, allowance=2)
    user = await users.create()
    user.tier_id = tier.id
    await session.commit()
    feed = await data.feed()
    articles = [await data.article(feed) for _ in range(3)]

    assert (await quota.try_charge(session, user, articles[0].id)).charged
    await session.commit()
    assert (await quota.try_charge(session, user, articles[1].id)).charged
    await session.commit()
    denied = await quota.try_charge(session, user, articles[2].id)
    await session.commit()
    assert denied == quota.DENIED
    assert await _used(session, user) == 2

    row = await session.get(UserQuotaPeriod, (user.id, quota.current_period()))
    assert (row.tier_key_snapshot, row.allowance_snapshot) == ("free", 2)


async def test_same_article_never_double_charges(session, users, data):
    tier = await _tier(session, allowance=2)
    user = await users.create()
    user.tier_id = tier.id
    await session.commit()
    art = await data.article(await data.feed())

    assert (await quota.try_charge(session, user, art.id)).charged
    await session.commit()
    again = await quota.try_charge(session, user, art.id)
    assert again == quota.FREE
    assert await _used(session, user) == 1


async def test_refund_returns_the_reservation(session, users, data):
    tier = await _tier(session, allowance=1)
    user = await users.create()
    user.tier_id = tier.id
    await session.commit()
    feed = await data.feed()
    art, other = await data.article(feed), await data.article(feed)

    assert (await quota.try_charge(session, user, art.id)).charged
    await session.commit()
    assert not (await quota.try_charge(session, user, other.id)).allowed
    await session.commit()

    await quota.refund(session, user, art.id)
    assert await _used(session, user) == 0
    assert (await session.scalars(select(UserArticleCharge))).all() == []
    # Refunding something never charged is a safe no-op.
    await quota.refund(session, user, art.id)
    # And the freed unit is usable again.
    assert (await quota.try_charge(session, user, other.id)).charged
    await session.commit()


@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_admin_roles_recorded_but_never_enforced(session, users, data, role):
    tier = await _tier(session, allowance=0)
    boss = await users.create(username=f"boss_{role}", role=role)
    boss.tier_id = tier.id
    await session.commit()
    art = await data.article(await data.feed())
    result = await quota.try_charge(session, boss, art.id)
    await session.commit()
    assert result.charged  # usage stays queryable, access never blocked


async def test_unassigned_user_rides_instance_default(session, users, data, monkeypatch):
    # Tests run self_hosted: default_tier is 'unlimited'; with no tier rows
    # seeded the default resolves to None, which must read as unlimited.
    user = await users.create()
    art = await data.article(await data.feed())
    assert (await quota.try_charge(session, user, art.id)).charged
    await session.commit()

    # A hosted instance defaults new users to 'free'.
    free = await _tier(session, allowance=0)
    monkeypatch.setattr(quota.settings, "default_tier", "free")
    other = await data.article(await data.feed(url="https://two.example/rss"))
    assert not (await quota.try_charge(session, user, other.id)).allowed
    assert free.key == "free"


async def test_monthly_reset_and_history(session, users, data, monkeypatch):
    tier = await _tier(session, allowance=1)
    user = await users.create()
    user.tier_id = tier.id
    await session.commit()
    feed = await data.feed()
    art, later = await data.article(feed), await data.article(feed)

    assert (await quota.try_charge(session, user, art.id)).charged
    await session.commit()
    assert not (await quota.try_charge(session, user, later.id)).allowed
    await session.commit()

    this_month = quota.current_period()
    monkeypatch.setattr(quota, "current_period", lambda now=None: quota.next_reset(this_month))
    assert (await quota.try_charge(session, user, later.id)).charged
    await session.commit()

    # Historic month stays queryable, snapshot intact.
    old = await session.get(UserQuotaPeriod, (user.id, this_month))
    assert (old.used, old.allowance_snapshot) == (1, 1)


async def test_tier_change_mid_month(session, users, data):
    small = await _tier(session, key="free", allowance=1)
    big = await _tier(session, key="paid", name="Paid", allowance=10, price=500)
    user = await users.create()
    user.tier_id = small.id
    await session.commit()
    feed = await data.feed()
    first, second = await data.article(feed), await data.article(feed)

    assert (await quota.try_charge(session, user, first.id)).charged
    await session.commit()
    assert not (await quota.try_charge(session, user, second.id)).allowed
    await session.commit()

    user.tier_id = big.id  # admin upgrade takes effect immediately
    await session.commit()
    assert (await quota.try_charge(session, user, second.id)).charged
    await session.commit()
    row = await session.get(UserQuotaPeriod, (user.id, quota.current_period()))
    assert (row.used, row.tier_key_snapshot, row.allowance_snapshot) == (2, "paid", 10)

    # Downgrading below current usage stops new charges, claws nothing back.
    user.tier_id = small.id
    await session.commit()
    third = await data.article(feed)
    assert not (await quota.try_charge(session, user, third.id)).allowed
    await session.commit()
    assert await _used(session, user) == 2


async def test_concurrent_charges_cannot_exceed_allowance(session, users, data):
    tier = await _tier(session, allowance=1)
    user = await users.create()
    user.tier_id = tier.id
    await session.commit()
    feed = await data.feed()
    ids = [(await data.article(feed)).id for _ in range(2)]

    async def one(article_id: int) -> bool:
        async with app_db.SessionLocal() as own:
            target = await own.get(User, user.id)
            result = await quota.try_charge(own, target, article_id)
            await own.commit()
            return result.allowed

    results = sorted(await asyncio.gather(one(ids[0]), one(ids[1])))
    assert results == [False, True]
    assert await _used(session, user) == 1


async def test_concurrent_same_article_charges_once(session, users, data):
    tier = await _tier(session, allowance=5)
    user = await users.create()
    user.tier_id = tier.id
    await session.commit()
    art = await data.article(await data.feed())

    async def one() -> bool:
        async with app_db.SessionLocal() as own:
            target = await own.get(User, user.id)
            result = await quota.try_charge(own, target, art.id)
            await own.commit()
            return result.allowed

    assert all(await asyncio.gather(one(), one()))
    assert await _used(session, user) == 1


def test_next_reset_year_rollover():
    assert quota.next_reset(date(2026, 12, 1)) == date(2027, 1, 1)
    assert quota.next_reset(date(2026, 8, 1)) == date(2026, 9, 1)


# --- worker gating: subscriber_with_quota_exists + charge_subscribers ---


async def _eligible_article_ids(session):
    default = await quota.default_tier(session)
    return list(
        await session.scalars(
            select(Article.id).where(
                quota.subscriber_with_quota_exists(default, quota.current_period())
            )
        )
    )


async def test_worker_skips_articles_nobody_can_pay_for(session, users, data, monkeypatch):
    monkeypatch.setattr(quota.settings, "default_tier", "free")
    free = await _tier(session, allowance=1)
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    art = await data.article(feed)

    assert await _eligible_article_ids(session) == [art.id]

    # Exhaust the (default-tier) allowance: the whole feed drops out (the
    # EXISTS asks who can still pay, not which article).
    assert (await quota.try_charge(session, user, art.id)).charged
    await session.commit()
    other = await data.article(feed)
    assert await _eligible_article_ids(session) == []

    # An exempt (admin) subscriber makes the feed payable again.
    boss = await users.create(username="boss", role="admin")
    await data.subscribe(boss, feed)
    assert set(await _eligible_article_ids(session)) == {art.id, other.id}
    assert free.key == "free"


async def test_worker_excludes_suspended_subscribers(session, users, data, monkeypatch):
    monkeypatch.setattr(quota.settings, "default_tier", "free")
    await _tier(session, allowance=1)
    user = await users.create(status="suspended")
    feed = await data.feed()
    await data.subscribe(user, feed)
    await data.article(feed)
    assert await _eligible_article_ids(session) == []


async def test_charge_subscribers_charges_payers_only(session, users, data, monkeypatch):
    monkeypatch.setattr(quota.settings, "default_tier", "free")
    await _tier(session, allowance=1)
    payer = await users.create(username="payer")
    broke = await users.create(username="broke")
    feed = await data.feed()
    await data.subscribe(payer, feed)
    await data.subscribe(broke, feed)
    art, earlier = await data.article(feed), await data.article(feed)

    # `broke` already spent their allowance elsewhere.
    assert (await quota.try_charge(session, broke, earlier.id)).charged
    await session.commit()

    assert await quota.charge_subscribers(session, art) == 1
    charged_users = set(
        await session.scalars(
            select(UserArticleCharge.user_id).where(UserArticleCharge.article_id == art.id)
        )
    )
    assert charged_users == {payer.id}
    # Idempotent: a rerun (duplicate job) records nothing new.
    assert await quota.charge_subscribers(session, art) == 0


# --- the read-only user endpoint ---


async def test_quota_endpoint_shape(client, users, session, data):
    tier = await _tier(session, allowance=5)
    user = await users.create()
    user.tier_id = tier.id
    await session.commit()
    art = await data.article(await data.feed())
    assert (await quota.try_charge(session, user, art.id)).charged
    await session.commit()

    body = (await client.get("/api/users/me/quota", headers=users.auth(user))).json()
    assert body["tier_key"] == "free"
    assert body["allowance"] == 5
    assert body["used"] == 1
    assert body["exempt"] is False
    assert body["period_start"] == quota.current_period().isoformat()
    assert body["resets_on"] == quota.next_reset(quota.current_period()).isoformat()


async def test_quota_endpoint_marks_admins_exempt(client, users):
    admin = await users.create(role="admin")
    body = (await client.get("/api/users/me/quota", headers=users.auth(admin))).json()
    assert body["exempt"] is True
    assert body["tier_key"] == "unlimited"  # no seed rows: default reads unlimited


# --- on-demand endpoint enforcement ---


async def _ai_setup(users, data, session, *, allowance):
    tier = await _tier(session, allowance=allowance)
    user = await users.create()
    user.tier_id = tier.id
    await session.commit()
    feed = await data.feed()
    await data.subscribe(user, feed)
    art = await data.article(feed)
    return user, feed, art


async def test_summarize_402_when_exhausted(client, users, data, session, monkeypatch):
    user, feed, art = await _ai_setup(users, data, session, allowance=0)
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    resp = await client.post(f"/api/articles/{art.id}/summarize", headers=users.auth(user))
    assert resp.status_code == 402
    assert "allowance" in resp.json()["detail"]

    stream = await client.post(f"/api/articles/{art.id}/summarize/stream", headers=users.auth(user))
    assert stream.status_code == 402


async def test_summarize_charges_once_and_regenerates_free(
    client, users, data, session, monkeypatch
):
    user, feed, art = await _ai_setup(users, data, session, allowance=1)
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    async def fake_generate(session_, article, **kwargs):
        article.summary = "full"
        article.summary_short = "s"
        article.summary_skipped_reason = None
        await session_.commit()

    monkeypatch.setattr(ai_router, "generate_summaries", fake_generate)
    headers = users.auth(user)
    assert (
        await client.post(f"/api/articles/{art.id}/summarize", headers=headers)
    ).status_code == 200
    assert await _used(session, user) == 1

    # Regenerating an already-charged article stays free even at the limit.
    forced = await client.post(f"/api/articles/{art.id}/summarize?force=true", headers=headers)
    assert forced.status_code == 200
    assert await _used(session, user) == 1

    # A different article is over the allowance.
    other = await data.article(feed)
    assert (
        await client.post(f"/api/articles/{other.id}/summarize", headers=headers)
    ).status_code == 402


async def test_summarize_skip_refunds_the_reservation(client, users, data, session, monkeypatch):
    user, feed, art = await _ai_setup(users, data, session, allowance=1)
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    async def skip(session_, article, **kwargs):
        raise SummarySkipped()

    monkeypatch.setattr(ai_router, "generate_summaries", skip)
    resp = await client.post(f"/api/articles/{art.id}/summarize", headers=users.auth(user))
    assert resp.status_code == 200
    # Nothing was delivered: the unit is back and usable elsewhere.
    assert await _used(session, user) == 0
    assert (await session.scalars(select(UserArticleCharge))).all() == []


# --- review follow-ups: reservation ordering, boundary refunds, imports ---


async def test_no_reservation_when_llm_unusable(client, users, data, session):
    # llm.is_configured is False in tests unless patched: the 503 must fire
    # before any allowance is reserved.
    user, feed, art = await _ai_setup(users, data, session, allowance=1)
    resp = await client.post(f"/api/articles/{art.id}/summarize", headers=users.auth(user))
    assert resp.status_code == 503
    assert await _used(session, user) == 0
    assert (await session.scalars(select(UserArticleCharge))).all() == []


async def test_refund_crosses_month_boundary(session, users, data, monkeypatch):
    tier = await _tier(session, allowance=1)
    user = await users.create()
    user.tier_id = tier.id
    await session.commit()
    art = await data.article(await data.feed())

    reservation_month = quota.current_period()
    assert (await quota.try_charge(session, user, art.id)).charged
    await session.commit()

    # The month ticks over between the reservation and the failure.
    monkeypatch.setattr(
        quota, "current_period", lambda now=None: quota.next_reset(reservation_month)
    )
    await quota.refund(session, user, art.id)

    assert (await session.scalars(select(UserArticleCharge))).all() == []
    old = await session.get(UserQuotaPeriod, (user.id, reservation_month))
    assert old.used == 0


async def test_import_fetch_failure_refunds_reservation(session, users, data, monkeypatch):
    from app.routers import imports as imports_router

    tier = await _tier(session, allowance=1)
    user = await users.create()
    user.tier_id = tier.id
    await session.commit()
    feed = await data.feed()
    art = await data.article(feed, full_text="", full_text_fetched_at=None)
    assert (await quota.try_charge(session, user, art.id)).charged
    await session.commit()

    async def explode(url):
        raise RuntimeError("dns failure")

    monkeypatch.setattr(imports_router, "fetch_page", explode)
    await imports_router.process_import(art.id, user.id, object(), refund_on_failure=True)
    assert await _used(session, user) == 0
    assert (await session.scalars(select(UserArticleCharge))).all() == []


async def test_import_never_refunds_a_preexisting_charge(session, users, data, monkeypatch):
    # refund_on_failure=False marks "this import reserved nothing": a charge
    # from an earlier operation must survive any import failure.
    from app.routers import imports as imports_router

    tier = await _tier(session, allowance=1)
    user = await users.create()
    user.tier_id = tier.id
    await session.commit()
    art = await data.article(await data.feed(), full_text="", full_text_fetched_at=None)
    assert (await quota.try_charge(session, user, art.id)).charged
    await session.commit()

    async def explode(url):
        raise RuntimeError("dns failure")

    monkeypatch.setattr(imports_router, "fetch_page", explode)
    await imports_router.process_import(art.id, user.id, object(), refund_on_failure=False)
    assert await _used(session, user) == 1
