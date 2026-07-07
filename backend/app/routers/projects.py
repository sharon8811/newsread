import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from typing import Literal

from ..db import get_session
from ..models import (
    Article,
    Feed,
    Project,
    ProjectArticle,
    ProjectMember,
    User,
    UserArticleState,
)
from ..schemas import (
    ArticleProjectStatus,
    ProjectArticleAddIn,
    ProjectArticleOut,
    ProjectArticleUpdateIn,
    ProjectCreateIn,
    ProjectMemberAddIn,
    ProjectMemberOut,
    ProjectOut,
    ProjectUpdateIn,
    UserPublic,
)
from ..security import get_current_user
from .articles import _entities_for_articles, _to_badge, to_list_item, user_can_access
from .shares import _states_for

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


def visible_pins(user_id: int):
    """THE project visibility predicate: a pin is visible if it was published
    to the project or the viewer added it. Every query that touches
    ProjectArticle — listings, counts, search, Q&A corpora — must include it."""
    return or_(ProjectArticle.is_shared.is_(True), ProjectArticle.added_by_user_id == user_id)


_project_load_options = (
    selectinload(Project.members).selectinload(ProjectMember.user),
    selectinload(Project.owner),
)


async def _membership(session: AsyncSession, project_id: int, user_id: int) -> ProjectMember | None:
    return await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
        )
    )


async def _member_or_404(session: AsyncSession, project_id: int, user_id: int) -> ProjectMember:
    membership = await _membership(session, project_id, user_id)
    if membership is None:
        # Non-members learn nothing, not even that the project exists.
        raise HTTPException(status_code=404, detail="Project not found")
    return membership


async def _visible_counts(session: AsyncSession, project_ids: list[int], user_id: int) -> dict[int, int]:
    if not project_ids:
        return {}
    rows = await session.execute(
        # Distinct articles, not pins: two members pinning the same article is
        # one article, matching the grouped card the project page renders.
        select(ProjectArticle.project_id, func.count(func.distinct(ProjectArticle.article_id)))
        .where(ProjectArticle.project_id.in_(project_ids), visible_pins(user_id))
        .group_by(ProjectArticle.project_id)
    )
    return dict(rows.all())


def _project_out(project: Project, my_role: str, article_count: int) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        owner=UserPublic.model_validate(project.owner),
        my_role=my_role,
        members=[
            ProjectMemberOut(user=UserPublic.model_validate(m.user), role=m.role)
            for m in sorted(project.members, key=lambda m: (m.role != "owner", m.created_at, m.id))
        ],
        article_count=article_count,
        created_at=project.created_at,
    )


async def _project_response(
    session: AsyncSession, project_id: int, user_id: int, my_role: str
) -> ProjectOut:
    """Standard epilogue: reload with members/owner and viewer-visible count."""
    project = await session.scalar(
        select(Project).where(Project.id == project_id).options(*_project_load_options)
    )
    counts = await _visible_counts(session, [project_id], user_id)
    return _project_out(project, my_role, counts.get(project_id, 0))


def _pin_out(pin: ProjectArticle, feed_title: str, state, entities=None) -> ProjectArticleOut:
    return ProjectArticleOut(
        id=pin.id,
        project_id=pin.project_id,
        article=to_list_item(pin.article, feed_title, state, entities),
        added_by=UserPublic.model_validate(pin.added_by),
        is_shared=pin.is_shared,
        shared_at=pin.shared_at,
        note=pin.note,
        created_at=pin.created_at,
    )


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(
    body: ProjectCreateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    project = Project(owner_id=user.id, name=body.name, description=body.description.strip())
    project.members = [ProjectMember(user_id=user.id, role="owner")]
    session.add(project)
    await session.commit()
    return await _project_response(session, project.id, user.id, "owner")


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(Project, ProjectMember.role)
            .join(
                ProjectMember,
                and_(ProjectMember.project_id == Project.id, ProjectMember.user_id == user.id),
            )
            .options(*_project_load_options)
            .order_by(Project.created_at.desc())
        )
    ).all()
    counts = await _visible_counts(session, [p.id for p, _ in rows], user.id)
    return [_project_out(p, role, counts.get(p.id, 0)) for p, role in rows]


