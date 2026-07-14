"""Entity pages: one person / org / product / repo / paper, plus every
visible article from the user's feeds that links or mentions it."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..enrichers import badge_for
from ..models import ArticleEntity, Entity, User
from ..schemas import EntityPageOut
from ..security import get_current_user
from .articles import Article, _related_scope, to_list_item

router = APIRouter(prefix="/entities", tags=["entities"])

ENTITY_PAGE_LIMIT = 100


@router.get("/{entity_id}", response_model=EntityPageOut)
async def get_entity(
    entity_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    entity = await session.get(Entity, entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    stmt = (
        _related_scope(user.id)
        .join(
            ArticleEntity,
            (ArticleEntity.article_id == Article.id) & (ArticleEntity.entity_id == entity_id),
        )
        .order_by(Article.published_at.desc().nulls_last(), Article.id.desc())
        .limit(ENTITY_PAGE_LIMIT)
    )
    rows = (await session.execute(stmt)).all()
    data = entity.data or {}
    badge = badge_for(entity.kind, data)
    return EntityPageOut(
        id=entity.id,
        kind=entity.kind,
        key=entity.canonical_key,
        url=entity.url,
        name=str(badge.get("label") or data.get("name") or entity.canonical_key),
        badge=badge,
        articles=[
            to_list_item(article, title or url, state) for article, title, url, state in rows
        ],
    )
