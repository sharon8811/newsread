import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import llm
from ..config import settings
from ..db import get_session
from ..extractor import clip_for_llm, ensure_full_text, is_thin
from ..models import Article, Conversation, Message, User
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
        configured=llm.is_configured(), model=settings.openai_model or None
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


@router.post("/articles/{article_id}/qa", response_model=MessageOut)
async def ask_article(
    article_id: int,
    body: AskIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    article = await _accessible_article(session, user, article_id)
    _require_llm()

    text = await ensure_full_text(session, article)
    if is_thin(text):
        text = (
            "[Only the headline and links below are available — the full article "
            "text could not be fetched. Be upfront about this limitation.]\n\n" + text
        )
    conversation = await _get_or_create_conversation(session, user.id, article.id)
    history = [(m.role, m.content) for m in conversation.messages]

    question = body.content.strip()
    try:
        reply = await llm.answer(article.title, clip_for_llm(text), history, question)
    except Exception as exc:
        logger.warning("Q&A failed for article %s: %s", article.id, exc)
        raise HTTPException(status_code=502, detail="The LLM request failed")
    if not reply:
        raise HTTPException(status_code=502, detail="The LLM returned an empty answer")

    session.add(Message(conversation_id=conversation.id, role="user", content=question))
    assistant = Message(conversation_id=conversation.id, role="assistant", content=reply)
    session.add(assistant)
    await session.commit()
    await session.refresh(assistant)
    return MessageOut.model_validate(assistant)
