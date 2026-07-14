import logging
import math
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import db
from ..access import accessible_article
from ..config import settings
from ..deps import CurrentUser, DbSession
from ..models import (
    Article,
    ArticleEmbedding,
    Project,
    ProjectArticle,
    ProjectArticleComment,
    ProjectArticleState,
    ProjectMember,
    User,
)
from ..queue import enqueue
from ..schemas import (
    ArticleProjectStatus,
    ProjectArticleAddIn,
    ProjectArticleOut,
    ProjectArticleStateOut,
    ProjectArticleStatusIn,
    ProjectArticleUpdateIn,
    ProjectCommentIn,
    ProjectCommentOut,
    ProjectCreateIn,
    ProjectMemberAddIn,
    ProjectMemberOut,
    ProjectMembershipIn,
    ProjectOut,
    ProjectUpdateIn,
    UserPublic,
)
from .articles import _entities_for_articles, _to_badge, to_list_item
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


async def _visible_counts(
    session: AsyncSession, project_ids: list[int], user_id: int
) -> dict[int, int]:
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


async def _unseen_counts(
    session: AsyncSession, project_ids: list[int], user_id: int
) -> dict[int, int]:
    """Articles other members published after the viewer's last visit."""
    if not project_ids:
        return {}
    rows = await session.execute(
        select(ProjectArticle.project_id, func.count(func.distinct(ProjectArticle.article_id)))
        .join(
            ProjectMember,
            and_(
                ProjectMember.project_id == ProjectArticle.project_id,
                ProjectMember.user_id == user_id,
            ),
        )
        .where(
            ProjectArticle.project_id.in_(project_ids),
            ProjectArticle.is_shared.is_(True),
            ProjectArticle.added_by_user_id != user_id,
            or_(
                ProjectMember.last_visited_at.is_(None),
                ProjectArticle.shared_at > ProjectMember.last_visited_at,
            ),
        )
        .group_by(ProjectArticle.project_id)
    )
    return dict(rows.all())


def _project_out(
    project: Project, membership: ProjectMember, article_count: int, unseen_count: int
) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        owner=UserPublic.model_validate(project.owner),
        my_role=membership.role,
        members=[
            ProjectMemberOut(user=UserPublic.model_validate(m.user), role=m.role)
            for m in sorted(project.members, key=lambda m: (m.role != "owner", m.created_at, m.id))
        ],
        article_count=article_count,
        unseen_count=unseen_count,
        is_muted=membership.is_muted,
        created_at=project.created_at,
    )


async def _project_response(session: AsyncSession, project_id: int, user_id: int) -> ProjectOut:
    """Standard epilogue: reload with members/owner plus the viewer's counts."""
    project = await session.scalar(
        select(Project).where(Project.id == project_id).options(*_project_load_options)
    )
    membership = next(m for m in project.members if m.user_id == user_id)
    counts = await _visible_counts(session, [project_id], user_id)
    unseen = await _unseen_counts(session, [project_id], user_id)
    return _project_out(project, membership, counts.get(project_id, 0), unseen.get(project_id, 0))


def _pin_out(
    pin: ProjectArticle, feed_title: str, state, entities=None, ticket=None, comment_count=0
) -> ProjectArticleOut:
    return ProjectArticleOut(
        id=pin.id,
        project_id=pin.project_id,
        article=to_list_item(pin.article, feed_title, state, entities),
        added_by=UserPublic.model_validate(pin.added_by),
        is_shared=pin.is_shared,
        shared_at=pin.shared_at,
        created_at=pin.created_at,
        status=ticket.status if ticket else "open",
        status_updated_by=UserPublic.model_validate(ticket.updated_by) if ticket else None,
        comment_count=comment_count,
    )


async def _ticket_info(
    session: AsyncSession, project_id: int, article_ids: list[int]
) -> tuple[dict[int, ProjectArticleState], dict[int, int]]:
    """Ticket states and comment counts by article id, both per-article (not
    per-pin) — every pin of an article reports the same shared ticket."""
    if not article_ids:
        return {}, {}
    states = (
        await session.scalars(
            select(ProjectArticleState)
            .where(
                ProjectArticleState.project_id == project_id,
                ProjectArticleState.article_id.in_(article_ids),
            )
            .options(selectinload(ProjectArticleState.updated_by))
        )
    ).all()
    counts = dict(
        (
            await session.execute(
                select(ProjectArticleComment.article_id, func.count())
                .where(
                    ProjectArticleComment.project_id == project_id,
                    ProjectArticleComment.article_id.in_(article_ids),
                )
                .group_by(ProjectArticleComment.article_id)
            )
        ).all()
    )
    return {s.article_id: s for s in states}, counts


