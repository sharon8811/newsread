import json
from datetime import UTC, datetime

from sqlalchemy import select

from app import llm, qa_agent
from app.models import Conversation, Message
from app.routers import ai as ai_router
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
    monkeypatch.setattr(llm.settings, "openai_model", "my-model")
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

    async def fake_generate(session, article, **kwargs):
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

    async def raise_thin(session, article, **kwargs):
        raise ThinContentError()

    monkeypatch.setattr(ai_router, "generate_summaries", raise_thin)
    resp = await client.post(f"/api/articles/{art.id}/summarize", headers=users.auth(user))
    assert resp.status_code == 422


async def test_summarize_llm_failure(client, users, data, monkeypatch):
    user, feed, art = await _setup(users, data)
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    async def boom(session, article, **kwargs):
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

    async def fake_generate(session, article, **kwargs):
        article.summary = "new full"
        article.summary_short = "new short"

    monkeypatch.setattr(ai_router, "generate_summaries", fake_generate)
    resp = await client.post(
        f"/api/articles/{art.id}/summarize", params={"force": "true"}, headers=users.auth(user)
    )
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
    session.add(
        Message(
            conversation_id=conv.id,
            role="assistant",
            content="hello",
            tool_events=[{"name": "web_search", "args": {}, "summary": "x"}],
        )
    )
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

    resp = await client.post(
        f"/api/articles/{art.id}/qa/stream",
        json={"content": "What is this?"},
        headers=users.auth(user),
    )
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
    resp = await client.post(
        f"/api/articles/{art.id}/qa/stream", json={"content": "q"}, headers=users.auth(user)
    )
    assert resp.status_code == 200
    assert "web_extract" in captured["text"]


async def test_ask_stream_no_llm(client, users, data, monkeypatch):
    user, feed, art = await _setup(users, data)
    monkeypatch.setattr(llm, "is_configured", lambda: False)
    resp = await client.post(
        f"/api/articles/{art.id}/qa/stream", json={"content": "q"}, headers=users.auth(user)
    )
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
    resp = await client.post(
        f"/api/articles/{art.id}/qa/stream", json={"content": "q"}, headers=users.auth(user)
    )
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
    resp = await client.post(
        f"/api/articles/{art.id}/qa/stream", json={"content": "q"}, headers=users.auth(user)
    )
    events = _parse_sse(resp.text)
    assert any(e["type"] == "error" and "empty" in e["detail"] for e in events)


async def test_ask_stream_with_entities_context(client, users, data, session, monkeypatch):
    from app.models import ArticleEntity, Entity

    user, feed, art = await _setup(users, data)
    entity = Entity(
        kind="github",
        canonical_key="a/b",
        url="https://github.com/a/b",
        data={"full_name": "a/b", "stargazers_count": 5},
    )
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
    await client.post(
        f"/api/articles/{art.id}/qa/stream", json={"content": "q"}, headers=users.auth(user)
    )
    assert captured["entities"][0]["kind"] == "github"


async def test_ask_stream_article_not_found(client, users, data, monkeypatch):
    user, feed, art = await _setup(users, data)
    resp = await client.post(
        "/api/articles/99999/qa/stream", json={"content": "q"}, headers=users.auth(user)
    )
    assert resp.status_code == 404


# --- Hacker News discussion Q&A ---


def _discussion_snapshot(discussion_id: str):
    return {
        "provider": "hackernews",
        "discussion_id": discussion_id,
        "fetched_at": "2026-07-12T12:00:00Z",
        "reported_total": 4,
        "included_total": 1,
        "comments": [
            {
                "id": 101,
                "parent_id": int(discussion_id),
                "author": "reader",
                "text": "A useful correction",
                "created_at": "2026-07-12T11:00:00Z",
                "depth": 0,
                "position": 0,
                "deleted": False,
                "dead": False,
            }
        ],
    }


async def test_discussion_conversation_is_separate(client, users, data, session):
    user, feed, art = await _setup(users, data)
    art.comments_url = "https://news.ycombinator.com/item?id=99"
    article_conv = Conversation(user_id=user.id, article_id=art.id, kind="article", messages=[])
    discussion_conv = Conversation(
        user_id=user.id, article_id=art.id, kind="discussion", messages=[]
    )
    session.add_all([article_conv, discussion_conv])
    await session.flush()
    session.add(Message(conversation_id=discussion_conv.id, role="user", content="discussion"))
    await session.commit()

    article_resp = await client.get(f"/api/articles/{art.id}/qa", headers=users.auth(user))
    discussion_resp = await client.get(
        f"/api/articles/{art.id}/discussion/qa", headers=users.auth(user)
    )
    assert article_resp.json() == []
    assert discussion_resp.json()[0]["content"] == "discussion"


