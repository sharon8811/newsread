"""Generated illustrations for articles that arrive without one.

Generation is lazy: the first view of an imageless article claims it
(articles.image_gen_attempted_at) and a background task renders the viewer's
prompt template against their image model — or the server-wide
IMAGE_GENERATION_* default when they haven't configured one. The result is
stored in generated_images and article.image_url points at the serving route,
so every subscriber sees it.

Two wire shapes: OpenRouter serves image models through its dedicated
`POST {base}/images` endpoint (the only one that accepts generation knobs like
aspect_ratio); everything else gets the OpenAI images API. Model-specific
extra parameters — a JSON object from IMAGE_GENERATION_EXTRA_PARAMS or the
user's image block — are merged verbatim into either request.
"""

import base64
import json
import logging
import time
from datetime import UTC, datetime

import httpx
from openai import AsyncOpenAI
from sqlalchemy import func, select

from . import crypto, db, llm
from .config import settings
from .models import Article, GeneratedImage, User, UserAISettings

logger = logging.getLogger(__name__)

# The user-facing default; {article_title}/{article_excerpt} are the
# supported template tags (render_prompt).
DEFAULT_IMAGE_PROMPT = (
    "{article_title} showcased in a gritty noir comic book splash page. "
    "High contrast chiaroscuro lighting, heavy ink lines, dramatic angle. "
    "Full bleed, edge-to-edge artwork, masterpiece."
)

_GENERATION_TIMEOUT = 120


def render_prompt(template: str, *, title: str, excerpt: str = "") -> str:
    # Plain replacement, not str.format: any other brace in the user's prompt
    # must stay literal text instead of raising KeyError.
    return template.replace("{article_title}", title).replace("{article_excerpt}", excerpt)


def is_configured() -> bool:
    """Whether the server-wide default image model is configured."""
    return bool(settings.image_generation_api_key and settings.image_generation_model)


def parse_extra_params(raw: str | None) -> dict:
    """The extra-parameters JSON object, or {}. A malformed value must degrade
    to plain generation rather than break it, so it only logs a warning."""
    if not raw or not raw.strip():
        return {}
    try:
        params = json.loads(raw)
    except ValueError:
        logger.warning("Ignoring invalid image extra params (not JSON): %.100s", raw)
        return {}
    if not isinstance(params, dict):
        logger.warning("Ignoring image extra params (not a JSON object): %.100s", raw)
        return {}
    return params


def system_config() -> llm.LLMConfig | None:
    if not is_configured():
        return None
    return llm.LLMConfig(
        provider="system",
        api_key=settings.image_generation_api_key,
        base_url=settings.image_generation_base_url or None,
        model=settings.image_generation_model,
        extra_params=parse_extra_params(settings.image_generation_extra_params),
    )


async def resolve_config(session, user_id: int) -> llm.LLMConfig | None:
    """The image model a user's view generates with: their own image block
    when configured (key falls back to their main key — the settings API only
    allows that for a matching provider), else the server-wide default."""
    row = await session.get(UserAISettings, user_id)
    if row is not None and row.image_provider and row.image_model:
        key_enc = row.image_api_key_enc or row.api_key_enc
        return llm.LLMConfig(
            provider=row.image_provider,
            api_key=crypto.decrypt_token(key_enc),
            base_url=llm.resolve_base_url(row.image_provider, row.image_base_url),
            model=row.image_model,
            user_owned=True,
            extra_params=parse_extra_params(row.image_extra_params),
        )
    return system_config()


async def generations_this_month(session, user_id: int) -> int:
    """Image generations this user started this calendar month (UTC). Counts
    claims rather than stored images: failed attempts spend money too, and the
    claim is written synchronously so the budget can't be raced past by
    generations that haven't finished yet."""
    month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    count = await session.scalar(
        select(func.count())
        .select_from(Article)
        .where(
            Article.image_gen_user_id == user_id,
            Article.image_gen_attempted_at >= month_start,
        )
    )
    return count or 0


async def remaining_budget(session, user: User) -> int | None:
    """Generations the user may still start this month; None = unlimited."""
    if user.image_gen_monthly_limit is None:
        return None
    used = await generations_this_month(session, user.id)
    return max(0, user.image_gen_monthly_limit - used)


