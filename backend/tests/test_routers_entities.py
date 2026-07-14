from datetime import UTC

from app.models import ArticleEntity, Entity


async def _entity(
    session, *, kind="person", key="peter thiel", name="Peter Thiel", url="", data=None
):
    entity = Entity(
        kind=kind, canonical_key=key, url=url, data=data if data is not None else {"name": name}
    )
    session.add(entity)
    await session.commit()
    await session.refresh(entity)
    return entity


async def _link(session, article, entity):
    session.add(ArticleEntity(article_id=article.id, entity_id=entity.id, source="ner"))
    await session.commit()


async def test_entity_not_found(client, users):
    user = await users.create()
    resp = await client.get("/api/entities/99999", headers=users.auth(user))
    assert resp.status_code == 404


async def test_entity_page_scopes_articles(client, users, data, session):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    other_feed = await data.feed(title="Unsubscribed")
    visible_new = await data.article(feed, title="Visible newer")
    visible_old = await data.article(feed, title="Visible older")
    foreign = await data.article(other_feed, title="Not subscribed")
    await data.article(feed, title="Unlinked")

    entity = await _entity(session)
    for art in (visible_new, visible_old, foreign):
        await _link(session, art, entity)
    from datetime import datetime, timedelta

    visible_new.published_at = datetime.now(UTC)
    visible_old.published_at = datetime.now(UTC) - timedelta(days=2)
    await session.commit()

    resp = await client.get(f"/api/entities/{entity.id}", headers=users.auth(user))
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "person"
    assert body["name"] == "Peter Thiel"
    assert body["url"] == ""
    assert [a["title"] for a in body["articles"]] == ["Visible newer", "Visible older"]


async def test_entity_page_name_fallbacks(client, users, data, session):
    user = await users.create()
    # Enricher kind with data -> badge label wins.
    repo = await _entity(
        session,
        kind="github",
        key="acme/x",
        url="https://github.com/acme/x",
        data={"full_name": "acme/x", "stargazers_count": 5},
    )
    resp = await client.get(f"/api/entities/{repo.id}", headers=users.auth(user))
    body = resp.json()
    assert body["name"] == body["badge"]["label"]
    assert body["url"] == "https://github.com/acme/x"
    assert body["articles"] == []
    # No badge, no data.name -> canonical key.
    bare = await _entity(session, kind="org", key="acme corp", data={})
    resp = await client.get(f"/api/entities/{bare.id}", headers=users.auth(user))
    assert resp.json()["name"] == "acme corp"
