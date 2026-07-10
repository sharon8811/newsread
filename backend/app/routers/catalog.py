from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import CatalogEntry, Feed, Subscription, User
from ..schemas import CatalogCategoryOut, CatalogEntryOut
from ..security import get_current_user

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("", response_model=list[CatalogEntryOut])
async def browse_catalog(
    q: str | None = Query(default=None, max_length=120),
    category: str | None = Query(default=None, max_length=64),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """The curated directory, optionally narrowed by search text and/or
    category. Subscribing itself goes through POST /feeds with the entry's
    url; the joins here only report whether the viewer already did."""
    stmt = (
        select(CatalogEntry, Subscription.feed_id)
        .outerjoin(Feed, Feed.url == CatalogEntry.url)
        .outerjoin(
            Subscription,
            and_(Subscription.feed_id == Feed.id, Subscription.user_id == user.id),
        )
        .order_by(CatalogEntry.category, func.lower(CatalogEntry.title))
    )
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                CatalogEntry.title.ilike(pattern),
                CatalogEntry.description.ilike(pattern),
                CatalogEntry.category.ilike(pattern),
            )
        )
    if category:
        stmt = stmt.where(CatalogEntry.category == category)

    rows = await session.execute(stmt)
    return [
        CatalogEntryOut(
            id=entry.id,
            url=entry.url,
            title=entry.title,
            description=entry.description,
            site_url=entry.site_url,
            category=entry.category,
            feed_id=feed_id,
            subscribed=feed_id is not None,
        )
        for entry, feed_id in rows
    ]


@router.get("/categories", response_model=list[CatalogCategoryOut])
async def list_categories(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    rows = await session.execute(
        select(CatalogEntry.category, func.count())
        .group_by(CatalogEntry.category)
        .order_by(CatalogEntry.category)
    )
    return [CatalogCategoryOut(name=name, count=count) for name, count in rows]