async def test_discussion_stream_uses_client_snapshot(client, users, data, session, monkeypatch):
    user, feed, art = await _setup(users, data)
    art.comments_url = "https://news.ycombinator.com/item?id=99"
    await session.commit()
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    async def fake_ensure(session_, article):
        return "article text " * 50

    monkeypatch.setattr(ai_router, "ensure_full_text", fake_ensure)
    captured = {}

    async def fake_stream(**kwargs):
        captured["snapshot"] = kwargs["snapshot"]
        yield {"type": "delta", "text": "Community"}
        yield {"type": "result", "content": "Community summary", "tool_events": []}

    monkeypatch.setattr(qa_agent, "stream_discussion_answer", fake_stream)
    resp = await client.post(
        f"/api/articles/{art.id}/discussion/qa/stream",
        json={"content": "Summarize", "snapshot": _discussion_snapshot("99")},
        headers=users.auth(user),
    )
    assert resp.status_code == 200
    assert any(event["type"] == "done" for event in _parse_sse(resp.text))
    assert captured["snapshot"]["comments"][0]["text"] == "A useful correction"


async def test_discussion_stream_rejects_mismatched_story(client, users, data, session):
    user, feed, art = await _setup(users, data)
    art.comments_url = "https://news.ycombinator.com/item?id=99"
    await session.commit()
    resp = await client.post(
        f"/api/articles/{art.id}/discussion/qa/stream",
        json={"content": "Summarize", "snapshot": _discussion_snapshot("98")},
        headers=users.auth(user),
    )
    assert resp.status_code == 422


async def test_discussion_snapshot_size_and_count_are_validated(client, users, data, session):
    user, feed, art = await _setup(users, data)
    art.comments_url = "https://news.ycombinator.com/item?id=99"
    await session.commit()
    snapshot = _discussion_snapshot("99")
    snapshot["included_total"] = 2
    resp = await client.post(
        f"/api/articles/{art.id}/discussion/qa/stream",
        json={"content": "Summarize", "snapshot": snapshot},
        headers=users.auth(user),
    )
    assert resp.status_code == 422


# --- project Q&A ---


async def _project_with_pins(users, data, session, *, comment=None, with_summary=True):

    from app.models import Project, ProjectArticle, ProjectArticleComment, ProjectMember

    owner = await users.create(username="powner")
    member = await users.create(username="pmember")
    feed = await data.feed()
    await data.subscribe(owner, feed)
    art = await data.article(
        feed,
        title="Corpus Article",
        published_at=datetime(2026, 7, 1, tzinfo=UTC),
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
        project_id=project.id,
        article_id=art.id,
        added_by_user_id=owner.id,
        is_shared=True,
        shared_at=datetime.now(UTC),
    )
    session.add(pin)
    if comment:
        session.add(
            ProjectArticleComment(
                project_id=project.id,
                article_id=art.id,
                author_id=owner.id,
                body=comment,
            )
        )
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
    from app.models import ProjectArticleComment, ProjectArticleState

    owner, member, project, art = await _project_with_pins(
        users,
        data,
        session,
        comment="worth reading",
    )
    session.add(
        ProjectArticleState(
            project_id=project.id,
            article_id=art.id,
            status="done",
            updated_by_user_id=owner.id,
        )
    )
    session.add(
        ProjectArticleComment(
            project_id=project.id,
            article_id=art.id,
            author_id=member.id,
            body="wrapped up",
            link_url="https://github.com/o/r/pull/7",
        )
    )
    await session.commit()
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    captured = {}

    async def fake_stream(**kwargs):
        captured.update(kwargs)
        yield {"type": "delta", "text": "Across"}
        yield {"type": "result", "content": "Across the articles…", "tool_events": []}

    monkeypatch.setattr(qa_agent, "stream_project_answer", fake_stream)
    resp = await client.post(
        f"/api/projects/{project.id}/qa/stream",
        json={"content": "themes?"},
        headers=users.auth(owner),
    )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert [e["type"] for e in events][-1] == "done"
    # Corpus carries title, summary, the discussion thread and the ticket status.
    assert "Corpus Article" in captured["corpus"]
    assert "the medium summary" in captured["corpus"]
    assert "@powner: worth reading" in captured["corpus"]
    assert "@pmember: wrapped up (https://github.com/o/r/pull/7)" in captured["corpus"]
    assert "Status: done" in captured["corpus"]
    assert captured["name"] == "Research"
    # Messages persisted on the project conversation.
    msgs = (await session.scalars(select(Message))).all()
    assert len(msgs) == 2