# Literal path defined before /{project_id} so "article" never parses as an id.
@router.get("/article/{article_id}", response_model=list[ArticleProjectStatus])
async def article_project_status(
    article_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Picker state: for each of my projects, my own pin and whether someone
    else already shared this article there."""
    mine = ProjectArticle.__table__.alias("mine")
    others = ProjectArticle.__table__.alias("others")
    rows = (
        await session.execute(
            select(Project.id, Project.name, mine.c.id, mine.c.is_shared, func.count(others.c.id))
            .join(
                ProjectMember,
                and_(ProjectMember.project_id == Project.id, ProjectMember.user_id == user.id),
            )
            .outerjoin(
                mine,
                and_(
                    mine.c.project_id == Project.id,
                    mine.c.article_id == article_id,
                    mine.c.added_by_user_id == user.id,
                ),
            )
            .outerjoin(
                others,
                and_(
                    others.c.project_id == Project.id,
                    others.c.article_id == article_id,
                    others.c.added_by_user_id != user.id,
                    others.c.is_shared.is_(True),
                ),
            )
            .group_by(Project.id, Project.name, mine.c.id, mine.c.is_shared)
            .order_by(Project.created_at.desc())
        )
    ).all()
    return [
        ArticleProjectStatus(
            project_id=pid,
            project_name=name,
            project_article_id=pin_id,
            is_shared=pin_shared,
            shared_by_others=bool(other_count),
        )
        for pid, name, pin_id, pin_shared, other_count in rows
    ]


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    membership = await _member_or_404(session, project_id, user.id)
    return await _project_response(session, project_id, user.id, membership.role)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    body: ProjectUpdateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    membership = await _member_or_404(session, project_id, user.id)
    if membership.role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can edit the project")
    project = await session.get(Project, project_id)
    updates = body.model_dump(exclude_unset=True)
    if updates.get("name") is not None:
        project.name = updates["name"]
    if updates.get("description") is not None:
        project.description = updates["description"].strip()
    await session.commit()
    return await _project_response(session, project_id, user.id, "owner")


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    membership = await _member_or_404(session, project_id, user.id)
    if membership.role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can delete the project")
    await session.delete(await session.get(Project, project_id))
    await session.commit()


@router.post("/{project_id}/members", response_model=ProjectOut, status_code=201)
async def add_member(
    project_id: int,
    body: ProjectMemberAddIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    membership = await _member_or_404(session, project_id, user.id)
    if membership.role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can invite members")
    username = body.username.strip().lstrip("@")
    invitee = await session.scalar(
        select(User).where(func.lower(User.username) == username.lower())
    )
    if invitee is None:
        raise HTTPException(status_code=404, detail=f"No such user: {username}")
    if await _membership(session, project_id, invitee.id):
        raise HTTPException(status_code=409, detail="Already a member")
    session.add(ProjectMember(project_id=project_id, user_id=invitee.id, role="member"))
    await session.commit()
    return await _project_response(session, project_id, user.id, "owner")


@router.delete("/{project_id}/members/{member_user_id}", status_code=204)
async def remove_member(
    project_id: int,
    member_user_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Owner removes anyone (but themselves); a member removes only themselves
    (leaving). A departing member's shared pins stay — the group has already
    built on them; their private pins simply remain invisible to everyone."""
    membership = await _member_or_404(session, project_id, user.id)
    if member_user_id == user.id:
        if membership.role == "owner":
            raise HTTPException(
                status_code=422, detail="The owner cannot leave; delete the project instead"
            )
        target = membership
    else:
        if membership.role != "owner":
            raise HTTPException(status_code=403, detail="Only the owner can remove members")
        target = await _membership(session, project_id, member_user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Member not found")
    await session.delete(target)
    await session.commit()


_pin_load_options = (
    selectinload(ProjectArticle.article).selectinload(Article.feed),
    selectinload(ProjectArticle.added_by),
)


@router.get("/{project_id}/articles", response_model=list[ProjectArticleOut])
async def list_project_articles(
    project_id: int,
    scope: Literal["all", "shared", "mine"] = "all",
    limit: int = Query(default=200, ge=1, le=500),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await _member_or_404(session, project_id, user.id)
    stmt = (
        select(ProjectArticle)
        .where(ProjectArticle.project_id == project_id, visible_pins(user.id))
        .options(*_pin_load_options)
        # Publish time drives the feed; private pins fall back to added time.
        .order_by(func.coalesce(ProjectArticle.shared_at, ProjectArticle.created_at).desc())
        .limit(limit)
    )
    if scope == "shared":
        stmt = stmt.where(ProjectArticle.is_shared.is_(True))
    elif scope == "mine":
        stmt = stmt.where(ProjectArticle.added_by_user_id == user.id)
    pins = (await session.scalars(stmt)).all()
    article_ids = [p.article_id for p in pins]
    states = await _states_for(session, user.id, article_ids)
    entity_map = await _entities_for_articles(session, article_ids)
    return [
        _pin_out(
            pin,
            pin.article.feed.title or pin.article.feed.url,
            states.get(pin.article_id),
            [_to_badge(link, entity) for link, entity in entity_map.get(pin.article_id, [])],
        )
        for pin in pins
    ]


@router.post("/{project_id}/articles", response_model=ProjectArticleOut, status_code=201)
async def add_project_article(
    project_id: int,
    body: ProjectArticleAddIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await _member_or_404(session, project_id, user.id)
    article = await session.get(Article, body.article_id)
    if article is None or not await user_can_access(session, user.id, article):
        raise HTTPException(status_code=404, detail="Article not found")
    note = body.note.strip() if body.note else None
    # Atomic insert: a concurrent duplicate (double-click, second tab) resolves
    # to "already added" instead of a unique-constraint 500.
    pin_id = await session.scalar(
        pg_insert(ProjectArticle)
        .values(
            project_id=project_id,
            article_id=article.id,
            added_by_user_id=user.id,
            is_shared=body.is_shared,
            shared_at=datetime.now(timezone.utc) if body.is_shared else None,
            note=note or None,
        )
        .on_conflict_do_nothing(index_elements=["project_id", "article_id", "added_by_user_id"])
        .returning(ProjectArticle.id)
    )
    if pin_id is None:
        raise HTTPException(status_code=409, detail="You already added this article")
    await session.commit()
    pin = await session.scalar(
        select(ProjectArticle).where(ProjectArticle.id == pin_id).options(*_pin_load_options)
    )
    states = await _states_for(session, user.id, [article.id])
    return _pin_out(pin, pin.article.feed.title or pin.article.feed.url, states.get(article.id))


async def _own_pin_or_error(
    session: AsyncSession, project_id: int, pin_id: int, user_id: int
) -> ProjectArticle:
    """Load a pin for mutation by its adder. Others get 404 for private pins
    (their existence is itself private) and 403 for shared ones."""
    pin = await session.scalar(
        select(ProjectArticle)
        .where(ProjectArticle.id == pin_id, ProjectArticle.project_id == project_id)
        .options(*_pin_load_options)
    )
    if pin is None or (not pin.is_shared and pin.added_by_user_id != user_id):
        raise HTTPException(status_code=404, detail="Not found in this project")
    return pin


@router.patch("/{project_id}/articles/{pin_id}", response_model=ProjectArticleOut)
async def update_project_article(
    project_id: int,
    pin_id: int,
    body: ProjectArticleUpdateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await _member_or_404(session, project_id, user.id)
    pin = await _own_pin_or_error(session, project_id, pin_id, user.id)
    if pin.added_by_user_id != user.id:
        raise HTTPException(status_code=403, detail="Only the adder can edit this")
    updates = body.model_dump(exclude_unset=True)
    if "is_shared" in updates and updates["is_shared"] is not None:
        if updates["is_shared"] and not pin.is_shared:
            pin.shared_at = datetime.now(timezone.utc)
        elif not updates["is_shared"]:
            pin.shared_at = None
        pin.is_shared = updates["is_shared"]
    if "note" in updates:
        note = updates["note"].strip() if updates["note"] else None
        pin.note = note or None
    await session.commit()
    states = await _states_for(session, user.id, [pin.article_id])
    return _pin_out(pin, pin.article.feed.title or pin.article.feed.url, states.get(pin.article_id))


@router.delete("/{project_id}/articles/{pin_id}", status_code=204)
async def remove_project_article(
    project_id: int,
    pin_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    membership = await _member_or_404(session, project_id, user.id)
    pin = await _own_pin_or_error(session, project_id, pin_id, user.id)
    if pin.added_by_user_id != user.id and not (membership.role == "owner" and pin.is_shared):
        raise HTTPException(status_code=403, detail="Only the adder or the owner can remove this")
    await session.delete(pin)
    await session.commit()


@router.delete("/{project_id}/articles/by-article/{article_id}", status_code=204)
async def remove_article_pins(
    project_id: int,
    article_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Remove every pin of this article the caller may remove, in one
    transaction: their own pin, plus all shared pins when they're the owner.
    Backs the card's single remove action — no client-side DELETE fan-out."""
    membership = await _member_or_404(session, project_id, user.id)
    pins = (
        await session.scalars(
            select(ProjectArticle).where(
                ProjectArticle.project_id == project_id,
                ProjectArticle.article_id == article_id,
                visible_pins(user.id),
            )
        )
    ).all()
    if not pins:
        raise HTTPException(status_code=404, detail="Not found in this project")
    removable = [
        p
        for p in pins
        if p.added_by_user_id == user.id or (membership.role == "owner" and p.is_shared)
    ]
    if not removable:
        raise HTTPException(status_code=403, detail="Only the adder or the owner can remove this")
    for pin in removable:
        await session.delete(pin)
    await session.commit()
