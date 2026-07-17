import json
import logging
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import crypto, llm, qa_agent
from ..access import accessible_article
from ..deps import CurrentUser, DbSession
from ..enrichers import badge_for
from ..extractor import clip_for_llm, ensure_full_text, is_thin
from ..fetcher import canonical_hn_comments_url
from ..models import (
    Article,
    ArticleEntity,
    Conversation,
    Entity,
    Message,
    Project,
    ProjectArticle,
    ProjectArticleComment,
    ProjectArticleState,
    User,
)
from ..schemas import (
    AiStatusOut,
    AskIn,
    DiscussionAskIn,
    MessageOut,
    ShareMessageIn,
    ShareMessageOut,
    SummaryOut,
    SynthesisOut,
    SynthesisSourceOut,
    SynthesisTimelineItem,
)
from ..summarizer import SummarySkipped, ThinContentError, generate_summaries
from .articles import related_articles
from .projects import _member_or_404, visible_pins

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ai"])


async def _resolve_llm(session: AsyncSession, user: User) -> llm.LLMConfig:
    """The user's own key when they saved one, else the server default — 503
    when neither is usable."""
    # crypto.TokenCryptoError propagates to the app-level 503 handler.
    config = await llm.resolve_config(session, user.id)
    if config is None:
        raise HTTPException(
            status_code=503,
            detail="No LLM is configured. Add your own key in Settings, or set "
            "OPENAI_API_KEY, OPENAI_BASE_URL and OPENAI_MODEL on the server.",
        )
    return config


@router.get("/ai/status", response_model=AiStatusOut)
async def ai_status(
    user: CurrentUser,
    session: DbSession,
):
    try:
        config = await llm.resolve_config(session, user.id)
    except crypto.TokenCryptoError:
        config = None
    return AiStatusOut(
        configured=config is not None,
        model=config.model if config else None,
        search=qa_agent.search_enabled(),
        search_provider=qa_agent.search_provider(),
        source=("user" if config.user_owned else "system") if config else None,
    )


@router.post("/articles/{article_id}/summarize", response_model=SummaryOut)
async def summarize_article(
    article_id: int,
    user: CurrentUser,
    session: DbSession,
    force: bool = False,
):
    article = await accessible_article(session, user.id, article_id)
    if not force and (
        (article.summary and article.summary_short) or article.summary_skipped_reason == "too_short"
    ):
        return _summary_out(article)
    config = await _resolve_llm(session, user)

    # Both domain exits pass through unrecorded because no LLM call happened.
    # ThinContentError remains a 422; SummarySkipped is an intentional 200.
    try:
        async with llm.usage_tracker(
            session,
            user_id=user.id,
            feature="summary",
            config=config,
            log_label=f"Summarization for article {article.id}",
            passthrough=(ThinContentError, SummarySkipped),
        ) as usage:
            await generate_summaries(
                session, article, config=config, usage=usage, allow_vision=True
            )
    except SummarySkipped:
        pass
    return _summary_out(article)


def _summary_out(article: Article) -> SummaryOut:
    return SummaryOut(
        summary=article.summary,
        summary_short=article.summary_short,
        summary_medium=article.summary_medium,
        model=article.summary_model,
        generated_at=article.summary_generated_at,
        skipped_reason=article.summary_skipped_reason,
    )


@router.post("/ai/share-message", response_model=ShareMessageOut)
async def share_message(
    body: ShareMessageIn,
    user: CurrentUser,
    session: DbSession,
):
    """Generate (or refine a draft of) the note that accompanies an article
    shared to a messaging platform. Uses the stored summary — never fetches."""
    article = await accessible_article(session, user.id, body.article_id)
    config = await _resolve_llm(session, user)
    summary = article.summary_medium or article.summary or article.excerpt or ""
    async with llm.usage_tracker(
        session,
        user_id=user.id,
        feature="share",
        config=config,
        log_label=f"Share-message generation for article {article.id}",
    ) as usage:
        text = await llm.share_message(
            title=article.title,
            summary=summary,
            draft=body.draft,
            tone=body.tone,
            target_name=body.target_name,
            config=config,
            usage=usage,
        )
        if not text:
            # An empty reply is a failed call — logged as such so the usage
            # trail matches the 502 the client sees.
            raise llm.EmptyResponseError("The LLM returned an empty message")
    return ShareMessageOut(message=text)


