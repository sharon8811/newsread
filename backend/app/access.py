"""Cross-router article visibility.

An article is visible to a user when they subscribe to its feed, someone
shared it with them, or it's pinned to a project they belong to. Routers used
to copy this guard around; they all funnel through accessible_article now.
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Article, ProjectArticle, ProjectMember, Share, ShareRecipient, Subscription


async def user_can_access(session: AsyncSession, user_id: int, article: Article) -> bool:
    subscribed = await session.scalar(
        select(Subscription.id).where(
            Subscription.user_id == user_id, Subscription.feed_id == article.feed_id
        )
    )
    if subscribed:
        return True
    shared = await session.scalar(
        select(ShareRecipient.id)
        .join(Share, Share.id == ShareRecipient.share_id)
        .where(ShareRecipient.to_user_id == user_id, Share.article_id == article.id)
    )
    if shared is not None:
        return True
    # Pinned to a project the user belongs to (their own pin, or a shared one).
    # Function-level import: routers.projects imports app modules at load time.
    from .routers.projects import visible_pins

    pinned = await session.scalar(
        select(ProjectArticle.id)
        .join(ProjectMember, ProjectMember.project_id == ProjectArticle.project_id)
        .where(
            ProjectMember.user_id == user_id,
            ProjectArticle.article_id == article.id,
            visible_pins(user_id),
        )
    )
    return pinned is not None


async def accessible_article(session: AsyncSession, user_id: int, article_id: int) -> Article:
    """The article, or the same 404 whether it's missing or merely invisible —
    existence of other users' articles is deliberately not leaked."""
    article = await session.get(Article, article_id)
    if article is None or not await user_can_access(session, user_id, article):
        raise HTTPException(status_code=404, detail="Article not found")
    return article
