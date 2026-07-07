from datetime import datetime, timezone

from sqlalchemy import select

from app.models import Project, ProjectArticle, ProjectMember


async def _project(session, owner, *members, name="Research", description=""):
    project = Project(owner_id=owner.id, name=name, description=description)
    project.members = [ProjectMember(user_id=owner.id, role="owner")] + [
        ProjectMember(user_id=m.id, role="member") for m in members
    ]
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


async def _pin(session, project, article, user, *, is_shared=False, shared_at=None,
               note=None, created_at=None):
    extra = {"created_at": created_at} if created_at else {}
    pin = ProjectArticle(
        project_id=project.id, article_id=article.id, added_by_user_id=user.id,
        is_shared=is_shared, shared_at=shared_at, note=note, **extra,
    )
    session.add(pin)
    await session.commit()
    await session.refresh(pin)
    return pin


async def _team(users, data):
    """An owner and a member, both subscribed to a feed with one article."""
    owner = await users.create(username="olive")
    member = await users.create(username="mia")
    feed = await data.feed()
    await data.subscribe(owner, feed)
    await data.subscribe(member, feed)
    article = await data.article(feed, title="Pinned Article")
    return owner, member, feed, article


# --- project CRUD ---

async def test_create_project(client, users):
    user = await users.create()
    resp = await client.post("/api/projects", json={
        "name": "  AI News  ", "description": " weekly digest ",
    }, headers=users.auth(user))
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "AI News"
    assert body["description"] == "weekly digest"
    assert body["my_role"] == "owner"
    assert body["article_count"] == 0
    assert [m["role"] for m in body["members"]] == ["owner"]
    assert body["owner"]["username"] == user.username


async def test_create_project_name_required(client, users):
    user = await users.create()
    resp = await client.post("/api/projects", json={"name": ""}, headers=users.auth(user))
    assert resp.status_code == 422


async def test_create_project_blank_name_rejected(client, users):
    user = await users.create()
    resp = await client.post("/api/projects", json={"name": "   "}, headers=users.auth(user))
    assert resp.status_code == 422


async def test_update_project_blank_name_rejected(client, users, session):
    owner = await users.create()
    project = await _project(session, owner)
    resp = await client.patch(f"/api/projects/{project.id}", json={"name": " \t "},
                              headers=users.auth(owner))
    assert resp.status_code == 422


async def test_count_deduplicates_articles_pinned_by_two_members(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner, member)
    now = datetime.now(timezone.utc)
    await _pin(session, project, article, owner, is_shared=True, shared_at=now)
    await _pin(session, project, article, member, is_shared=True, shared_at=now)
    resp = await client.get("/api/projects", headers=users.auth(owner))
    assert resp.json()[0]["article_count"] == 1


async def test_list_projects_mine_only_with_roles(client, users, session):
    owner = await users.create(username="olive")
    member = await users.create(username="mia")
    outsider = await users.create(username="oscar")
    await _project(session, owner, member, name="Shared Proj")
    await _project(session, outsider, name="Not Mine")
    resp = await client.get("/api/projects", headers=users.auth(member))
    body = resp.json()
    assert [p["name"] for p in body] == ["Shared Proj"]
    assert body[0]["my_role"] == "member"
    # Owner sorts first in the member list.
    assert [m["role"] for m in body[0]["members"]] == ["owner", "member"]


