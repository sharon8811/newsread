async def test_update_me_sets_default_view(client, users):
    user = await users.create(default_view="list")
    resp = await client.patch("/api/users/me", json={"default_view": "zen"},
                              headers=users.auth(user))
    assert resp.status_code == 200
    assert resp.json()["default_view"] == "zen"


async def test_update_me_none_leaves_unchanged(client, users):
    user = await users.create(default_view="stories")
    resp = await client.patch("/api/users/me", json={}, headers=users.auth(user))
    assert resp.status_code == 200
    assert resp.json()["default_view"] == "stories"


async def test_update_me_invalid_view(client, users):
    user = await users.create()
    resp = await client.patch("/api/users/me", json={"default_view": "bogus"},
                              headers=users.auth(user))
    assert resp.status_code == 422


async def test_update_me_requires_auth(client):
    resp = await client.patch("/api/users/me", json={"default_view": "zen"})
    assert resp.status_code == 401


async def test_search_users(client, users):
    me = await users.create(username="me")
    await users.create(username="alice", name="Alice Smith")
    await users.create(username="alicia", name="Alicia Jones")
    await users.create(username="bob", name="Bob")

    resp = await client.get("/api/users/search", params={"q": "alic"}, headers=users.auth(me))
    assert resp.status_code == 200
    names = {u["username"] for u in resp.json()}
    assert names == {"alice", "alicia"}


async def test_search_users_matches_name(client, users):
    me = await users.create(username="searcher")
    await users.create(username="xyz", name="Zebra Person")
    resp = await client.get("/api/users/search", params={"q": "zebra"}, headers=users.auth(me))
    assert [u["username"] for u in resp.json()] == ["xyz"]


async def test_search_users_excludes_self(client, users):
    me = await users.create(username="selfmatch", name="selfmatch")
    resp = await client.get("/api/users/search", params={"q": "selfmatch"}, headers=users.auth(me))
    assert resp.json() == []


async def test_search_users_requires_query(client, users):
    me = await users.create()
    resp = await client.get("/api/users/search", params={"q": ""}, headers=users.auth(me))
    assert resp.status_code == 422
