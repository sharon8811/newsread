from app.models import Share, ShareRecipient


async def _sharable(users, data):
    """A sender subscribed to a feed with one article, plus two recipients."""
    sender = await users.create(username="sender")
    r1 = await users.create(username="rin")
    r2 = await users.create(username="rob")
    feed = await data.feed()
    await data.subscribe(sender, feed)
    art = await data.article(feed, title="Shared Article")
    return sender, r1, r2, feed, art


async def test_create_share(client, users, data):
    sender, r1, r2, feed, art = await _sharable(users, data)
    resp = await client.post("/api/shares", json={
        "article_id": art.id, "recipients": ["rin", "@rob"], "note": "  read this  ",
    }, headers=users.auth(sender))
    assert resp.status_code == 201
    body = resp.json()
    assert body["note"] == "read this"
    assert {u["username"] for u in body["to_users"]} == {"rin", "rob"}
    assert body["from_user"]["username"] == "sender"


async def test_create_share_empty_note_becomes_null(client, users, data):
    sender, r1, r2, feed, art = await _sharable(users, data)
    resp = await client.post("/api/shares", json={
        "article_id": art.id, "recipients": ["rin"], "note": "   ",
    }, headers=users.auth(sender))
    assert resp.json()["note"] is None


async def test_create_share_article_not_found(client, users, data):
    sender = await users.create(username="s")
    await users.create(username="rin")
    resp = await client.post("/api/shares", json={
        "article_id": 99999, "recipients": ["rin"],
    }, headers=users.auth(sender))
    assert resp.status_code == 404


async def test_create_share_no_access_to_article(client, users, data):
    sender = await users.create(username="s")
    await users.create(username="rin")
    feed = await data.feed()  # sender NOT subscribed
    art = await data.article(feed)
    resp = await client.post("/api/shares", json={
        "article_id": art.id, "recipients": ["rin"],
    }, headers=users.auth(sender))
    assert resp.status_code == 404


async def test_create_share_only_self_recipient(client, users, data):
    sender, r1, r2, feed, art = await _sharable(users, data)
    resp = await client.post("/api/shares", json={
        "article_id": art.id, "recipients": ["sender", "@sender"],
    }, headers=users.auth(sender))
    assert resp.status_code == 422


async def test_create_share_unknown_recipient(client, users, data):
    sender, r1, r2, feed, art = await _sharable(users, data)
    resp = await client.post("/api/shares", json={
        "article_id": art.id, "recipients": ["rin", "ghost"],
    }, headers=users.auth(sender))
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]


async def test_create_share_validation_no_recipients(client, users, data):
    sender, r1, r2, feed, art = await _sharable(users, data)
    resp = await client.post("/api/shares", json={
        "article_id": art.id, "recipients": [],
    }, headers=users.auth(sender))
    assert resp.status_code == 422


async def test_received_shares(client, users, data, session):
    sender, r1, r2, feed, art = await _sharable(users, data)
    share = Share(from_user_id=sender.id, article_id=art.id, note="hi")
    share.recipients = [ShareRecipient(to_user_id=r1.id)]
    session.add(share)
    await session.commit()
    resp = await client.get("/api/shares/received", headers=users.auth(r1))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["seen_at"] is None
    assert body[0]["article"]["title"] == "Shared Article"


async def test_received_shares_with_state(client, users, data, session):
    sender, r1, r2, feed, art = await _sharable(users, data)
    # r1 must be able to access via the share; mark saved state
    await data.state(r1, art, is_saved=True)
    share = Share(from_user_id=sender.id, article_id=art.id)
    share.recipients = [ShareRecipient(to_user_id=r1.id)]
    session.add(share)
    await session.commit()
    resp = await client.get("/api/shares/received", headers=users.auth(r1))
    assert resp.json()[0]["article"]["is_saved"] is True


async def test_received_shares_empty(client, users):
    user = await users.create()
    resp = await client.get("/api/shares/received", headers=users.auth(user))
    assert resp.json() == []


async def test_sent_shares_empty(client, users):
    user = await users.create()
    resp = await client.get("/api/shares/sent", headers=users.auth(user))
    assert resp.json() == []


async def test_sent_shares(client, users, data, session):
    sender, r1, r2, feed, art = await _sharable(users, data)
    share = Share(from_user_id=sender.id, article_id=art.id)
    share.recipients = [ShareRecipient(to_user_id=r1.id), ShareRecipient(to_user_id=r2.id)]
    session.add(share)
    await session.commit()
    resp = await client.get("/api/shares/sent", headers=users.auth(sender))
    assert len(resp.json()) == 1
    assert len(resp.json()[0]["to_users"]) == 2


async def test_mark_seen(client, users, data, session):
    sender, r1, r2, feed, art = await _sharable(users, data)
    share = Share(from_user_id=sender.id, article_id=art.id)
    recipient = ShareRecipient(to_user_id=r1.id)
    share.recipients = [recipient]
    session.add(share)
    await session.commit()
    await session.refresh(share)

    resp = await client.post(f"/api/shares/{share.id}/seen", headers=users.auth(r1))
    assert resp.status_code == 204
    await session.refresh(recipient)
    assert recipient.seen_at is not None


async def test_mark_seen_idempotent(client, users, data, session):
    sender, r1, r2, feed, art = await _sharable(users, data)
    share = Share(from_user_id=sender.id, article_id=art.id)
    share.recipients = [ShareRecipient(to_user_id=r1.id)]
    session.add(share)
    await session.commit()
    await session.refresh(share)
    await client.post(f"/api/shares/{share.id}/seen", headers=users.auth(r1))
    # Second call must not error even though already seen.
    resp = await client.post(f"/api/shares/{share.id}/seen", headers=users.auth(r1))
    assert resp.status_code == 204


async def test_mark_seen_not_recipient(client, users, data, session):
    sender, r1, r2, feed, art = await _sharable(users, data)
    share = Share(from_user_id=sender.id, article_id=art.id)
    share.recipients = [ShareRecipient(to_user_id=r1.id)]
    session.add(share)
    await session.commit()
    await session.refresh(share)
    resp = await client.post(f"/api/shares/{share.id}/seen", headers=users.auth(r2))
    assert resp.status_code == 404


async def test_unseen_count(client, users, data, session):
    sender, r1, r2, feed, art = await _sharable(users, data)
    art2 = await data.article(feed, title="Second")
    for a in (art, art2):
        share = Share(from_user_id=sender.id, article_id=a.id)
        share.recipients = [ShareRecipient(to_user_id=r1.id)]
        session.add(share)
    await session.commit()
    resp = await client.get("/api/shares/unseen-count", headers=users.auth(r1))
    assert resp.json()["count"] == 2


async def test_unseen_count_zero(client, users, data):
    sender = await users.create(username="s")
    resp = await client.get("/api/shares/unseen-count", headers=users.auth(sender))
    assert resp.json()["count"] == 0


async def test_create_share_enqueues_push_job(client, users, data, monkeypatch):
    sender = await users.create(username="pusher")
    recipient = await users.create(username="pushee")
    feed = await data.feed()
    await data.subscribe(sender, feed)
    article = await data.article(feed)

    jobs = []

    async def record(job_name, *args):
        jobs.append((job_name, args))

    monkeypatch.setattr("app.routers.shares.enqueue", record)
    resp = await client.post(
        "/api/shares",
        json={"article_id": article.id, "recipients": ["pushee"]},
        headers=users.auth(sender),
    )
    assert resp.status_code == 201
    assert jobs == [("send_share_push", (resp.json()["id"],))]
