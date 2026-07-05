import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import llm, qa_agent
from ..config import settings
from ..db import get_session
from ..enrichers import badge_for
from ..extractor import clip_for_llm, ensure_full_text, is_thin
from ..models import Article, ArticleEntity, Conversation, Entity, Message, User
from ..schemas import AiStatusOut, AskIn, MessageOut, SummaryOut
from ..security import get_current_user
from ..summarizer import ThinContentError, generate_summaries
from .articles import user_can_access

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