def _comment_out(comment: ProjectArticleComment, author: User | None = None) -> ProjectCommentOut:
    return ProjectCommentOut(
        id=comment.id,
        author=UserPublic.model_validate(author or comment.author),
        body=comment.body,
        link_url=comment.link_url,
        created_at=comment.created_at,
    )


async def _thread_or_404(
    session: AsyncSession, project_id: int, article_id: int, user_id: int
) -> None:
    """A thread (and its ticket) exists for the viewer iff they can see at
    least one pin of the article in the project — it rides the grouped card."""
    pin_id = await session.scalar(
        select(ProjectArticle.id)
        .where(
            ProjectArticle.project_id == project_id,
            ProjectArticle.article_id == article_id,
            visible_pins(user_id),
        )
        .limit(1)
    )
    if pin_id is None:
        raise HTTPException(status_code=404, detail="Not found in this project")


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(
    body: ProjectCreateIn,
    user: CurrentUser,
    session: DbSession,
):
    project = Project(owner_id=user.id, name=body.name, description=body.description.strip())
    project.members = [ProjectMember(user_id=user.id, role="owner")]
    session.add(project)
    await session.commit()
    return await _project_response(session, project.id, user.id)


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    user: CurrentUser,
    session: DbSession,
):
    rows = (
        await session.execute(
            select(Project, ProjectMember)
            .join(
                ProjectMember,
                and_(ProjectMember.project_id == Project.id, ProjectMember.user_id == user.id),
            )
            .options(*_project_load_options)
            .order_by(Project.created_at.desc())
        )
    ).all()
    project_ids = [p.id for p, _ in rows]
    counts = await _visible_counts(session, project_ids, user.id)
    unseen = await _unseen_counts(session, project_ids, user.id)
    return [
        _project_out(p, membership, counts.get(p.id, 0), unseen.get(p.id, 0))
        for p, membership in rows
    ]


# A project is suggested when the article's embedding is at least this close
# to the centroid of the project's recent pins.
SUGGEST_MIN_SIMILARITY = 0.55
# Newest pins per project that shape its centroid — projects drift over time.
SUGGEST_PINS_PER_PROJECT = 50


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


async def _suggested_project_id(session: AsyncSession, article_id: int, user_id: int) -> int | None:
    """The single project whose recent pins are most similar to this article,
    if any clears the threshold. Purely reads stored vectors — no LLM calls."""
    if not db.vector_enabled:
        return None
    article_vector = await session.scalar(
        select(ArticleEmbedding.embedding).where(
            ArticleEmbedding.article_id == article_id,
            ArticleEmbedding.model == settings.openai_embedding_model,
        )
    )
    if article_vector is None:
        return None
    rows = (
        await session.execute(
            select(ProjectArticle.project_id, ArticleEmbedding.embedding)
            .join(
                ProjectMember,
                and_(
                    ProjectMember.project_id == ProjectArticle.project_id,
                    ProjectMember.user_id == user_id,
                ),
            )
            .join(
                ArticleEmbedding,
                and_(
                    ArticleEmbedding.article_id == ProjectArticle.article_id,
                    ArticleEmbedding.model == settings.openai_embedding_model,
                ),
            )
            .where(visible_pins(user_id), ProjectArticle.article_id != article_id)
            .order_by(func.coalesce(ProjectArticle.shared_at, ProjectArticle.created_at).desc())
        )
    ).all()
    vectors_by_project: dict[int, list[list[float]]] = {}
    for project_id, vector in rows:
        bucket = vectors_by_project.setdefault(project_id, [])
        if len(bucket) < SUGGEST_PINS_PER_PROJECT:
            bucket.append([float(x) for x in vector])
    target = [float(x) for x in article_vector]
    best_id, best_similarity = None, SUGGEST_MIN_SIMILARITY
    for project_id, vectors in vectors_by_project.items():
        centroid = [sum(dim) / len(vectors) for dim in zip(*vectors, strict=False)]
        similarity = _cosine(target, centroid)
        if similarity >= best_similarity:
            best_id, best_similarity = project_id, similarity
    return best_id