async def test_ask_project_stream_excludes_others_private_pins(
    client,
    users,
    data,
    session,
    monkeypatch,
):

    from app.models import ProjectArticle

    owner, member, project, art = await _project_with_pins(users, data, session)
    feed2 = await data.feed()
    secret = await data.article(feed2, title="Secret Research")
    session.add(
        ProjectArticle(
            project_id=project.id,
            article_id=secret.id,
            added_by_user_id=owner.id,
            is_shared=False,
        )
    )
    await session.commit()
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    captured = {}

    async def fake_stream(**kwargs):
        captured.update(kwargs)
        yield {"type": "result", "content": "answer", "tool_events": []}

    monkeypatch.setattr(qa_agent, "stream_project_answer", fake_stream)
    resp = await client.post(
        f"/api/projects/{project.id}/qa/stream", json={"content": "q"}, headers=users.auth(member)
    )
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
    resp = await client.post(
        f"/api/projects/{project.id}/qa/stream", json={"content": "q"}, headers=users.auth(user)
    )
    assert resp.status_code == 422


async def test_ask_project_stream_no_llm(client, users, data, session, monkeypatch):
    owner, member, project, art = await _project_with_pins(users, data, session)
    monkeypatch.setattr(llm, "is_configured", lambda: False)
    resp = await client.post(
        f"/api/projects/{project.id}/qa/stream", json={"content": "q"}, headers=users.auth(owner)
    )
    assert resp.status_code == 503


async def test_ask_project_stream_agent_error(client, users, data, session, monkeypatch):
    owner, member, project, art = await _project_with_pins(users, data, session)
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    async def boom(**kwargs):
        raise RuntimeError("crash")
        yield  # pragma: no cover

    monkeypatch.setattr(qa_agent, "stream_project_answer", boom)
    resp = await client.post(
        f"/api/projects/{project.id}/qa/stream", json={"content": "q"}, headers=users.auth(owner)
    )
    assert any(e["type"] == "error" for e in _parse_sse(resp.text))


async def test_ask_project_stream_empty_answer(client, users, data, session, monkeypatch):
    owner, member, project, art = await _project_with_pins(users, data, session)
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    async def empty(**kwargs):
        yield {"type": "result", "content": "", "tool_events": []}

    monkeypatch.setattr(qa_agent, "stream_project_answer", empty)
    resp = await client.post(
        f"/api/projects/{project.id}/qa/stream", json={"content": "q"}, headers=users.auth(owner)
    )
    assert any(e["type"] == "error" for e in _parse_sse(resp.text))


async def test_ask_project_stream_article_without_summary_gets_hint(
    client,
    users,
    data,
    session,
    monkeypatch,
):
    owner, member, project, art = await _project_with_pins(
        users,
        data,
        session,
        with_summary=False,
    )
    art.excerpt = ""
    await session.commit()
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    captured = {}

    async def fake_stream(**kwargs):
        captured.update(kwargs)
        yield {"type": "result", "content": "a", "tool_events": []}

    monkeypatch.setattr(qa_agent, "stream_project_answer", fake_stream)
    await client.post(
        f"/api/projects/{project.id}/qa/stream", json={"content": "q"}, headers=users.auth(owner)
    )
    assert "no summary available" in captured["corpus"]


# --- bring-your-own-key: per-user config + usage logging ---

from app import crypto
from app.models import LLMUsage, UserAISettings

OWN_KEY = "sk-own-12345678"


async def _own_key(session, user, *, provider="openai", model="gpt-5"):
    session.add(
        UserAISettings(
            user_id=user.id,
            provider=provider,
            model=model,
            api_key_enc=crypto.encrypt_token(OWN_KEY),
            key_hint=OWN_KEY[-4:],
        )
    )
    await session.commit()


