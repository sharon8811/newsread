import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app import llm, qa_agent
from app.routers import ai as ai_router
from app.models import Conversation, Message
from app.summarizer import ThinContentError


async def _setup(users, data):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    art = await data.article(feed, content_html="<p>" + "body " * 200 + "</p>")
    return user, feed, art


# --- ai/status ---

async def test_ai_status_unconfigured(client, users, monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: False)
    monkeypatch.setattr(qa_agent, "search_enabled", lambda: False)
    monkeypatch.setattr(qa_agent, "search_provider", lambda: None)
    user = await users.create()
    resp = await client.get("/api/ai/status", headers=users.auth(user))
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["search"] is False


async def test_ai_status_configured(client, users, monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(qa_agent, "search_enabled", lambda: True)
    monkeypatch.setattr(qa_agent, "search_provider", lambda: "searxng")
    monkeypatch.setattr(ai_router.settings, "openai_model", "my-model")
    user = await users.create()
    resp = await client.get("/api/ai/status", headers=users.auth(user))
    body = resp.json()
    assert body["configured"] is True
    assert body["model"] == "my-model"
    assert body["search_provider"] == "searxng"


# --- summarize ---

async def test_summarize_returns_cached(client, users, data, monkeypatch):
    user, feed, art = await _setup(users, data)
    art.summary = "full summary"
    art.summary_short = "short"
    await data.session.commit()
    resp = await client.post(f"/api/articles/{art.id}/summarize", headers=users.auth(user))
    assert resp.status_code == 200
    assert resp.json()["summary"] == "full summary"


async def test_summarize_generates(client, users, data, monkeypatch):
    user, feed, art = await _setup(users, data)
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    async def fake_generate(session, article):
        article.summary = "generated full"
        article.summary_short = "gen short"
        article.summary_medium = "gen medium"
        article.summary_model = "m"

    monkeypatch.setattr(ai_router, "generate_summaries", fake_generate)
    resp = await client.post(f"/api/articles/{art.id}/summarize", headers=users.auth(user))
    assert resp.status_code == 200
    assert resp.json()["summary"] == "generated full"


async def test_summarize_no_llm(client, users, data, monkeypatch):
    user, feed, art = await _setup(users, data)
    monkeypatch.setattr(llm, "is_configured", lambda: False)
    resp = await client.post(f"/api/articles/{art.id}/summarize", headers=users.auth(user))
    assert resp.status_code == 503


async def test_summarize_thin_content(client, users, data, monkeypatch):
    user, feed, art = await _setup(users, data)
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    async def raise_thin(session, article):
        raise ThinContentError()

    monkeypatch.setattr(ai_router, "generate_summaries", raise_thin)
    resp = await client.post(f"/api/articles/{art.id}/summarize", headers=users.auth(user))
    assert resp.status_code == 422


async def test_summarize_llm_failure(client, users, data, monkeypatch):
    user, feed, art = await _setup(users, data)
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    async def boom(session, article):
        raise RuntimeError("llm down")

    monkeypatch.setattr(ai_router, "generate_summaries", boom)
    resp = await client.post(f"/api/articles/{art.id}/summarize", headers=users.auth(user))
    assert resp.status_code == 502


async def test_summarize_force_regenerates(client, users, data, monkeypatch):
    user, feed, art = await _setup(users, data)
    art.summary = "old"
    art.summary_short = "old short"
    await data.session.commit()
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    async def fake_generate(session, article):
        article.summary = "new full"
        article.summary_short = "new short"

    monkeypatch.setattr(ai_router, "generate_summaries", fake_generate)
    resp = await client.post(f"/api/articles/{art.id}/summarize",
                             params={"force": "true"}, headers=users.auth(user))
    assert resp.json()["summary"] == "new full"


async def test_summarize_article_not_found(client, users, data):
    user, feed, art = await _setup(users, data)
    resp = await client.post("/api/articles/99999/summarize", headers=users.auth(user))
    assert resp.status_code == 404


# --- get conversation ---

async def test_get_conversation_empty(client, users, data):
    user, feed, art = await _setup(users, data)
    resp = await client.get(f"/api/articles/{art.id}/qa", headers=users.auth(user))
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_conversation_with_messages(client, users, data, session):
    user, feed, art = await _setup(users, data)
    conv = Conversation(user_id=user.id, article_id=art.id, messages=[])
    session.add(conv)
    await session.flush()
    session.add(Message(conversation_id=conv.id, role="user", content="hi"))
    session.add(Message(conversation_id=conv.id, role="assistant", content="hello",
                        tool_events=[{"name": "web_search", "args": {}, "summary": "x"}]))
    await session.commit()
    resp = await client.get(f"/api/articles/{art.id}/qa", headers=users.auth(user))
    body = resp.json()
    assert len(body) == 2
    assert body[0]["content"] == "hi"


async def test_get_conversation_article_not_found(client, users, data):
    user, feed, art = await _setup(users, data)
    resp = await client.get("/api/articles/99999/qa", headers=users.auth(user))
    assert resp.status_code == 404


# --- ask stream ---

def _parse_sse(text):
    events = []
    for frame in text.split("\n\n"):
        for line in frame.split("\n"):
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


async def test_ask_stream_success(client, users, data, session, monkeypatch):
    user, feed, art = await _setup(users, data)
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(qa_agent, "search_enabled", lambda: False)

    async def fake_ensure(session_, article):
        return "x" * 500

    monkeypatch.setattr(ai_router, "ensure_full_text", fake_ensure)

    async def fake_stream(**kwargs):
        yield {"type": "delta", "text": "Hello"}
        yield {"type": "result", "content": "Hello answer", "tool_events": []}

    monkeypatch.setattr(qa_agent, "stream_answer", fake_stream)

    resp = await client.post(f"/api/articles/{art.id}/qa/stream",
                             json={"content": "What is this?"}, headers=users.auth(user))
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert "delta" in types
    assert "done" in types
    # messages persisted
    msgs = (await session.scalars(select(Message))).all()
    assert len(msgs) == 2


async def test_ask_stream_thin_text_adds_hint(client, users, data, monkeypatch):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    art = await data.article(feed, content_html="<p>tiny</p>")
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(qa_agent, "search_enabled", lambda: True)

    async def fake_ensure(session_, article):
        return "tiny"

    monkeypatch.setattr(ai_router, "ensure_full_text", fake_ensure)
    captured = {}

    async def fake_stream(**kwargs):
        captured["text"] = kwargs["text"]
        yield {"type": "result", "content": "answer", "tool_events": []}

    monkeypatch.setattr(qa_agent, "stream_answer", fake_stream)
    resp = await client.post(f"/api/articles/{art.id}/qa/stream",
                             json={"content": "q"}, headers=users.auth(user))
    assert resp.status_code == 200
    assert "web_extract" in captured["text"]


async def test_ask_stream_no_llm(client, users, data, monkeypatch):
    user, feed, art = await _setup(users, data)
    monkeypatch.setattr(llm, "is_configured", lambda: False)
    resp = await client.post(f"/api/articles/{art.id}/qa/stream",
                             json={"content": "q"}, headers=users.auth(user))
    assert resp.status_code == 503


async def test_ask_stream_agent_error(client, users, data, monkeypatch):
    user, feed, art = await _setup(users, data)
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(qa_agent, "search_enabled", lambda: False)

    async def fake_ensure(session_, article):
        return "x" * 500

    monkeypatch.setattr(ai_router, "ensure_full_text", fake_ensure)

    async def boom_stream(**kwargs):
        raise RuntimeError("agent crashed")
        yield  # pragma: no cover

    monkeypatch.setattr(qa_agent, "stream_answer", boom_stream)
    resp = await client.post(f"/api/articles/{art.id}/qa/stream",
                             json={"content": "q"}, headers=users.auth(user))
    events = _parse_sse(resp.text)
    assert any(e["type"] == "error" for e in events)


async def test_ask_stream_empty_answer(client, users, data, monkeypatch):
    user, feed, art = await _setup(users, data)
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(qa_agent, "search_enabled", lambda: False)

    async def fake_ensure(session_, article):
        return "x" * 500

    monkeypatch.setattr(ai_router, "ensure_full_text", fake_ensure)

    async def empty_stream(**kwargs):
        yield {"type": "result", "content": "", "tool_events": []}

    monkeypatch.setattr(qa_agent, "stream_answer", empty_stream)
    resp = await client.post(f"/api/articles/{art.id}/qa/stream",
                             json={"content": "q"}, headers=users.auth(user))
    events = _parse_sse(resp.text)
    assert any(e["type"] == "error" and "empty" in e["detail"] for e in events)


async def test_ask_stream_with_entities_context(client, users, data, session, monkeypatch):
    from app.models import ArticleEntity, Entity

    user, feed, art = await _setup(users, data)
    entity = Entity(kind="github", canonical_key="a/b", url="https://github.com/a/b",
                    data={"full_name": "a/b", "stargazers_count": 5})
    session.add(entity)
    await session.flush()
    session.add(ArticleEntity(article_id=art.id, entity_id=entity.id, source="primary", position=0))
    await session.commit()

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(qa_agent, "search_enabled", lambda: False)

    async def fake_ensure(session_, article):
        return "x" * 500

    monkeypatch.setattr(ai_router, "ensure_full_text", fake_ensure)
    captured = {}

    async def fake_stream(**kwargs):
        captured["entities"] = kwargs["entities"]
        yield {"type": "result", "content": "answer", "tool_events": []}

    monkeypatch.setattr(qa_agent, "stream_answer", fake_stream)
    await client.post(f"/api/articles/{art.id}/qa/stream",
                      json={"content": "q"}, headers=users.auth(user))
    assert captured["entities"][0]["kind"] == "github"


async def test_ask_stream_article_not_found(client, users, data, monkeypatch):
    user, feed, art = await _setup(users, data)
    resp = await client.post("/api/articles/99999/qa/stream",
                             json={"content": "q"}, headers=users.auth(user))
    assert resp.status_code == 404


# --- project Q&A ---

async def _project_with_pins(users, data, session, *, note=None, with_summary=True):
    from datetime import datetime, timezone

    from app.models import Project, ProjectArticle, ProjectMember

    owner = await users.create(username="powner")
    member = await users.create(username="pmember")
    feed = await data.feed()
    await data.subscribe(owner, feed)
    art = await data.article(
        feed, title="Corpus Article",
        published_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        **({"summary_medium": "the medium summary"} if with_summary else {}),
    )
    project = Project(owner_id=owner.id, name="Research", description="focus")
    project.members = [
        ProjectMember(user_id=owner.id, role="owner"),
        ProjectMember(user_id=member.id, role="member"),
    ]
    session.add(project)
    await session.commit()
    await session.refresh(project)
    pin = ProjectArticle(
        project_id=project.id, article_id=art.id, added_by_user_id=owner.id,
        is_shared=True, shared_at=datetime.now(timezone.utc), note=note,
    )
    session.add(pin)
    await session.commit()
    return owner, member, project, art


async def test_get_project_conversation_empty(client, users, data, session):
    owner, member, project, art = await _project_with_pins(users, data, session)
    resp = await client.get(f"/api/projects/{project.id}/qa", headers=users.auth(owner))
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_project_conversation_non_member_404(client, users, data, session):
    owner, member, project, art = await _project_with_pins(users, data, session)
    outsider = await users.create()
    resp = await client.get(f"/api/projects/{project.id}/qa", headers=users.auth(outsider))
    assert resp.status_code == 404


async def test_get_project_conversation_with_messages(client, users, data, session):
    from app.models import Conversation, Message

    owner, member, project, art = await _project_with_pins(users, data, session)
    conv = Conversation(user_id=owner.id, project_id=project.id, messages=[])
    session.add(conv)
    await session.flush()
    session.add(Message(conversation_id=conv.id, role="user", content="hi"))
    await session.commit()
    resp = await client.get(f"/api/projects/{project.id}/qa", headers=users.auth(owner))
    assert [m["content"] for m in resp.json()] == ["hi"]


async def test_ask_project_stream_success(client, users, data, session, monkeypatch):
    owner, member, project, art = await _project_with_pins(
        users, data, session, note="worth reading",
    )
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    captured = {}

    async def fake_stream(**kwargs):
        captured.update(kwargs)
        yield {"type": "delta", "text": "Across"}
        yield {"type": "result", "content": "Across the articles…", "tool_events": []}

    monkeypatch.setattr(qa_agent, "stream_project_answer", fake_stream)
    resp = await client.post(f"/api/projects/{project.id}/qa/stream",
                             json={"content": "themes?"}, headers=users.auth(owner))
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert [e["type"] for e in events][-1] == "done"
    # Corpus carries title, summary and the member note.
    assert "Corpus Article" in captured["corpus"]
    assert "the medium summary" in captured["corpus"]
    assert "@powner: worth reading" in captured["corpus"]
    assert captured["name"] == "Research"
    # Messages persisted on the project conversation.
    msgs = (await session.scalars(select(Message))).all()
    assert len(msgs) == 2


async def test_ask_project_stream_excludes_others_private_pins(
    client, users, data, session, monkeypatch,
):
    from datetime import datetime, timezone

    from app.models import ProjectArticle

    owner, member, project, art = await _project_with_pins(users, data, session)
    feed2 = await data.feed()
    secret = await data.article(feed2, title="Secret Research")
    session.add(ProjectArticle(
        project_id=project.id, article_id=secret.id, added_by_user_id=owner.id,
        is_shared=False, note=None,
    ))
    await session.commit()
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    captured = {}

    async def fake_stream(**kwargs):
        captured.update(kwargs)
        yield {"type": "result", "content": "answer", "tool_events": []}

    monkeypatch.setattr(qa_agent, "stream_project_answer", fake_stream)
    resp = await client.post(f"/api/projects/{project.id}/qa/stream",
                             json={"content": "q"}, headers=users.auth(member))
    assert resp.status_code == 200
    assert "Secret Research" not in captured["corpus"]
    assert "Corpus Article" in captured["corpus"]


async def test_ask_project_stream_empty_project_422(client, users, data, session, monkeypatch):
    from app.models import Project, ProjectMember

    user = await users.create()
    project = Project(owner_id=user.id, name="Empty")
    project.members = [ProjectMember(user_id=user.id, role="owner")]
    session.add(project)
    await session.commit()
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    resp = await client.post(f"/api/projects/{project.id}/qa/stream",
                             json={"content": "q"}, headers=users.auth(user))
    assert resp.status_code == 422


async def test_ask_project_stream_no_llm(client, users, data, session, monkeypatch):
    owner, member, project, art = await _project_with_pins(users, data, session)
    monkeypatch.setattr(llm, "is_configured", lambda: False)
    resp = await client.post(f"/api/projects/{project.id}/qa/stream",
                             json={"content": "q"}, headers=users.auth(owner))
    assert resp.status_code == 503


async def test_ask_project_stream_agent_error(client, users, data, session, monkeypatch):
    owner, member, project, art = await _project_with_pins(users, data, session)
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    async def boom(**kwargs):
        raise RuntimeError("crash")
        yield  # pragma: no cover

    monkeypatch.setattr(qa_agent, "stream_project_answer", boom)
    resp = await client.post(f"/api/projects/{project.id}/qa/stream",
                             json={"content": "q"}, headers=users.auth(owner))
    assert any(e["type"] == "error" for e in _parse_sse(resp.text))


async def test_ask_project_stream_empty_answer(client, users, data, session, monkeypatch):
    owner, member, project, art = await _project_with_pins(users, data, session)
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    async def empty(**kwargs):
        yield {"type": "result", "content": "", "tool_events": []}

    monkeypatch.setattr(qa_agent, "stream_project_answer", empty)
    resp = await client.post(f"/api/projects/{project.id}/qa/stream",
                             json={"content": "q"}, headers=users.auth(owner))
    assert any(e["type"] == "error" for e in _parse_sse(resp.text))


async def test_ask_project_stream_article_without_summary_gets_hint(
    client, users, data, session, monkeypatch,
):
    owner, member, project, art = await _project_with_pins(
        users, data, session, with_summary=False,
    )
    art.excerpt = ""
    await session.commit()
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    captured = {}

    async def fake_stream(**kwargs):
        captured.update(kwargs)
        yield {"type": "result", "content": "a", "tool_events": []}

    monkeypatch.setattr(qa_agent, "stream_project_answer", fake_stream)
    await client.post(f"/api/projects/{project.id}/qa/stream",
                      json={"content": "q"}, headers=users.auth(owner))
    assert "no summary available" in captured["corpus"]
