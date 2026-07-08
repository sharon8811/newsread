"""Generated illustrations for articles that arrive without one.

Generation is lazy: the first view of an imageless article claims it
(articles.image_gen_attempted_at) and a background task renders the viewer's
prompt template against their image model — or the server-wide
IMAGE_GENERATION_* default when they haven't configured one. The result is
stored in generated_images and article.image_url points at the serving route,
so every subscriber sees it.

Two wire shapes: OpenRouter serves image models through chat completions with
the `modalities` extension (base64 data URL in the message); everything else
gets the OpenAI images API.
"""

import base64
import logging
import time

import httpx
from openai import AsyncOpenAI

from . import crypto, db, llm
from .config import settings
from .models import Article, GeneratedImage, UserAISettings

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


def system_config() -> llm.LLMConfig | None:
    if not is_configured():
        return None
    return llm.LLMConfig(
        provider="system",
        api_key=settings.image_generation_api_key,
        base_url=settings.image_generation_base_url or None,
        model=settings.image_generation_model,
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
        )
    return system_config()


def public_image_url(article_id: int) -> str:
    # oauth_redirect_base is the deployment's public backend base URL; <img>
    # tags need an absolute address.
    return f"{settings.oauth_redirect_base}/api/articles/{article_id}/generated-image"


def _uses_chat_modalities(config: llm.LLMConfig) -> bool:
    return "openrouter" in (config.base_url or "")


def _image_url_of(entry) -> str:
    """The data/hosted URL out of one chat-modalities image entry, which the
    SDK surfaces either as a plain dict or an extra-field object."""
    if isinstance(entry, dict):
        image_url = entry.get("image_url") or {}
        return (image_url.get("url") if isinstance(image_url, dict) else "") or ""
    image_url = getattr(entry, "image_url", None)
    if isinstance(image_url, dict):
        return image_url.get("url") or ""
    return getattr(image_url, "url", "") or ""


async def _fetch_image(url: str) -> tuple[bytes, str]:
    """Bytes + content type from a data: URL or a hosted image URL."""
    if url.startswith("data:"):
        header, _, b64 = url.partition(",")
        content_type = header[len("data:"):].split(";")[0] or "image/png"
        return base64.b64decode(b64), content_type
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content, response.headers.get("content-type", "image/png")


async def generate(config: llm.LLMConfig, prompt: str) -> tuple[bytes, str, llm.TokenUsage]:
    """One image from the configured endpoint: (bytes, content_type, usage)."""
    usage = llm.TokenUsage()
    async with AsyncOpenAI(
        api_key=config.api_key, base_url=config.base_url, timeout=_GENERATION_TIMEOUT
    ) as client:
        if _uses_chat_modalities(config):
            response = await client.chat.completions.create(
                model=config.model,
                messages=[{"role": "user", "content": prompt}],
                extra_body={"modalities": ["image", "text"]},
            )
            if response.usage is not None:
                usage.add(response.usage.prompt_tokens, response.usage.completion_tokens)
            message = response.choices[0].message
            images = (
                getattr(message, "images", None)
                or (message.model_extra or {}).get("images")
                or []
            )
            url = _image_url_of(images[0]) if images else ""
            if not url:
                raise RuntimeError("The model returned no image")
            data, content_type = await _fetch_image(url)
            return data, content_type, usage

        response = await client.images.generate(model=config.model, prompt=prompt, n=1)
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
                session, user_id=user_id, feature="image", config=config,
                duration_ms=int((time.monotonic() - started) * 1000),
                status="error", error=str(exc),
            )
            return
        session.add(
            GeneratedImage(article_id=article_id, content_type=content_type,
                           data=data, model=config.model)
        )
        article = await session.get(Article, article_id)
        if article is not None and article.image_url is None:
            article.image_url = public_image_url(article_id)
        await session.commit()
        await llm.record_usage(
            session, user_id=user_id, feature="image", config=config, usage=usage,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        logger.info("Generated image for article %s via %s", article_id, config.model)