@router.post("/articles/{article_id}/related-synthesis", response_model=SynthesisOut)
async def synthesize_related_coverage(
    article_id: int,
    user: CurrentUser,
    session: DbSession,
):
    """Lazy 'synthesize coverage' for the related-articles section: one LLM
    call over the stored summaries of the article and its related set —
    nothing is fetched, and nothing runs unless the user clicked."""
    article = await accessible_article(session, user.id, article_id)
    related = await related_articles(session, user.id, article)
    if not related:
        raise HTTPException(status_code=422, detail="No related coverage to synthesize yet")
    config = await _resolve_llm(session, user)

    # Capture ORM attrs before the LLM call — the error path rolls back and
    # expired attributes must not be touched (same discipline as summarize).
    sources = [(article.title, article.summary_medium or article.excerpt or "")] + [
        (row.article.title, row.article.summary_medium or row.article.excerpt or "")
        for row in related
    ]
    source_meta = [SynthesisSourceOut(n=1, id=article.id, title=article.title)] + [
        SynthesisSourceOut(n=index, id=row.article.id, title=row.article.title)
        for index, row in enumerate(related, start=2)
    ]
    async with llm.usage_tracker(
        session,
        user_id=user.id,
        feature="synthesis",
        config=config,
        log_label=f"Coverage synthesis for article {article_id}",
    ) as usage:
        result = await llm.synthesize_related(sources, config=config, usage=usage)
        if not result.overview:
            raise llm.EmptyResponseError("The LLM returned an empty synthesis")
    items = llm.parse_timeline(result.timeline_raw)
    return SynthesisOut(
        overview=result.overview,
        timeline=[SynthesisTimelineItem(**item) for item in items] if items else None,
        timeline_raw=result.timeline_raw if items is None else None,
        perspectives=result.perspectives,
        sources=source_meta,
    )


async def _get_or_create_conversation(
    session: AsyncSession,
    user_id: int,
    *,
    article_id: int | None = None,
    project_id: int | None = None,
    kind: str = "article",
) -> Conversation:
    """One thread per (article, user, kind), or per (project, user)."""
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .options(selectinload(Conversation.messages))
    )
    if project_id is not None:
        stmt = stmt.where(Conversation.project_id == project_id)
    else:
        stmt = stmt.where(Conversation.article_id == article_id, Conversation.kind == kind)
    conversation = await session.scalar(stmt)
    if conversation is None:
        # messages is set while the object is transient — an assignment after
        # flush would trigger a sync lazy-load, which async sessions forbid.
        conversation = Conversation(
            user_id=user_id,
            article_id=article_id,
            project_id=project_id,
            kind=kind,
            messages=[],
        )
        session.add(conversation)
        await session.flush()
    return conversation


@router.get("/articles/{article_id}/qa", response_model=list[MessageOut])
async def get_conversation(
    article_id: int,
    user: CurrentUser,
    session: DbSession,
):
    await accessible_article(session, user.id, article_id)
    conversation = await session.scalar(
        select(Conversation)
        .where(
            Conversation.user_id == user.id,
            Conversation.article_id == article_id,
            Conversation.kind == "article",
        )
        .options(selectinload(Conversation.messages))
    )
    if conversation is None:
        return []
    return [MessageOut.model_validate(m) for m in conversation.messages]


async def _entity_context(session: AsyncSession, article_id: int) -> list[dict]:
    """Cached enricher data (repo stats, paper metadata…) for the agent's prompt."""
    rows = (
        await session.execute(
            select(ArticleEntity, Entity)
            .join(Entity, Entity.id == ArticleEntity.entity_id)
            .where(ArticleEntity.article_id == article_id)
            .order_by(ArticleEntity.position)
        )
    ).all()
    return [
        {
            "kind": entity.kind,
            "key": entity.canonical_key,
            "url": entity.url,
            "badge": badge_for(entity.kind, entity.data or {}),
        }
        for _, entity in rows
    ]


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _qa_stream_response(
    session: AsyncSession,
    *,
    user_id: int,
    config: llm.LLMConfig,
    conversation_id: int,
    question: str,
    events,
    log_label: str,
) -> StreamingResponse:
    """The SSE pipeline shared by all Q&A streams: forward agent events,
    meter usage, then persist the exchange and emit `done`.

    `events` is the qa_agent event generator; `conversation_id` and `user_id`
    are captured by value because the error paths roll the session back and
    expired ORM attributes must not be touched afterwards.
    """

    async def event_source():
        result: dict | None = None
        usage = llm.TokenUsage()
        started = time.monotonic()
        try:
            async for event in events:
                if event["type"] == "result":
                    result = event
                else:
                    yield _sse(event)
        except Exception as exc:
            logger.warning("%s failed: %s", log_label, exc)
            # Reset the transaction (it may be poisoned, and it holds the
            # flushed-but-uncommitted conversation) before the usage row;
            # record before yielding so a disconnected client can't skip it.
            await session.rollback()
            await llm.record_usage(
                session,
                user_id=user_id,
                feature="qa",
                config=config,
                duration_ms=llm.ms_since(started),
                status="error",
                error=str(exc),
            )
            yield _sse({"type": "error", "detail": "The LLM request failed"})
            return
        if result is not None:
            run_usage = result.get("usage") or {}
            usage.add(run_usage.get("prompt_tokens"), run_usage.get("completion_tokens"))
        if result is None or not result["content"]:
            await session.rollback()
            await llm.record_usage(
                session,
                user_id=user_id,
                feature="qa",
                config=config,
                usage=usage,
                duration_ms=llm.ms_since(started),
                status="error",
                error="The LLM returned an empty answer",
            )
            yield _sse({"type": "error", "detail": "The LLM returned an empty answer"})
            return
        await llm.record_usage(
            session,
            user_id=user_id,
            feature="qa",
            config=config,
            usage=usage,
            duration_ms=llm.ms_since(started),
        )

        session.add(Message(conversation_id=conversation_id, role="user", content=question))
        assistant = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=result["content"],
            tool_events=result["tool_events"] or None,
        )
        session.add(assistant)
        await session.commit()
        await session.refresh(assistant)
        message = MessageOut.model_validate(assistant).model_dump(mode="json")
        yield _sse({"type": "done", "message": message})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/articles/{article_id}/qa/stream")
