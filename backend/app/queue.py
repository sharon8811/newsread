"""Enqueue jobs for the ARQ worker; failures degrade gracefully (cron catches up)."""

import logging

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from .config import settings

logger = logging.getLogger(__name__)

_pool: ArqRedis | None = None


async def enqueue(job_name: str, *args) -> None:
    global _pool
    try:
        if _pool is None:
            _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        await _pool.enqueue_job(job_name, *args)
    except Exception as exc:
        logger.warning("Could not enqueue %s%r: %s", job_name, args, exc)
