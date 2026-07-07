import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import llm, qa_agent
from ..config import settings
from ..db import get_session
from ..enrichers import badge_for
from ..extractor import clip_for_llm, ensure_full_text, is_thin
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
from ..schemas import AiStatusOut, AskIn, MessageOut, SummaryOut
from ..security import get_current_user
from ..summarizer import ThinContentError, generate_summaries
from .articles import user_can_access
from .projects import _member_or_404, visible_pins

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ai"])


async def _accessible_article(
    session: AsyncSession, user: User, article_id: int
) -> Article:
    article = await session.get(Article, article_id)
    if article is None or not await user_can_access(session, user.id, article):
        raise HTTPException(status_code=404, detail="Article not found")
    return article


def _require_llm() -> None:
    if not llm.is_configured():
        raise HTTPException(
            status_code=503,
            detail="No LLM is configured. Set OPENAI_API_KEY, OPENAI_BASE_URL and OPENAI_MODEL.",
        )


@router.get("/ai/status", response_model=AiStatusOut)
async def ai_status(user: User = Depends(get_current_user)):
    return AiStatusOut(
        configured=llm.is_configured(),
        model=settings.openai_model or None,
        search=qa_agent.search_enabled(),
        search_provider=qa_agent.search_provider(),
    )


@router.post("/articles/{article_id}/summarize", response_model=SummaryOut)
async def summarize_article(
    article_id: int,
    force: bool = False,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    article = await _accessible_article(session, user, article_id)
    if article.summary and article.summary_short and not force:
        return _summary_out(article)
    _require_llm()

    try:
        await generate_summaries(session, article)
    except ThinContentError:
        # Summarizing a headline stub just makes the model invent details.
        raise HTTPException(
            status_code=422,
            detail="Couldn't fetch the article's full text — the site may block automated readers. Open the original instead.",
        )
    except Exception as exc:
        logger.warning("Summarization failed for article %s: %s", article.id, exc)
        raise HTTPException(status_code=502, detail="The LLM request failed")
    return _summary_out(article)


def _summary_out(article: Article) -> SummaryOut:
    return SummaryOut(
        summary=article.summary,
        summary_short=article.summary_short,
        summary_medium=article.summary_medium,
        model=article.summary_model,
        generated_at=article.summary_generated_at,
    )


async def _get_or_create_conversation(
    session: AsyncSession, user_id: int, article_id: int
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation)
        .where(Conversation.user_id == user_id, Conversation.article_id == article_id)
        .options(selectinload(Conversation.messages))
    )
    if conversation is None:
        # messages is set while the object is transient — an assignment after
        # flush would trigger a sync lazy-load, which async sessions forbid.
        conversation = Conversation(user_id=user_id, article_id=article_id, messages=[])
        session.add(conversation)
        await session.flush()
    return conversation


@router.get("/articles/{article_id}/qa", response_model=list[MessageOut])
async def get_conversation(
    article_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await _accessible_article(session, user, article_id)
    conversation = await session.scalar(
        select(Conversation)
        .where(Conversation.user_id == user.id, Conversation.article_id == article_id)
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


@router.post("/articles/{article_id}/qa/stream")
async def ask_article_stream(
    article_id: int,
    body: AskIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Answer a question about the article, streaming SSE events:

    status | tool_call | tool_result | delta | done | error
    """
    article = await _accessible_article(session, user, article_id)
    _require_llm()

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
    conversation = await _get_or_create_conversation(session, user.id, article.id)
    history = [(m.role, m.content) for m in conversation.messages]
    entities = await _entity_context(session, article.id)
    question = body.content.strip()

    async def event_source():
        result: dict | None = None
        try:
            async for event in qa_agent.stream_answer(
                title=article.title,
                url=article.url,
                text=clip_for_llm(text),
                published_at=article.published_at,
                entities=entities,
                history=history,
                question=question,
            ):
                if event["type"] == "result":
                    result = event
                else:
                    yield _sse(event)
        except Exception as exc:
            logger.warning("Q&A stream failed for article %s: %s", article.id, exc)
            yield _sse({"type": "error", "detail": "The LLM request failed"})
            return
        if result is None or not result["content"]:
            yield _sse({"type": "error", "detail": "The LLM returned an empty answer"})
            return

        session.add(
            Message(conversation_id=conversation.id, role="user", content=question)
        )
        assistant = Message(
            conversation_id=conversation.id,
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
            .order_by(
                func.coalesce(ProjectArticle.shared_at, ProjectArticle.created_at).desc()
            )
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


async def _get_or_create_project_conversation(
    session: AsyncSession, user_id: int, project_id: int
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation)
        .where(Conversation.user_id == user_id, Conversation.project_id == project_id)
        .options(selectinload(Conversation.messages))
    )
    if conversation is None:
        conversation = Conversation(user_id=user_id, project_id=project_id, messages=[])
        session.add(conversation)
        await session.flush()
    return conversation


@router.get("/projects/{project_id}/qa", response_model=list[MessageOut])
async def get_project_conversation(
    project_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
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
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Answer a question across the project's collection; same SSE event
    stream as the per-article endpoint."""
    await _member_or_404(session, project_id, user.id)
    project = await session.get(Project, project_id)
    _require_llm()

    corpus = await _project_corpus(session, project_id, user.id)
    if not corpus:
        raise HTTPException(
            status_code=422, detail="Nothing in this project to ask about yet"
        )
    conversation = await _get_or_create_project_conversation(session, user.id, project_id)
    history = [(m.role, m.content) for m in conversation.messages]
    question = body.content.strip()

    async def event_source():
        result: dict | None = None
        try:
            async for event in qa_agent.stream_project_answer(
                name=project.name,
                description=project.description,
                corpus=corpus,
                history=history,
                question=question,
            ):
                if event["type"] == "result":
                    result = event
                else:
                    yield _sse(event)
        except Exception as exc:
            logger.warning("Q&A stream failed for project %s: %s", project_id, exc)
            yield _sse({"type": "error", "detail": "The LLM request failed"})
            return
        if result is None or not result["content"]:
            yield _sse({"type": "error", "detail": "The LLM returned an empty answer"})
            return

        session.add(
            Message(conversation_id=conversation.id, role="user", content=question)
        )
        assistant = Message(
            conversation_id=conversation.id,
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
