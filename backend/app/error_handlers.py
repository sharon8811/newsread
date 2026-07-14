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
from .llm import LLMRequestFailed
from .summarizer import ThinContentError


def register(app: FastAPI) -> None:
    @app.exception_handler(LLMRequestFailed)
    async def _llm_failed(request: Request, exc: LLMRequestFailed) -> JSONResponse:
        # Covers EmptyResponseError too — its message is user-facing.
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(ThinContentError)
    async def _thin_content(request: Request, exc: ThinContentError) -> JSONResponse:
        # Summarizing a headline stub just makes the model invent details.
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Couldn't fetch the article's full text — the site may "
                "block automated readers. Open the original instead."
            },
        )

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