async def ask_article_stream(
    article_id: int,
    body: AskIn,
    user: CurrentUser,
    session: DbSession,
):
    """Answer a question about the article, streaming SSE events:

    status | tool_call | tool_result | delta | done | error
    """
    article = await accessible_article(session, user.id, article_id)
    config = await _resolve_llm(session, user)

    text = await ensure_full_text(session, article)
    if is_thin(text):
        hint = (
            " Try web_extract on the article URL first; if that fails too, be "
            "upfront about the limitation."
            if qa_agent.search_enabled()
            else " Be upfront about this limitation."
        )
        text = (
            "[Only the headline and links below are available — the full article "
            "text could not be fetched." + hint + "]\n\n" + text
        )
    conversation = await _get_or_create_conversation(session, user.id, article_id=article.id)
    history = [(m.role, m.content) for m in conversation.messages]
    entities = await _entity_context(session, article.id)
    question = body.content.strip()

    return _qa_stream_response(
        session,
        user_id=user.id,
        config=config,
        conversation_id=conversation.id,
        question=question,
        events=qa_agent.stream_answer(
            title=article.title,
            url=article.url,
            text=clip_for_llm(text),
            author=article.author,
            published_at=article.published_at,
            entities=entities,
            history=history,
            question=question,
            config=config,
        ),
        log_label=f"Q&A stream for article {article.id}",
    )


def _article_hn_id(article: Article) -> str | None:
    canonical = canonical_hn_comments_url(article.comments_url)
    if canonical is None:
        canonical = canonical_hn_comments_url(article.url)
    return canonical.rsplit("=", 1)[-1] if canonical else None


@router.get("/articles/{article_id}/discussion/qa", response_model=list[MessageOut])
async def get_discussion_conversation(
    article_id: int,
    user: CurrentUser,
    session: DbSession,
):
    article = await accessible_article(session, user.id, article_id)
    if _article_hn_id(article) is None:
        raise HTTPException(status_code=404, detail="Discussion not found")
    conversation = await session.scalar(
        select(Conversation)
        .where(
            Conversation.user_id == user.id,
            Conversation.article_id == article_id,
            Conversation.kind == "discussion",
        )
        .options(selectinload(Conversation.messages))
    )
    if conversation is None:
        return []
    return [MessageOut.model_validate(message) for message in conversation.messages]


@router.post("/articles/{article_id}/discussion/qa/stream")
async def ask_discussion_stream(
    article_id: int,
    body: DiscussionAskIn,
    user: CurrentUser,
    session: DbSession,
):
    """Analyze a client-fetched HN snapshot without fetching HN server-side."""
    article = await accessible_article(session, user.id, article_id)
    expected_id = _article_hn_id(article)
    if expected_id is None:
        raise HTTPException(status_code=404, detail="Discussion not found")
    if body.snapshot.discussion_id != expected_id:
        raise HTTPException(status_code=422, detail="Discussion does not match article")
    config = await _resolve_llm(session, user)
    text = clip_for_llm(await ensure_full_text(session, article))
    conversation = await _get_or_create_conversation(
        session, user.id, article_id=article.id, kind="discussion"
    )
    history = [(message.role, message.content) for message in conversation.messages]
    question = body.content.strip()
    snapshot = body.snapshot.model_dump(mode="json")

    return _qa_stream_response(
        session,
        user_id=user.id,
        config=config,
        conversation_id=conversation.id,
        question=question,
        events=qa_agent.stream_discussion_answer(
            title=article.title,
            url=article.url,
            article_text=text,
            snapshot=snapshot,
            history=history,
            question=question,
            config=config,
        ),
        log_label=f"Discussion Q&A for article {article.id}",
    )