async def test_list_projects_count_excludes_others_private(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    other_article = await data.article(feed, title="Second")
    project = await _project(session, owner, member)
    await _pin(session, project, article, owner, is_shared=True,
               shared_at=datetime.now(timezone.utc))
    await _pin(session, project, other_article, owner)  # owner's private pin
    resp = await client.get("/api/projects", headers=users.auth(member))
    assert resp.json()[0]["article_count"] == 1
    resp = await client.get("/api/projects", headers=users.auth(owner))
    assert resp.json()[0]["article_count"] == 2


async def test_get_project(client, users, session):
    owner = await users.create()
    project = await _project(session, owner)
    resp = await client.get(f"/api/projects/{project.id}", headers=users.auth(owner))
    assert resp.status_code == 200
    assert resp.json()["name"] == "Research"


async def test_get_project_non_member_404(client, users, session):
    owner = await users.create()
    outsider = await users.create()
    project = await _project(session, owner)
    resp = await client.get(f"/api/projects/{project.id}", headers=users.auth(outsider))
    assert resp.status_code == 404


async def test_update_project(client, users, session):
    owner = await users.create()
    project = await _project(session, owner, description="old")
    resp = await client.patch(f"/api/projects/{project.id}", json={"name": " Renamed "},
                              headers=users.auth(owner))
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"
    assert resp.json()["description"] == "old"  # untouched by partial patch


async def test_update_project_description(client, users, session):
    owner = await users.create()
    project = await _project(session, owner)
    resp = await client.patch(f"/api/projects/{project.id}",
                              json={"description": " focus areas "},
                              headers=users.auth(owner))
    assert resp.json()["description"] == "focus areas"
    assert resp.json()["name"] == "Research"


async def test_update_project_member_forbidden(client, users, session):
    owner = await users.create()
    member = await users.create()
    project = await _project(session, owner, member)
    resp = await client.patch(f"/api/projects/{project.id}", json={"name": "X"},
                              headers=users.auth(member))
    assert resp.status_code == 403


async def test_delete_project_cascades(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner, member)
    await _pin(session, project, article, owner, is_shared=True)
    resp = await client.delete(f"/api/projects/{project.id}", headers=users.auth(owner))
    assert resp.status_code == 204
    assert (await client.get("/api/projects", headers=users.auth(member))).json() == []


async def test_delete_project_member_forbidden(client, users, session):
    owner = await users.create()
    member = await users.create()
    project = await _project(session, owner, member)
    resp = await client.delete(f"/api/projects/{project.id}", headers=users.auth(member))
    assert resp.status_code == 403


# --- membership ---

async def test_add_member(client, users, session):
    owner = await users.create()
    await users.create(username="Newbie")
    project = await _project(session, owner)
    resp = await client.post(f"/api/projects/{project.id}/members",
                             json={"username": "@newbie"}, headers=users.auth(owner))
    assert resp.status_code == 201
    assert {m["user"]["username"] for m in resp.json()["members"]} == {owner.username, "Newbie"}


async def test_add_member_unknown_user(client, users, session):
    owner = await users.create()
    project = await _project(session, owner)
    resp = await client.post(f"/api/projects/{project.id}/members",
                             json={"username": "ghost"}, headers=users.auth(owner))
    assert resp.status_code == 404


async def test_add_member_already_member(client, users, session):
    owner = await users.create()
    member = await users.create(username="mia")
    project = await _project(session, owner, member)
    resp = await client.post(f"/api/projects/{project.id}/members",
                             json={"username": "mia"}, headers=users.auth(owner))
    assert resp.status_code == 409


async def test_add_member_by_member_forbidden(client, users, session):
    owner = await users.create()
    member = await users.create()
    await users.create(username="third")
    project = await _project(session, owner, member)
    resp = await client.post(f"/api/projects/{project.id}/members",
                             json={"username": "third"}, headers=users.auth(member))
    assert resp.status_code == 403


async def test_member_leaves(client, users, session):
    owner = await users.create()
    member = await users.create()
    project = await _project(session, owner, member)
    resp = await client.delete(f"/api/projects/{project.id}/members/{member.id}",
                               headers=users.auth(member))
    assert resp.status_code == 204
    assert (await client.get(f"/api/projects/{project.id}",
                             headers=users.auth(member))).status_code == 404


async def test_departed_member_shared_pins_stay(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner, member)
    await _pin(session, project, article, member, is_shared=True,
               shared_at=datetime.now(timezone.utc))
    await client.delete(f"/api/projects/{project.id}/members/{member.id}",
                        headers=users.auth(member))
    resp = await client.get(f"/api/projects/{project.id}/articles", headers=users.auth(owner))
    assert [p["article"]["title"] for p in resp.json()] == ["Pinned Article"]


async def test_owner_cannot_leave(client, users, session):
    owner = await users.create()
    project = await _project(session, owner)
    resp = await client.delete(f"/api/projects/{project.id}/members/{owner.id}",
                               headers=users.auth(owner))
    assert resp.status_code == 422


async def test_owner_removes_member(client, users, session):
    owner = await users.create()
    member = await users.create()
    project = await _project(session, owner, member)
    resp = await client.delete(f"/api/projects/{project.id}/members/{member.id}",
                               headers=users.auth(owner))
    assert resp.status_code == 204


async def test_member_removes_other_forbidden(client, users, session):
    owner = await users.create()
    m1 = await users.create()
    m2 = await users.create()
    project = await _project(session, owner, m1, m2)
    resp = await client.delete(f"/api/projects/{project.id}/members/{m2.id}",
                               headers=users.auth(m1))
    assert resp.status_code == 403


async def test_owner_removes_nonexistent_member(client, users, session):
    owner = await users.create()
    outsider = await users.create()
    project = await _project(session, owner)
    resp = await client.delete(f"/api/projects/{project.id}/members/{outsider.id}",
                               headers=users.auth(owner))
    assert resp.status_code == 404


# --- pin listing & visibility ---

async def test_list_articles_hides_others_private(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    second = await data.article(feed, title="Private One")
    project = await _project(session, owner, member)
    await _pin(session, project, article, owner, is_shared=True,
               shared_at=datetime.now(timezone.utc))
    await _pin(session, project, second, owner, note="my secret angle")
    mine = await client.get(f"/api/projects/{project.id}/articles", headers=users.auth(owner))
    assert {p["article"]["title"] for p in mine.json()} == {"Pinned Article", "Private One"}
    theirs = await client.get(f"/api/projects/{project.id}/articles", headers=users.auth(member))
    assert {p["article"]["title"] for p in theirs.json()} == {"Pinned Article"}


async def test_list_articles_scopes(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    second = await data.article(feed, title="Mine Private")
    project = await _project(session, owner, member)
    await _pin(session, project, article, member, is_shared=True,
               shared_at=datetime.now(timezone.utc))
    await _pin(session, project, second, owner)
    shared = await client.get(f"/api/projects/{project.id}/articles?scope=shared",
                              headers=users.auth(owner))
    assert [p["article"]["title"] for p in shared.json()] == ["Pinned Article"]
    mine = await client.get(f"/api/projects/{project.id}/articles?scope=mine",
                            headers=users.auth(owner))
    assert [p["article"]["title"] for p in mine.json()] == ["Mine Private"]


async def test_list_articles_orders_by_publish_time(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    old = await data.article(feed, title="Old But Published Today")
    project = await _project(session, owner, member)
    await _pin(session, project, article, owner, is_shared=True,
               shared_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
               created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    # Added long ago, published just now → must surface first.
    await _pin(session, project, old, owner, is_shared=True,
               shared_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
               created_at=datetime(2025, 12, 1, tzinfo=timezone.utc))
    resp = await client.get(f"/api/projects/{project.id}/articles", headers=users.auth(owner))
    assert [p["article"]["title"] for p in resp.json()] == [
        "Old But Published Today", "Pinned Article",
    ]


async def test_list_articles_includes_state_and_meta(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    await data.state(member, article, is_saved=True)
    project = await _project(session, owner, member)
    await _pin(session, project, article, owner, is_shared=True,
               shared_at=datetime.now(timezone.utc), note="worth a read")
    resp = await client.get(f"/api/projects/{project.id}/articles", headers=users.auth(member))
    [pin] = resp.json()
    assert pin["note"] == "worth a read"
    assert pin["added_by"]["username"] == owner.username
    assert pin["article"]["is_saved"] is True
    assert pin["article"]["feed_title"] == "A Feed"


async def test_list_articles_non_member_404(client, users, session):
    owner = await users.create()
    outsider = await users.create()
    project = await _project(session, owner)
    resp = await client.get(f"/api/projects/{project.id}/articles",
                            headers=users.auth(outsider))
    assert resp.status_code == 404


# --- adding pins ---

async def test_add_article_private_default(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner, member)
    resp = await client.post(f"/api/projects/{project.id}/articles",
                             json={"article_id": article.id}, headers=users.auth(member))
    assert resp.status_code == 201
    body = resp.json()
    assert body["is_shared"] is False
    assert body["shared_at"] is None
    assert body["added_by"]["username"] == member.username


async def test_add_article_shared_stamps_shared_at(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner)
    resp = await client.post(
        f"/api/projects/{project.id}/articles",
        json={"article_id": article.id, "is_shared": True, "note": "  hot take  "},
        headers=users.auth(owner),
    )
    body = resp.json()
    assert body["is_shared"] is True
    assert body["shared_at"] is not None
    assert body["note"] == "hot take"


async def test_add_article_empty_note_becomes_null(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner)
    resp = await client.post(f"/api/projects/{project.id}/articles",
                             json={"article_id": article.id, "note": "   "},
                             headers=users.auth(owner))
    assert resp.json()["note"] is None


async def test_add_article_duplicate_by_same_user(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner)
    payload = {"article_id": article.id}
    await client.post(f"/api/projects/{project.id}/articles", json=payload,
                      headers=users.auth(owner))
    resp = await client.post(f"/api/projects/{project.id}/articles", json=payload,
                             headers=users.auth(owner))
    assert resp.status_code == 409


async def test_add_article_same_article_two_users_ok(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner, member)
    payload = {"article_id": article.id}
    r1 = await client.post(f"/api/projects/{project.id}/articles", json=payload,
                           headers=users.auth(owner))
    r2 = await client.post(f"/api/projects/{project.id}/articles", json=payload,
                           headers=users.auth(member))
    assert (r1.status_code, r2.status_code) == (201, 201)


async def test_add_article_no_access(client, users, data, session):
    owner = await users.create()
    feed = await data.feed()  # not subscribed
    article = await data.article(feed)
    project = await _project(session, owner)
    resp = await client.post(f"/api/projects/{project.id}/articles",
                             json={"article_id": article.id}, headers=users.auth(owner))
    assert resp.status_code == 404


async def test_add_article_nonexistent(client, users, session):
    owner = await users.create()
    project = await _project(session, owner)
    resp = await client.post(f"/api/projects/{project.id}/articles",
                             json={"article_id": 99999}, headers=users.auth(owner))
    assert resp.status_code == 404


async def test_add_article_non_member_404(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    outsider = await users.create()
    project = await _project(session, owner)
    resp = await client.post(f"/api/projects/{project.id}/articles",
                             json={"article_id": article.id}, headers=users.auth(outsider))
    assert resp.status_code == 404


async def test_add_article_accessible_via_other_project(client, users, data, session):
    """A shared project pin grants access — the member can re-pin the article
    into their own project without subscribing to the feed."""
    owner, member, feed, article = await _team(users, data)
    stranger = await users.create(username="sasha")  # no subscription at all
    source = await _project(session, owner, stranger, name="Source")
    await _pin(session, source, article, owner, is_shared=True,
               shared_at=datetime.now(timezone.utc))
    own = await _project(session, stranger, name="Sasha's")
    resp = await client.post(f"/api/projects/{own.id}/articles",
                             json={"article_id": article.id}, headers=users.auth(stranger))
    assert resp.status_code == 201


# --- editing pins ---

async def test_publish_pin_sets_shared_at(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner)
    pin = await _pin(session, project, article, owner)
    resp = await client.patch(f"/api/projects/{project.id}/articles/{pin.id}",
                              json={"is_shared": True}, headers=users.auth(owner))
    assert resp.status_code == 200
    assert resp.json()["is_shared"] is True
    assert resp.json()["shared_at"] is not None


async def test_unpublish_pin_clears_shared_at(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner)
    pin = await _pin(session, project, article, owner, is_shared=True,
                     shared_at=datetime.now(timezone.utc))
    resp = await client.patch(f"/api/projects/{project.id}/articles/{pin.id}",
                              json={"is_shared": False}, headers=users.auth(owner))
    assert resp.json()["is_shared"] is False
    assert resp.json()["shared_at"] is None


async def test_republish_keeps_original_shared_at(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner)
    stamp = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pin = await _pin(session, project, article, owner, is_shared=True, shared_at=stamp)
    resp = await client.patch(f"/api/projects/{project.id}/articles/{pin.id}",
                              json={"is_shared": True}, headers=users.auth(owner))
    assert resp.json()["shared_at"] == "2026-06-01T00:00:00Z"


async def test_update_pin_note_and_null_flag_ignored(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner)
    pin = await _pin(session, project, article, owner, is_shared=True,
                     shared_at=datetime.now(timezone.utc), note="old")
    resp = await client.patch(f"/api/projects/{project.id}/articles/{pin.id}",
                              json={"is_shared": None, "note": " new "},
                              headers=users.auth(owner))
    assert resp.json()["is_shared"] is True  # explicit null = unchanged
    assert resp.json()["note"] == "new"


async def test_update_pin_clear_note(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner)
    pin = await _pin(session, project, article, owner, note="old")
    resp = await client.patch(f"/api/projects/{project.id}/articles/{pin.id}",
                              json={"note": None}, headers=users.auth(owner))
    assert resp.json()["note"] is None


async def test_update_pin_not_adder_forbidden(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner, member)
    pin = await _pin(session, project, article, member, is_shared=True,
                     shared_at=datetime.now(timezone.utc))
    resp = await client.patch(f"/api/projects/{project.id}/articles/{pin.id}",
                              json={"note": "hijack"}, headers=users.auth(owner))
    assert resp.status_code == 403


async def test_update_pin_others_private_is_404(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner, member)
    pin = await _pin(session, project, article, member)  # private
    resp = await client.patch(f"/api/projects/{project.id}/articles/{pin.id}",
                              json={"is_shared": True}, headers=users.auth(owner))
    assert resp.status_code == 404


async def test_update_pin_wrong_project_404(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner)
    other = await _project(session, owner, name="Other")
    pin = await _pin(session, project, article, owner)
    resp = await client.patch(f"/api/projects/{other.id}/articles/{pin.id}",
                              json={"is_shared": True}, headers=users.auth(owner))
    assert resp.status_code == 404


# --- removing pins ---

async def test_adder_removes_own_pin(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner, member)
    pin = await _pin(session, project, article, member)
    resp = await client.delete(f"/api/projects/{project.id}/articles/{pin.id}",
                               headers=users.auth(member))
    assert resp.status_code == 204


async def test_owner_removes_others_shared_pin(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner, member)
    pin = await _pin(session, project, article, member, is_shared=True,
                     shared_at=datetime.now(timezone.utc))
    resp = await client.delete(f"/api/projects/{project.id}/articles/{pin.id}",
                               headers=users.auth(owner))
    assert resp.status_code == 204


async def test_owner_cannot_remove_others_private_pin(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner, member)
    pin = await _pin(session, project, article, member)
    resp = await client.delete(f"/api/projects/{project.id}/articles/{pin.id}",
                               headers=users.auth(owner))
    assert resp.status_code == 404  # invisible, not merely forbidden


async def test_member_cannot_remove_others_shared_pin(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    m2 = await users.create()
    project = await _project(session, owner, member, m2)
    pin = await _pin(session, project, article, member, is_shared=True,
                     shared_at=datetime.now(timezone.utc))
    resp = await client.delete(f"/api/projects/{project.id}/articles/{pin.id}",
                               headers=users.auth(m2))
    assert resp.status_code == 403


async def test_remove_by_article_removes_own_and_shared_for_owner(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    now = datetime.now(timezone.utc)
    project = await _project(session, owner, member)
    await _pin(session, project, article, owner)  # owner's private pin
    await _pin(session, project, article, member, is_shared=True, shared_at=now)
    resp = await client.delete(
        f"/api/projects/{project.id}/articles/by-article/{article.id}",
        headers=users.auth(owner),
    )
    assert resp.status_code == 204
    left = (await client.get(f"/api/projects/{project.id}/articles",
                             headers=users.auth(member))).json()
    assert left == []


async def test_remove_by_article_member_keeps_others_shared(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    now = datetime.now(timezone.utc)
    project = await _project(session, owner, member)
    await _pin(session, project, article, member)  # member's own private pin
    await _pin(session, project, article, owner, is_shared=True, shared_at=now)
    resp = await client.delete(
        f"/api/projects/{project.id}/articles/by-article/{article.id}",
        headers=users.auth(member),
    )
    assert resp.status_code == 204
    left = (await client.get(f"/api/projects/{project.id}/articles",
                             headers=users.auth(member))).json()
    # The owner's shared pin survives; only the member's own pin went away.
    assert [p["added_by"]["username"] for p in left] == [owner.username]


async def test_remove_by_article_nothing_visible_404(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner, member)
    await _pin(session, project, article, owner)  # private → invisible to member
    resp = await client.delete(
        f"/api/projects/{project.id}/articles/by-article/{article.id}",
        headers=users.auth(member),
    )
    assert resp.status_code == 404


async def test_remove_by_article_member_cannot_remove_others_shared(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    now = datetime.now(timezone.utc)
    project = await _project(session, owner, member)
    await _pin(session, project, article, owner, is_shared=True, shared_at=now)
    resp = await client.delete(
        f"/api/projects/{project.id}/articles/by-article/{article.id}",
        headers=users.auth(member),
    )
    assert resp.status_code == 403


# --- unseen counts, visits, mute ---

async def test_unseen_counts_others_published_since_visit(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    second = await data.article(feed, title="Newer")
    project = await _project(session, owner, member)
    # Member visited between the two publishes.
    await _pin(session, project, article, owner, is_shared=True,
               shared_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    membership = await session.scalar(
        select(ProjectMember).where(ProjectMember.project_id == project.id,
                                    ProjectMember.user_id == member.id)
    )
    membership.last_visited_at = datetime(2026, 7, 2, tzinfo=timezone.utc)
    await session.commit()
    await _pin(session, project, second, owner, is_shared=True,
               shared_at=datetime(2026, 7, 3, tzinfo=timezone.utc))

    resp = await client.get("/api/projects", headers=users.auth(member))
    assert resp.json()[0]["unseen_count"] == 1


async def test_unseen_counts_everything_before_first_visit(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner, member)
    await _pin(session, project, article, owner, is_shared=True,
               shared_at=datetime.now(timezone.utc))
    resp = await client.get("/api/projects", headers=users.auth(member))
    assert resp.json()[0]["unseen_count"] == 1


async def test_unseen_ignores_own_and_private_pins(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    second = await data.article(feed, title="Private One")
    project = await _project(session, owner, member)
    await _pin(session, project, article, member, is_shared=True,
               shared_at=datetime.now(timezone.utc))  # member's own publish
    await _pin(session, project, second, owner)  # owner's private pin
    resp = await client.get("/api/projects", headers=users.auth(member))
    assert resp.json()[0]["unseen_count"] == 0


async def test_visit_resets_unseen(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner, member)
    await _pin(session, project, article, owner, is_shared=True,
               shared_at=datetime.now(timezone.utc))
    resp = await client.post(f"/api/projects/{project.id}/visit", headers=users.auth(member))
    assert resp.status_code == 204
    resp = await client.get("/api/projects", headers=users.auth(member))
    assert resp.json()[0]["unseen_count"] == 0


async def test_visit_non_member_404(client, users, session):
    owner = await users.create()
    outsider = await users.create()
    project = await _project(session, owner)
    resp = await client.post(f"/api/projects/{project.id}/visit", headers=users.auth(outsider))
    assert resp.status_code == 404


async def test_membership_mute_toggle(client, users, session):
    owner = await users.create()
    member = await users.create()
    project = await _project(session, owner, member)
    resp = await client.patch(f"/api/projects/{project.id}/membership",
                              json={"is_muted": True}, headers=users.auth(member))
    assert resp.status_code == 200
    assert resp.json()["is_muted"] is True
    # The other member's view is unaffected.
    resp = await client.get(f"/api/projects/{project.id}", headers=users.auth(owner))
    assert resp.json()["is_muted"] is False


async def test_membership_non_member_404(client, users, session):
    owner = await users.create()
    outsider = await users.create()
    project = await _project(session, owner)
    resp = await client.patch(f"/api/projects/{project.id}/membership",
                              json={"is_muted": True}, headers=users.auth(outsider))
    assert resp.status_code == 404


# --- publish push enqueues ---

async def test_add_shared_pin_enqueues_push(client, users, data, session, monkeypatch):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner, member)
    jobs = []

    async def record(job_name, *args):
        jobs.append((job_name, args))

    monkeypatch.setattr("app.routers.projects.enqueue", record)
    resp = await client.post(f"/api/projects/{project.id}/articles",
                             json={"article_id": article.id, "is_shared": True},
                             headers=users.auth(owner))
    assert resp.status_code == 201
    assert jobs == [("send_project_pin_push", (resp.json()["id"],))]


async def test_add_private_pin_enqueues_nothing(client, users, data, session, monkeypatch):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner, member)
    jobs = []

    async def record(job_name, *args):
        jobs.append((job_name, args))

    monkeypatch.setattr("app.routers.projects.enqueue", record)
    await client.post(f"/api/projects/{project.id}/articles",
                      json={"article_id": article.id}, headers=users.auth(owner))
    assert jobs == []


async def test_publish_flip_enqueues_push_once(client, users, data, session, monkeypatch):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner, member)
    pin = await _pin(session, project, article, owner)
    jobs = []

    async def record(job_name, *args):
        jobs.append((job_name, args))

    monkeypatch.setattr("app.routers.projects.enqueue", record)
    await client.patch(f"/api/projects/{project.id}/articles/{pin.id}",
                       json={"is_shared": True}, headers=users.auth(owner))
    # Re-publishing an already-shared pin must not notify again.
    await client.patch(f"/api/projects/{project.id}/articles/{pin.id}",
                       json={"is_shared": True}, headers=users.auth(owner))
    assert jobs == [("send_project_pin_push", (pin.id,))]


# --- picker status ---

async def test_article_project_status(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    with_my_pin = await _project(session, member, name="Mine")
    await _pin(session, with_my_pin, article, member)
    shared_by_other = await _project(session, owner, member, name="Theirs")
    await _pin(session, shared_by_other, article, owner, is_shared=True,
               shared_at=datetime.now(timezone.utc))
    empty = await _project(session, member, name="Empty")

    resp = await client.get(f"/api/projects/article/{article.id}", headers=users.auth(member))
    by_name = {s["project_name"]: s for s in resp.json()}
    assert set(by_name) == {"Mine", "Theirs", "Empty"}
    assert by_name["Mine"]["project_article_id"] is not None
    assert by_name["Mine"]["is_shared"] is False
    assert by_name["Mine"]["shared_by_others"] is False
    assert by_name["Theirs"]["project_article_id"] is None
    assert by_name["Theirs"]["shared_by_others"] is True
    assert by_name["Empty"]["project_article_id"] is None
    assert by_name["Empty"]["shared_by_others"] is False


async def test_article_project_status_ignores_others_private(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner, member)
    await _pin(session, project, article, owner)  # private → invisible to member
    resp = await client.get(f"/api/projects/article/{article.id}", headers=users.auth(member))
    [status] = resp.json()
    assert status["shared_by_others"] is False


# --- access through projects (user_can_access leg) ---

async def test_get_article_via_shared_project_pin(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    stranger = await users.create(username="reader")
    project = await _project(session, owner, stranger)
    await _pin(session, project, article, owner, is_shared=True,
               shared_at=datetime.now(timezone.utc))
    resp = await client.get(f"/api/articles/{article.id}", headers=users.auth(stranger))
    assert resp.status_code == 200


async def test_get_article_others_private_pin_denies(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    stranger = await users.create(username="reader")
    project = await _project(session, owner, stranger)
    await _pin(session, project, article, owner)  # private
    resp = await client.get(f"/api/articles/{article.id}", headers=users.auth(stranger))
    assert resp.status_code == 404


# --- embedding-based suggestions ---

def _vec(direction: str) -> list[float]:
    """Orthogonal toy vectors: 'ai' articles vs 'sports' articles."""
    return [1.0, 0.0, 0.0] if direction == "ai" else [0.0, 1.0, 0.0]


async def _embed(session, article, direction):
    from app.models import ArticleEmbedding

    session.add(ArticleEmbedding(article_id=article.id, model="", embedding=_vec(direction)))
    await session.commit()


async def test_status_suggests_best_matching_project(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    ai_article = await data.article(feed, title="Old AI Piece")
    sports_article = await data.article(feed, title="Match Report")
    new_article = await data.article(feed, title="Fresh AI News")
    await _embed(session, ai_article, "ai")
    await _embed(session, sports_article, "sports")
    await _embed(session, new_article, "ai")

    ai_project = await _project(session, owner, name="AI")
    await _pin(session, ai_project, ai_article, owner, is_shared=True,
               shared_at=datetime.now(timezone.utc))
    sports_project = await _project(session, owner, name="Sports")
    await _pin(session, sports_project, sports_article, owner, is_shared=True,
               shared_at=datetime.now(timezone.utc))

    resp = await client.get(f"/api/projects/article/{new_article.id}",
                            headers=users.auth(owner))
    by_name = {s["project_name"]: s for s in resp.json()}
    assert by_name["AI"]["suggested"] is True
    assert by_name["Sports"]["suggested"] is False


async def test_status_no_suggestion_without_embedding(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    project = await _project(session, owner, name="AI")
    pinned = await data.article(feed, title="Pinned")
    await _embed(session, pinned, "ai")
    await _pin(session, project, pinned, owner, is_shared=True,
               shared_at=datetime.now(timezone.utc))
    # `article` itself has no embedding row.
    resp = await client.get(f"/api/projects/article/{article.id}",
                            headers=users.auth(owner))
    assert all(s["suggested"] is False for s in resp.json())


async def test_status_never_suggests_where_already_pinned(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    twin = await data.article(feed, title="Twin")
    await _embed(session, article, "ai")
    await _embed(session, twin, "ai")
    project = await _project(session, owner, name="AI")
    await _pin(session, project, twin, owner, is_shared=True,
               shared_at=datetime.now(timezone.utc))
    await _pin(session, project, article, owner)  # already added by viewer

    resp = await client.get(f"/api/projects/article/{article.id}",
                            headers=users.auth(owner))
    [status] = resp.json()
    assert status["suggested"] is False


async def test_status_suggestion_ignores_others_private_pins(client, users, data, session):
    owner, member, feed, article = await _team(users, data)
    hidden = await data.article(feed, title="Hidden")
    await _embed(session, article, "ai")
    await _embed(session, hidden, "ai")
    project = await _project(session, owner, member, name="AI")
    await _pin(session, project, hidden, owner)  # private → invisible to member

    resp = await client.get(f"/api/projects/article/{article.id}",
                            headers=users.auth(member))
    [status] = resp.json()
    assert status["suggested"] is False


async def test_status_suggestion_skipped_without_vector_support(
    client, users, data, session, monkeypatch,
):
    from app import db as app_db

    owner, member, feed, article = await _team(users, data)
    pinned = await data.article(feed, title="Pinned")
    await _embed(session, article, "ai")
    await _embed(session, pinned, "ai")
    project = await _project(session, owner, name="AI")
    await _pin(session, project, pinned, owner, is_shared=True,
               shared_at=datetime.now(timezone.utc))
    monkeypatch.setattr(app_db, "vector_enabled", False)
    resp = await client.get(f"/api/projects/article/{article.id}",
                            headers=users.auth(owner))
    [status] = resp.json()
    assert status["suggested"] is False