# Literal path defined before /{project_id} so "article" never parses as an id.
@router.get("/article/{article_id}", response_model=list[ArticleProjectStatus])
async def article_project_status(
    article_id: int,
    user: CurrentUser,
    session: DbSession,
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
    suggested_id = await _suggested_project_id(session, article_id, user.id)
    return [
        ArticleProjectStatus(
            project_id=pid,
            project_name=name,
            project_article_id=pin_id,
            is_shared=pin_shared,
            shared_by_others=bool(other_count),
            # Suggesting a project the article is already pinned to is noise.
            suggested=pid == suggested_id and pin_id is None,
        )
        for pid, name, pin_id, pin_shared, other_count in rows
    ]


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int,
    user: CurrentUser,
    session: DbSession,
):
    await _member_or_404(session, project_id, user.id)
    return await _project_response(session, project_id, user.id)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    body: ProjectUpdateIn,
    user: CurrentUser,
    session: DbSession,
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
    return await _project_response(session, project_id, user.id)


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: int,
    user: CurrentUser,
    session: DbSession,
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
    user: CurrentUser,
    session: DbSession,
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
    return await _project_response(session, project_id, user.id)


@router.delete("/{project_id}/members/{member_user_id}", status_code=204)
async def remove_member(
    project_id: int,
    member_user_id: int,
    user: CurrentUser,
    session: DbSession,
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


@router.post("/{project_id}/visit", status_code=204)
async def visit_project(
    project_id: int,
    user: CurrentUser,
    session: DbSession,
):
    """The project page reports each open; unseen counts measure against it."""
    membership = await _member_or_404(session, project_id, user.id)
    membership.last_visited_at = datetime.now(UTC)
    await session.commit()


@router.patch("/{project_id}/membership", response_model=ProjectOut)
async def update_membership(
    project_id: int,
    body: ProjectMembershipIn,
    user: CurrentUser,
    session: DbSession,
):
    """The viewer's own per-project settings (currently just the push mute)."""
    membership = await _member_or_404(session, project_id, user.id)
    membership.is_muted = body.is_muted
    await session.commit()
    return await _project_response(session, project_id, user.id)


_pin_load_options = (
    selectinload(ProjectArticle.article).selectinload(Article.feed),
    selectinload(ProjectArticle.added_by),
)


@router.get("/{project_id}/articles", response_model=list[ProjectArticleOut])
async def list_project_articles(
    project_id: int,
    user: CurrentUser,
    session: DbSession,
    scope: Literal["all", "shared", "mine"] = "all",
    limit: int = Query(default=200, ge=1, le=500),
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
    tickets, comment_counts = await _ticket_info(session, project_id, article_ids)
    return [
        _pin_out(
            pin,
            pin.article.feed.title or pin.article.feed.url,
            states.get(pin.article_id),
            [_to_badge(link, entity) for link, entity in entity_map.get(pin.article_id, [])],
            ticket=tickets.get(pin.article_id),
            comment_count=comment_counts.get(pin.article_id, 0),
        )
        for pin in pins
    ]


@router.post("/{project_id}/articles", response_model=ProjectArticleOut, status_code=201)
async def add_project_article(
    project_id: int,
    body: ProjectArticleAddIn,
    user: CurrentUser,
    session: DbSession,
):
    await _member_or_404(session, project_id, user.id)
    article = await accessible_article(session, user.id, body.article_id)
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
            shared_at=datetime.now(UTC) if body.is_shared else None,
        )
        .on_conflict_do_nothing(index_elements=["project_id", "article_id", "added_by_user_id"])
        .returning(ProjectArticle.id)
    )
    if pin_id is None:
        raise HTTPException(status_code=409, detail="You already added this article")
    if note:
        # The save-time note opens the article's thread rather than living on
        # the pin, so later discussion lands in the same place.
        session.add(
            ProjectArticleComment(
                project_id=project_id, article_id=article.id, author_id=user.id, body=note
            )
        )
    await session.commit()
    if body.is_shared:
        await enqueue("send_project_pin_push", pin_id)
    pin = await session.scalar(
        select(ProjectArticle).where(ProjectArticle.id == pin_id).options(*_pin_load_options)
    )
    states = await _states_for(session, user.id, [article.id])
    tickets, comment_counts = await _ticket_info(session, project_id, [article.id])
    return _pin_out(
        pin,
        pin.article.feed.title or pin.article.feed.url,
        states.get(article.id),
        ticket=tickets.get(article.id),
        comment_count=comment_counts.get(article.id, 0),
    )


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
    user: CurrentUser,
    session: DbSession,
):
    await _member_or_404(session, project_id, user.id)
    pin = await _own_pin_or_error(session, project_id, pin_id, user.id)
    if pin.added_by_user_id != user.id:
        raise HTTPException(status_code=403, detail="Only the adder can edit this")
    updates = body.model_dump(exclude_unset=True)
    published = False
    if "is_shared" in updates and updates["is_shared"] is not None:
        if updates["is_shared"] and not pin.is_shared:
            pin.shared_at = datetime.now(UTC)
            published = True
        elif not updates["is_shared"]:
            pin.shared_at = None
        pin.is_shared = updates["is_shared"]
    await session.commit()
    if published:
        await enqueue("send_project_pin_push", pin.id)
    states = await _states_for(session, user.id, [pin.article_id])
    tickets, comment_counts = await _ticket_info(session, project_id, [pin.article_id])
    return _pin_out(
        pin,
        pin.article.feed.title or pin.article.feed.url,
        states.get(pin.article_id),
        ticket=tickets.get(pin.article_id),
        comment_count=comment_counts.get(pin.article_id, 0),
    )


