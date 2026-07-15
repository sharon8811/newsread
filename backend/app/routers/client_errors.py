import logging

from fastapi import APIRouter

from ..schemas import ClientErrorIn

router = APIRouter(prefix="/client-errors", tags=["client-errors"])

logger = logging.getLogger(__name__)


@router.post("", status_code=204)
async def report_client_error(body: ClientErrorIn):
    """Log sink for browser-side errors — the web app's only production error
    channel. Unauthenticated (errors happen before login too); the client
    dedupes and caps volume, and the schema bounds every field."""
    logger.error(
        "client error [%s]%s at %s: %s\n%s",
        body.context or "unknown",
        f" digest={body.digest}" if body.digest else "",
        body.url or "?",
        body.message,
        body.stack or "(no stack)",
    )
