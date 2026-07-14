"""LLM usage for bring-your-own-key users: the audit trail behind /usage.

Only calls made on the user's own key are ever written to llm_usage
(llm.record_usage), so these endpoints simply read back the user's rows —
history stays visible even after the key is deleted.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Date, cast, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import LLMUsage, User, UserAISettings
from ..schemas import (
    ActivityRange,
    UsageDayOut,
    UsageEventOut,
    UsageFeatureOut,
    UsageModelOut,
    UsageSummaryOut,
)
from ..security import get_current_user

router = APIRouter(prefix="/usage", tags=["usage"])

RANGE_DAYS: dict[str, int] = {"week": 7, "month": 30, "year": 365}

EVENTS_MAX = 100

# Rows carry UTC timestamps; days are bucketed in UTC (unlike reading activity,
# there's no client-local day column — a call's date is when the server ran it).
_day = cast(LLMUsage.created_at, Date)
_tokens = LLMUsage.prompt_tokens + LLMUsage.completion_tokens


@router.get("/summary", response_model=UsageSummaryOut)
async def summary(
    range_: ActivityRange = Query("week", alias="range"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    # UTC on purpose: the Date cast above buckets in the DB session's UTC, and
    # the window boundary must agree with it.
    today = datetime.now(UTC).date()
    window = RANGE_DAYS[range_]
    start = today - timedelta(days=window - 1)
    prev_start = start - timedelta(days=window)

    in_window = (
        LLMUsage.user_id == user.id,
        _day >= start,
        _day <= today,
    )

    day_rows = await session.execute(
        select(_day.label("day"), func.count(), func.sum(_tokens)).where(*in_window).group_by("day")
    )
    by_day = {day: (calls, tokens or 0) for day, calls, tokens in day_rows.all()}
    days = [
        UsageDayOut(day=d, calls=by_day.get(d, (0, 0))[0], tokens=by_day.get(d, (0, 0))[1])
        for d in (start + timedelta(days=i) for i in range(window))
    ]

    prev_total_tokens = (
        await session.scalar(
            select(func.coalesce(func.sum(_tokens), 0)).where(
                LLMUsage.user_id == user.id,
                _day >= prev_start,
                _day < start,
            )
        )
    ) or 0

    error_count = (
        await session.scalar(select(func.count()).where(*in_window, LLMUsage.status == "error"))
    ) or 0

    tokens_sum = func.sum(_tokens).label("tokens")
    feature_rows = await session.execute(
        select(LLMUsage.feature, func.count(), tokens_sum)
        .where(*in_window)
        .group_by(LLMUsage.feature)
        .order_by(desc("tokens"))
    )
    by_feature = [
        UsageFeatureOut(feature=feature, calls=calls, tokens=tokens or 0)
        for feature, calls, tokens in feature_rows.all()
    ]

    model_rows = await session.execute(
        select(LLMUsage.provider, LLMUsage.model, func.count(), tokens_sum)
        .where(*in_window)
        .group_by(LLMUsage.provider, LLMUsage.model)
        .order_by(desc("tokens"))
    )
    by_model = [
        UsageModelOut(provider=provider, model=model, calls=calls, tokens=tokens or 0)
        for provider, model, calls, tokens in model_rows.all()
    ]

    return UsageSummaryOut(
        range=range_,
        configured=(await session.get(UserAISettings, user.id)) is not None,
        total_calls=sum(d.calls for d in days),
        total_tokens=sum(d.tokens for d in days),
        prev_total_tokens=prev_total_tokens,
        error_count=error_count,
        days=days,
        by_feature=by_feature,
        by_model=by_model,
    )


@router.get("/events", response_model=list[UsageEventOut])
async def events(
    before_id: int | None = Query(None, description="Cursor: return rows older than this id"),
    limit: int = Query(20, ge=1, le=EVENTS_MAX),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Newest-first call log; page with the last row's id as before_id."""
    query = (
        select(LLMUsage)
        .where(LLMUsage.user_id == user.id)
        .order_by(LLMUsage.id.desc())
        .limit(limit)
    )
    if before_id is not None:
        query = query.where(LLMUsage.id < before_id)
    return [UsageEventOut.model_validate(row) for row in (await session.scalars(query)).all()]