async def test_ai_status_reports_user_source(client, users, session, monkeypatch):
    monkeypatch.setattr(qa_agent, "search_enabled", lambda: False)
    monkeypatch.setattr(qa_agent, "search_provider", lambda: None)
    user = await users.create()
    await _own_key(session, user, model="my-own-model")
    body = (await client.get("/api/ai/status", headers=users.auth(user))).json()
    assert body["configured"] is True
    assert body["model"] == "my-own-model"
    assert body["source"] == "user"


async def test_ai_status_reports_system_source(client, users, monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(qa_agent, "search_enabled", lambda: False)
    monkeypatch.setattr(qa_agent, "search_provider", lambda: None)
    user = await users.create()
    body = (await client.get("/api/ai/status", headers=users.auth(user))).json()
    assert body["source"] == "system"


async def test_summarize_on_user_key_logs_usage(client, users, data, session, monkeypatch):
    user, feed, art = await _setup(users, data)
    await _own_key(session, user)
    captured = {}

    async def fake_generate(session_, article, **kwargs):
        captured["config"] = kwargs["config"]
        kwargs["usage"].add(50, 10)
        article.summary = "full"
        article.summary_short = "s"

    monkeypatch.setattr(ai_router, "generate_summaries", fake_generate)
    resp = await client.post(f"/api/articles/{art.id}/summarize", headers=users.auth(user))
    assert resp.status_code == 200
    assert captured["config"].user_owned is True
    assert captured["config"].api_key == OWN_KEY
    row = (await session.scalars(select(LLMUsage))).one()
    assert row.feature == "summary"
    assert row.status == "ok"
    assert row.prompt_tokens == 50
    assert row.completion_tokens == 10


async def test_summarize_failure_on_user_key_logs_error(client, users, data, session, monkeypatch):
    user, feed, art = await _setup(users, data)
    await _own_key(session, user)

    async def boom(session_, article, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(ai_router, "generate_summaries", boom)
    resp = await client.post(f"/api/articles/{art.id}/summarize", headers=users.auth(user))
    assert resp.status_code == 502
    row = (await session.scalars(select(LLMUsage))).one()
    assert row.status == "error"
    assert "llm down" in row.error


async def test_summarize_on_system_key_not_logged(client, users, data, session, monkeypatch):
    user, feed, art = await _setup(users, data)
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    async def fake_generate(session_, article, **kwargs):
        article.summary = "full"
        article.summary_short = "s"

    monkeypatch.setattr(ai_router, "generate_summaries", fake_generate)
    resp = await client.post(f"/api/articles/{art.id}/summarize", headers=users.auth(user))
    assert resp.status_code == 200
    assert (await session.scalars(select(LLMUsage))).all() == []


async def test_summarize_undecryptable_key_503(client, users, data, session, monkeypatch):
    user, feed, art = await _setup(users, data)
    await _own_key(session, user)

    def broken(ciphertext):
        raise crypto.TokenCryptoError("key changed")

    monkeypatch.setattr(crypto, "decrypt_token", broken)
    resp = await client.post(f"/api/articles/{art.id}/summarize", headers=users.auth(user))
    assert resp.status_code == 503
    assert "re-enter" in resp.json()["detail"]


async def test_share_message_on_user_key_logs_usage(client, users, data, session, monkeypatch):
    user, feed, art = await _setup(users, data)
    await _own_key(session, user)

    async def fake_share(**kwargs):
        kwargs["usage"].add(7, 3)
        return "a note"

    monkeypatch.setattr(llm, "share_message", fake_share)
    resp = await client.post(
        "/api/ai/share-message", json={"article_id": art.id}, headers=users.auth(user)
    )
    assert resp.status_code == 200
    row = (await session.scalars(select(LLMUsage))).one()
    assert row.feature == "share"
    assert row.prompt_tokens == 7
    assert row.completion_tokens == 3


async def test_ask_stream_on_user_key_logs_usage(client, users, data, session, monkeypatch):
    user, feed, art = await _setup(users, data)
    await _own_key(session, user)
    monkeypatch.setattr(qa_agent, "search_enabled", lambda: False)

    async def fake_ensure(session_, article):
        return "x" * 500

    monkeypatch.setattr(ai_router, "ensure_full_text", fake_ensure)
    captured = {}

    async def fake_stream(**kwargs):
        captured["config"] = kwargs["config"]
        yield {
            "type": "result",
            "content": "answer",
            "tool_events": [],
            "usage": {"prompt_tokens": 11, "completion_tokens": 22},
        }

    monkeypatch.setattr(qa_agent, "stream_answer", fake_stream)
    resp = await client.post(
        f"/api/articles/{art.id}/qa/stream", json={"content": "q"}, headers=users.auth(user)
    )
    assert resp.status_code == 200
    assert captured["config"].user_owned is True
    row = (await session.scalars(select(LLMUsage))).one()
    assert row.feature == "qa"
    assert row.prompt_tokens == 11
    assert row.completion_tokens == 22
    assert row.status == "ok"


async def test_ask_stream_error_on_user_key_logs_error(client, users, data, session, monkeypatch):
    user, feed, art = await _setup(users, data)
    await _own_key(session, user)
    monkeypatch.setattr(qa_agent, "search_enabled", lambda: False)

    async def fake_ensure(session_, article):
        return "x" * 500

    monkeypatch.setattr(ai_router, "ensure_full_text", fake_ensure)

    async def boom_stream(**kwargs):
        raise RuntimeError("agent crashed")
        yield  # pragma: no cover

    monkeypatch.setattr(qa_agent, "stream_answer", boom_stream)
    resp = await client.post(
        f"/api/articles/{art.id}/qa/stream", json={"content": "q"}, headers=users.auth(user)
    )
    assert any(e["type"] == "error" for e in _parse_sse(resp.text))
    row = (await session.scalars(select(LLMUsage))).one()
    assert row.status == "error"
    assert "agent crashed" in row.error


async def test_ai_status_undecryptable_key_reads_unconfigured(client, users, session, monkeypatch):
    monkeypatch.setattr(qa_agent, "search_enabled", lambda: False)
    monkeypatch.setattr(qa_agent, "search_provider", lambda: None)
    user = await users.create()
    await _own_key(session, user)

    def broken(ciphertext):
        raise crypto.TokenCryptoError("key changed")

    monkeypatch.setattr(crypto, "decrypt_token", broken)
    body = (await client.get("/api/ai/status", headers=users.auth(user))).json()
    assert body["configured"] is False
    assert body["source"] is None


async def test_share_message_empty_502(client, users, data, monkeypatch):
    user, feed, art = await _setup(users, data)
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    async def fake_share(**kwargs):
        return ""

    monkeypatch.setattr(llm, "share_message", fake_share)
    resp = await client.post(
        "/api/ai/share-message", json={"article_id": art.id}, headers=users.auth(user)
    )
    assert resp.status_code == 502


async def test_ask_stream_error_discards_flushed_conversation(
    client, users, data, session, monkeypatch
):
    """A failed first question must not persist an empty conversation row —
    the error-path usage commit rolls back first."""
    user, feed, art = await _setup(users, data)
    await _own_key(session, user)
    monkeypatch.setattr(qa_agent, "search_enabled", lambda: False)

    async def fake_ensure(session_, article):
        return "x" * 500

    monkeypatch.setattr(ai_router, "ensure_full_text", fake_ensure)

    async def boom_stream(**kwargs):
        raise RuntimeError("agent crashed")
        yield  # pragma: no cover

    monkeypatch.setattr(qa_agent, "stream_answer", boom_stream)
    await client.post(
        f"/api/articles/{art.id}/qa/stream", json={"content": "q"}, headers=users.auth(user)
    )
    assert (await session.scalars(select(Conversation))).all() == []
    row = (await session.scalars(select(LLMUsage))).one()
    assert row.status == "error"


async def test_share_message_empty_on_user_key_logs_error(
    client, users, data, session, monkeypatch
):
    user, feed, art = await _setup(users, data)
    await _own_key(session, user)

    async def fake_share(**kwargs):
        return ""

    monkeypatch.setattr(llm, "share_message", fake_share)
    resp = await client.post(
        "/api/ai/share-message", json={"article_id": art.id}, headers=users.auth(user)
    )
    assert resp.status_code == 502
    row = (await session.scalars(select(LLMUsage))).one()
    assert row.status == "error"
    assert "empty" in row.error


# --- POST /articles/{id}/related-synthesis ---


async def _with_related(users, data, session):
    """Article + one related article, linked via a shared entity so the
    related query's entity-overlap leg finds it without embeddings."""
    from app.models import ArticleEntity, Entity

    user, feed, art = await _setup(users, data)
    other = await data.article(feed, title="Other Coverage", summary_medium="other summary")
    entity = Entity(kind="github", canonical_key="acme/synth", url="https://gh/acme/synth")
    session.add(entity)
    await session.commit()
    for a in (art, other):
        session.add(ArticleEntity(article_id=a.id, entity_id=entity.id, source="primary"))
    await session.commit()
    return user, art, other


async def test_synthesis_article_not_accessible(client, users, data):
    user = await users.create()
    feed = await data.feed()
    art = await data.article(feed)  # not subscribed
    resp = await client.post(f"/api/articles/{art.id}/related-synthesis", headers=users.auth(user))
    assert resp.status_code == 404


async def test_synthesis_422_without_related(client, users, data, monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    user, feed, art = await _setup(users, data)
    resp = await client.post(f"/api/articles/{art.id}/related-synthesis", headers=users.auth(user))
    assert resp.status_code == 422


async def test_synthesis_503_without_llm(client, users, data, session):
    user, art, _ = await _with_related(users, data, session)
    resp = await client.post(f"/api/articles/{art.id}/related-synthesis", headers=users.auth(user))
    assert resp.status_code == 503


async def test_synthesis_happy_path_structured_timeline(client, users, data, session, monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    user, art, other = await _with_related(users, data, session)
    art.summary_medium = "main summary"
    await session.commit()

    captured = {}

    async def fake_synthesize(sources, *, config=None, usage=None):
        captured["sources"] = sources
        return llm.RelatedSynthesis(
            overview="Overall picture [2].",
            timeline_raw="- May 1 — a thing happened [2]\n- May 2 — more [1]",
            perspectives="- [2] adds an angle",
        )

    monkeypatch.setattr(ai_router.llm, "synthesize_related", fake_synthesize)
    resp = await client.post(f"/api/articles/{art.id}/related-synthesis", headers=users.auth(user))
    assert resp.status_code == 200
    body = resp.json()
    assert body["overview"] == "Overall picture [2]."
    assert body["timeline"] == [
        {"when": "May 1", "what": "a thing happened [2]"},
        {"when": "May 2", "what": "more [1]"},
    ]
    assert body["timeline_raw"] is None  # structured parse succeeded
    assert body["perspectives"] == "- [2] adds an angle"
    assert body["sources"][0] == {"n": 1, "id": art.id, "title": art.title}
    assert body["sources"][1] == {"n": 2, "id": other.id, "title": "Other Coverage"}
    # The LLM read stored summaries, not fetched pages.
    assert captured["sources"][0] == (art.title, "main summary")
    assert captured["sources"][1] == ("Other Coverage", "other summary")


async def test_synthesis_unparseable_timeline_echoes_raw(client, users, data, session, monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    user, art, _ = await _with_related(users, data, session)

    async def fake_synthesize(sources, *, config=None, usage=None):
        return llm.RelatedSynthesis(
            overview="o", timeline_raw="events unfolded gradually", perspectives=None
        )

    monkeypatch.setattr(ai_router.llm, "synthesize_related", fake_synthesize)
    body = (
        await client.post(f"/api/articles/{art.id}/related-synthesis", headers=users.auth(user))
    ).json()
    assert body["timeline"] is None
    assert body["timeline_raw"] == "events unfolded gradually"


async def test_synthesis_llm_failure_logs_usage(client, users, data, session, monkeypatch):
    user, art, _ = await _with_related(users, data, session)
    await _own_key(session, user)

    async def boom(sources, *, config=None, usage=None):
        raise RuntimeError("llm down")

    monkeypatch.setattr(ai_router.llm, "synthesize_related", boom)
    resp = await client.post(f"/api/articles/{art.id}/related-synthesis", headers=users.auth(user))
    assert resp.status_code == 502
    row = await session.scalar(select(LLMUsage).where(LLMUsage.feature == "synthesis"))
    assert row is not None and row.status == "error"


async def test_synthesis_empty_overview_is_502(client, users, data, session, monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    user, art, _ = await _with_related(users, data, session)

    async def fake_synthesize(sources, *, config=None, usage=None):
        return llm.RelatedSynthesis(overview="", timeline_raw=None, perspectives=None)

    monkeypatch.setattr(ai_router.llm, "synthesize_related", fake_synthesize)
    resp = await client.post(f"/api/articles/{art.id}/related-synthesis", headers=users.auth(user))
    assert resp.status_code == 502
