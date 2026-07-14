"""Send push notifications through Expo's push service.

Expo push tokens work for both iOS and Android and need no server-side
credentials, which keeps self-hosted deployments zero-config. Tokens that
Expo reports as DeviceNotRegistered (app uninstalled, token rotated) are
pruned so they aren't retried forever.
"""

import logging

import httpx
from sqlalchemy import delete, select

from . import db
from .models import Device

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
EXPO_BATCH = 100  # Expo's documented max messages per request


async def send_push(user_ids: list[int], title: str, body: str, data: dict | None = None) -> int:
    """Notify every registered device of the given users; returns messages
    accepted by Expo. Failures are logged, never raised — push is best-effort."""
    if not user_ids:
        return 0
    async with db.SessionLocal() as session:
        devices = (await session.scalars(select(Device).where(Device.user_id.in_(user_ids)))).all()
    if not devices:
        return 0

    messages = [
        {
            "to": device.push_token,
            "title": title,
            "body": body,
            "data": data or {},
            "sound": "default",
        }
        for device in devices
    ]
    sent = 0
    stale: list[str] = []
    async with httpx.AsyncClient(timeout=15) as client:
        for start in range(0, len(messages), EXPO_BATCH):
            chunk = messages[start : start + EXPO_BATCH]
            try:
                response = await client.post(EXPO_PUSH_URL, json=chunk)
                response.raise_for_status()
                tickets = response.json().get("data", [])
            except Exception as exc:
                logger.warning("Expo push request failed: %s", exc)
                continue
            for message, ticket in zip(chunk, tickets, strict=False):
                if ticket.get("status") == "ok":
                    sent += 1
                elif (ticket.get("details") or {}).get("error") == "DeviceNotRegistered":
                    stale.append(message["to"])

    if stale:
        async with db.SessionLocal() as session:
            await session.execute(delete(Device).where(Device.push_token.in_(stale)))
            await session.commit()
        logger.info("Pruned %d unregistered push tokens", len(stale))
    return sent
