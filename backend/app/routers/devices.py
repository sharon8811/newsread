from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from ..deps import CurrentUser, DbSession
from ..models import Device
from ..schemas import DeviceIn, DeviceOut

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("", response_model=DeviceOut, status_code=201)
async def register_device(
    body: DeviceIn,
    user: CurrentUser,
    session: DbSession,
):
    """Idempotent: re-registering an existing token refreshes it, and a token
    that logs into another account moves to that account."""
    device = await session.scalar(select(Device).where(Device.push_token == body.push_token))
    if device is None:
        device = Device(user_id=user.id, push_token=body.push_token, platform=body.platform)
        session.add(device)
    else:
        device.user_id = user.id
        device.platform = body.platform
        device.last_seen_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(device)
    return DeviceOut.model_validate(device)


@router.delete("", status_code=204)
async def unregister_device(
    user: CurrentUser,
    session: DbSession,
    push_token: str = Query(min_length=1, max_length=512),
):
    """Called on logout. The token goes in the query string because Expo
    tokens contain characters that are awkward in a path segment."""
    device = await session.scalar(
        select(Device).where(Device.push_token == push_token, Device.user_id == user.id)
    )
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    await session.delete(device)
    await session.commit()