# --- project-wide Q&A ---

# How many distinct articles feed the corpus; each contributes its summary,
# not full text (the agent can web_extract a specific URL to read deeper).
PROJECT_QA_ARTICLES = 30


async def _project_corpus(session: AsyncSession, project_id: int, user_id: int) -> str:
    """Titles + summaries + each article's ticket status and discussion thread,
    for the pins the viewer may see — others' private pins are excluded here
    exactly as everywhere else."""
    pins = (
        await session.scalars(
            select(ProjectArticle)
            .where(ProjectArticle.project_id == project_id, visible_pins(user_id))
            .options(selectinload(ProjectArticle.article))
            .order_by(func.coalesce(ProjectArticle.shared_at, ProjectArticle.created_at).desc())
            .limit(PROJECT_QA_ARTICLES * 2)  # headroom for multi-pin articles
        )
    ).all()
    grouped: dict[int, dict] = {}
    order: list[int] = []
    for pin in pins:
        if pin.article_id not in grouped:
            grouped[pin.article_id] = {"article": pin.article, "comments": []}
            order.append(pin.article_id)
    order = order[:PROJECT_QA_ARTICLES]
    statuses = dict(
        (
            await session.execute(
                select(ProjectArticleState.article_id, ProjectArticleState.status).where(
                    ProjectArticleState.project_id == project_id,
                    ProjectArticleState.article_id.in_(order),
                )
            )
        ).all()
    )
    comments = (
        await session.scalars(
            select(ProjectArticleComment)
            .where(
                ProjectArticleComment.project_id == project_id,
                ProjectArticleComment.article_id.in_(order),
            )
            .options(selectinload(ProjectArticleComment.author))
            .order_by(ProjectArticleComment.created_at.asc(), ProjectArticleComment.id.asc())
        )
    ).all()
    for comment in comments:
        text = comment.body
        if comment.link_url:
            text = f"{text} ({comment.link_url})" if text else comment.link_url
        grouped[comment.article_id]["comments"].append(f"@{comment.author.username}: {text}")
    blocks = []
    for article_id in order:
        entry = grouped[article_id]
        article = entry["article"]
        lines = [f"### {article.title}", f"URL: {article.url}"]
        if article.published_at:
            lines.append(f"Published: {article.published_at.date().isoformat()}")
        if statuses.get(article_id, "open") != "open":
            lines.append(f"Status: {statuses[article_id]}")
        if entry["comments"]:
            lines.append("Discussion: " + " | ".join(entry["comments"]))
        summary = article.summary_medium or article.summary or article.excerpt
        lines.append(summary or "(no summary available — web_extract the URL to read it)")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


@router.get("/projects/{project_id}/qa", response_model=list[MessageOut])
async def get_project_conversation(
    project_id: int,
    user: CurrentUser,
    session: DbSession,
):
    await _member_or_404(session, project_id, user.id)
    conversation = await session.scalar(
        select(Conversation)
        .where(Conversation.user_id == user.id, Conversation.project_id == project_id)
        .options(selectinload(Conversation.messages))
    )
    if conversation is None:
        return []
    return [MessageOut.model_validate(m) for m in conversation.messages]


@router.post("/projects/{project_id}/qa/stream")
async def ask_project_stream(
    project_id: int,
    body: AskIn,
    user: CurrentUser,
    session: DbSession,
):
    """Answer a question across the project's collection; same SSE event
    stream as the per-article endpoint."""
    await _member_or_404(session, project_id, user.id)
    project = await session.get(Project, project_id)
    config = await _resolve_llm(session, user)

    corpus = await _project_corpus(session, project_id, user.id)
    if not corpus:
        raise HTTPException(status_code=422, detail="Nothing in this project to ask about yet")
    conversation = await _get_or_create_conversation(session, user.id, project_id=project_id)
    history = [(m.role, m.content) for m in conversation.messages]
    question = body.content.strip()

    return _qa_stream_response(
        session,
        user_id=user.id,
        config=config,
        conversation_id=conversation.id,
        question=question,
        events=qa_agent.stream_project_answer(
            name=project.name,
            description=project.description,
            corpus=corpus,
            history=history,
            question=question,
            config=config,
        ),
        log_label=f"Q&A stream for project {project_id}",
    )