@router.delete("/{project_id}/articles/{pin_id}", status_code=204)
async def remove_project_article(
    project_id: int,
    pin_id: int,
    user: CurrentUser,
    session: DbSession,
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
    user: CurrentUser,
    session: DbSession,
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


@router.put(
    "/{project_id}/articles/by-article/{article_id}/status",
    response_model=ProjectArticleStateOut,
)
async def set_article_status(
    project_id: int,
    article_id: int,
    body: ProjectArticleStatusIn,
    user: CurrentUser,
    session: DbSession,
):
    """Move the article's ticket. Any member can — shared task-list semantics,
    unlike pin edits which stay adder-only. An optional comment (the
    resolution note, possibly with a link) posts atomically with the move."""
    await _member_or_404(session, project_id, user.id)
    await _thread_or_404(session, project_id, article_id, user.id)
    now = datetime.now(UTC)
    await session.execute(
        pg_insert(ProjectArticleState)
        .values(
            project_id=project_id,
            article_id=article_id,
            status=body.status,
            updated_by_user_id=user.id,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["project_id", "article_id"],
            set_={"status": body.status, "updated_by_user_id": user.id, "updated_at": now},
        )
    )
    comment = None
    comment_body = body.comment.strip() if body.comment else ""
    if comment_body or body.link_url:
        comment = ProjectArticleComment(
            project_id=project_id,
            article_id=article_id,
            author_id=user.id,
            body=comment_body,
            link_url=body.link_url,
        )
        session.add(comment)
    await session.commit()
    if comment is not None:
        await session.refresh(comment)  # load the server-side created_at
    return ProjectArticleStateOut(
        status=body.status,
        updated_by=UserPublic.model_validate(user),
        updated_at=now,
        comment=_comment_out(comment, author=user) if comment else None,
    )


@router.get(
    "/{project_id}/articles/by-article/{article_id}/comments",
    response_model=list[ProjectCommentOut],
)
async def list_article_comments(
    project_id: int,
    article_id: int,
    user: CurrentUser,
    session: DbSession,
):
    await _member_or_404(session, project_id, user.id)
    await _thread_or_404(session, project_id, article_id, user.id)
    comments = (
        await session.scalars(
            select(ProjectArticleComment)
            .where(
                ProjectArticleComment.project_id == project_id,
                ProjectArticleComment.article_id == article_id,
            )
            .options(selectinload(ProjectArticleComment.author))
            .order_by(ProjectArticleComment.created_at.asc(), ProjectArticleComment.id.asc())
        )
    ).all()
    return [_comment_out(c) for c in comments]


@router.post(
    "/{project_id}/articles/by-article/{article_id}/comments",
    response_model=ProjectCommentOut,
    status_code=201,
)
async def add_article_comment(
    project_id: int,
    article_id: int,
    body: ProjectCommentIn,
    user: CurrentUser,
    session: DbSession,
):
    await _member_or_404(session, project_id, user.id)
    await _thread_or_404(session, project_id, article_id, user.id)
    comment = ProjectArticleComment(
        project_id=project_id,
        article_id=article_id,
        author_id=user.id,
        body=body.body,
        link_url=body.link_url,
    )
    session.add(comment)
    await session.commit()
    await session.refresh(comment)  # load the server-side created_at
    return _comment_out(comment, author=user)


@router.delete("/{project_id}/comments/{comment_id}", status_code=204)
async def delete_article_comment(
    project_id: int,
    comment_id: int,
    user: CurrentUser,
    session: DbSession,
):
    membership = await _member_or_404(session, project_id, user.id)
    comment = await session.scalar(
        select(ProjectArticleComment).where(
            ProjectArticleComment.id == comment_id,
            ProjectArticleComment.project_id == project_id,
        )
    )
    if comment is None:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.author_id != user.id and membership.role != "owner":
        raise HTTPException(status_code=403, detail="Only the author or the owner can delete this")
    await session.delete(comment)
    await session.commit()
