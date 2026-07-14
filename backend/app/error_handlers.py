"""App-level translations of domain exceptions to HTTP responses.

Routers used to hand-translate these in local try/except blocks, drifting the
messages apart. A raise anywhere in a request now produces the same response;
routers only catch these exceptions when they genuinely handle them (e.g.
subscribing without an initial fetch on FeedRateLimited, or the preview cache
in catalog.py that stores its own message).
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .crypto import TokenCryptoError
from .fetcher import FeedRateLimited


def register(app: FastAPI) -> None:
    @app.exception_handler(TokenCryptoError)
    async def _token_crypto(request: Request, exc: TokenCryptoError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "Your stored API key can't be decrypted — re-enter it in Settings."},
        )

    @app.exception_handler(FeedRateLimited)
    async def _feed_rate_limited(request: Request, exc: FeedRateLimited) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": f"{exc.host} is rate-limiting our requests right now. "
                "Try again in a minute or two."
            },
        )