def public_image_url(article_id: int) -> str:
    # Relative on purpose: each client resolves it against the API base it
    # already reaches (web NEXT_PUBLIC_API_URL, mobile server URL), so the
    # stored value never depends on a deployment-specific host. Absolute URLs
    # built from oauth_redirect_base broke as soon as that var pointed at an
    # OAuth tunnel instead of the API the browser uses.
    return f"/api/articles/{article_id}/generated-image"


def _uses_openrouter_images(config: llm.LLMConfig) -> bool:
    return "openrouter" in (config.base_url or "")


async def _fetch_image(url: str) -> tuple[bytes, str]:
    """Bytes + content type from a data: URL or a hosted image URL."""
    if url.startswith("data:"):
        header, _, b64 = url.partition(",")
        content_type = header[len("data:") :].split(";")[0] or "image/png"
        return base64.b64decode(b64), content_type
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content, response.headers.get("content-type", "image/png")


async def _generate_openrouter(
    config: llm.LLMConfig, prompt: str, usage: llm.TokenUsage
) -> tuple[bytes, str, llm.TokenUsage]:
    """OpenRouter's dedicated images endpoint: POST {base}/images. Unlike the
    chat-completions modalities route, it accepts generation parameters such
    as aspect_ratio, which is why extra_params merge into the body here."""
    payload = {"model": config.model, "prompt": prompt, **config.extra_params}
    async with httpx.AsyncClient(timeout=_GENERATION_TIMEOUT) as client:
        response = await client.post(
            f"{(config.base_url or '').rstrip('/')}/images",
            headers={"Authorization": f"Bearer {config.api_key}"},
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
    reported = body.get("usage") or {}
    usage.add(reported.get("prompt_tokens"), reported.get("completion_tokens"))
    data_items = body.get("data") or []
    first = data_items[0] if data_items else {}
    if first.get("b64_json"):
        content_type = first.get("media_type") or "image/png"
        return base64.b64decode(first["b64_json"]), content_type, usage
    if first.get("url"):
        data, content_type = await _fetch_image(first["url"])
        return data, content_type, usage
    raise RuntimeError("The model returned no image")


async def generate(config: llm.LLMConfig, prompt: str) -> tuple[bytes, str, llm.TokenUsage]:
    """One image from the configured endpoint: (bytes, content_type, usage)."""
    usage = llm.TokenUsage()
    if _uses_openrouter_images(config):
        return await _generate_openrouter(config, prompt, usage)

    async with AsyncOpenAI(
        api_key=config.api_key, base_url=config.base_url, timeout=_GENERATION_TIMEOUT
    ) as client:
        response = await client.images.generate(
            model=config.model,
            prompt=prompt,
            n=1,
            extra_body=config.extra_params or None,
        )
        datum = response.data[0]
        if datum.b64_json:
            return base64.b64decode(datum.b64_json), "image/png", usage
        if datum.url:
            data, content_type = await _fetch_image(datum.url)
            return data, content_type, usage
        raise RuntimeError("The image endpoint returned no image data")


async def generate_for_article(
    article_id: int, user_id: int, config: llm.LLMConfig, prompt: str
) -> None:
    """Background task; the caller already claimed the article by setting
    image_gen_attempted_at, so this runs at most once per article."""
    started = time.monotonic()
    # Late-bound (db.SessionLocal, not a by-value import) so the test
    # suite's engine rebinding applies here too.
    async with db.SessionLocal() as session:
        try:
            data, content_type, usage = await generate(config, prompt)
        except Exception as exc:
            logger.warning("Image generation failed for article %s: %s", article_id, exc)
            await llm.record_usage(
                session,
                user_id=user_id,
                feature="image",
                config=config,
                duration_ms=int((time.monotonic() - started) * 1000),
                status="error",
                error=str(exc),
            )
            return
        session.add(
            GeneratedImage(
                article_id=article_id, content_type=content_type, data=data, model=config.model
            )
        )
        article = await session.get(Article, article_id)
        if article is not None and article.image_url is None:
            article.image_url = public_image_url(article_id)
        await session.commit()
        await llm.record_usage(
            session,
            user_id=user_id,
            feature="image",
            config=config,
            usage=usage,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        logger.info("Generated image for article %s via %s", article_id, config.model)
