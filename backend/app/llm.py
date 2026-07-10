"""LLM access via any OpenAI-compatible endpoint (OpenAI, vLLM, LiteLLM, Ollama).

Every call runs against an LLMConfig: either the server-wide default from
config.py or a user's own key (UserAISettings, "bring your own key"). Anthropic
is reached through its OpenAI-compatible endpoint, so one wire format covers
every provider in the dropdown."""

import base64
import logging
import re
from dataclasses import dataclass, field

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from . import crypto
from .config import settings
from .models import LLMUsage, UserAISettings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

ANTHROPIC_COMPAT_BASE_URL = "https://api.anthropic.com/v1/"


@dataclass(frozen=True)
class LLMConfig:
    """One resolved endpoint+model to run a call against. `user_owned` marks
    calls billed to the user's key — only those are logged to llm_usage."""

    provider: str  # 'system' | 'openai' | 'anthropic' | 'custom'
    api_key: str
    base_url: str | None
    model: str
    user_owned: bool = False
    supports_vision: bool = False  # model accepts image input (user/operator-declared)
    # Model-specific request parameters merged verbatim into every generation
    # call (image models only today, e.g. {"aspect_ratio": "16:9"}).
    extra_params: dict = field(default_factory=dict)


class TokenUsage:
    """Accumulates token counts across the call(s) behind one logical request."""

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def add(self, prompt_tokens: int | None, completion_tokens: int | None) -> None:
        self.prompt_tokens += prompt_tokens or 0
        self.completion_tokens += completion_tokens or 0


def is_configured() -> bool:
    """Whether the server-wide default LLM is configured."""
    return bool(settings.openai_api_key and settings.openai_model)


def system_config() -> LLMConfig | None:
    if not is_configured():
        return None
    return LLMConfig(
        provider="system",
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
        model=settings.openai_model,
        supports_vision=settings.openai_model_vision,
    )


def resolve_base_url(provider: str, base_url: str | None) -> str | None:
    if provider == "anthropic":
        return ANTHROPIC_COMPAT_BASE_URL
    if provider == "custom":
        return base_url or None
    return None  # openai: SDK default


def config_for_user_settings(row: UserAISettings) -> LLMConfig:
    """Build a config from a user's stored settings. Raises crypto.TokenCryptoError
    when the stored key can't be decrypted (encryption key rotated) — callers
    surface that instead of silently falling back to the operator's bill."""
    return LLMConfig(
        provider=row.provider,
        api_key=crypto.decrypt_token(row.api_key_enc),
        base_url=resolve_base_url(row.provider, row.base_url),
        model=row.model,
        user_owned=True,
        supports_vision=row.supports_vision,
    )


async def resolve_config(session: AsyncSession, user_id: int) -> LLMConfig | None:
    """The LLM a user's interactive calls run on: their own key when they
    saved one, else the server-wide default, else None."""
    row = await session.get(UserAISettings, user_id)
    if row is not None:
        return config_for_user_settings(row)
    return system_config()


async def record_usage(
    session: AsyncSession,
    *,
    user_id: int,
    feature: str,
    config: LLMConfig | None,
    usage: TokenUsage | None = None,
    duration_ms: int = 0,
    status: str = "ok",
    error: str | None = None,
) -> None:
    """Log one call to llm_usage — a no-op unless it ran on the user's own key."""
    if config is None or not config.user_owned:
        return
    session.add(
        LLMUsage(
            user_id=user_id,
            feature=feature,
            provider=config.provider,
            model=config.model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            duration_ms=duration_ms,
            status=status,
            error=error[:500] if error else None,
        )
    )
    await session.commit()


def get_client() -> AsyncOpenAI:
    """Shared client for the server-wide endpoint; also used by embeddings.py."""
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            timeout=120,
        )
    return _client


def user_client(config: LLMConfig) -> AsyncOpenAI:
    """A short-lived client for one call on a user's key. Deliberately not
    cached: a cache would pin plaintext keys and dead connection pools across
    key rotations, and the handshake is noise next to the LLM call itself.
    Use as an async context manager so the pool is closed after the call."""
    return AsyncOpenAI(api_key=config.api_key, base_url=config.base_url, timeout=120)


def _clean(content: str) -> str:
    return _THINK_RE.sub("", content).strip()


async def _complete(
    messages: list[dict],
    max_tokens: int,
    *,
    config: LLMConfig | None = None,
    usage: TokenUsage | None = None,
) -> str:
    model = config.model if config is not None else settings.openai_model
    if config is not None and config.user_owned:
        async with user_client(config) as client:
            return await _create(client, model, messages, max_tokens, usage)
    return await _create(get_client(), model, messages, max_tokens, usage)


async def _create(
    client: AsyncOpenAI,
    model: str,
    messages: list[dict],
    max_tokens: int,
    usage: TokenUsage | None,
) -> str:
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.3,
    )
    if usage is not None and response.usage is not None:
        usage.add(response.usage.prompt_tokens, response.usage.completion_tokens)
    return _clean(response.choices[0].message.content or "")


SUMMARY_SYSTEM = """You summarize news articles for a busy reader, at three levels of depth.

Output EXACTLY this structure (plain text, no markdown):

ONELINER: one sentence of at most 20 words with the gist
PARAGRAPH: two to four sentences with the essential information
FULL:
One or two sentences with the core takeaway.

Three to five key points, each on its own line starting with "• ".

Be concrete and specific. Never pad, never editorialize, never mention that you are summarizing."""

_ONELINER_RE = re.compile(r"ONELINER:\s*(.+)")
_PARAGRAPH_RE = re.compile(r"PARAGRAPH:\s*(.+?)(?=\n\s*FULL:|\Z)", re.DOTALL)
_FULL_RE = re.compile(r"FULL:\s*\n?(.+)", re.DOTALL)


def _parse_levels(raw: str) -> tuple[str, str, str]:
    short = medium = full = ""
    if match := _ONELINER_RE.search(raw):
        short = match.group(1).strip()
    if match := _PARAGRAPH_RE.search(raw):
        medium = " ".join(match.group(1).split())
    if match := _FULL_RE.search(raw):
        full = match.group(1).strip()
    if not full:
        full = raw.strip()
    return short, medium, full


async def summarize(
    title: str,
    text: str,
    *,
    config: LLMConfig | None = None,
    usage: TokenUsage | None = None,
) -> tuple[str, str, str]:
    """Return (one-liner, paragraph, full) summaries from a single completion."""
    raw = await _complete(
        [
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": f"Article title: {title}\n\nArticle text:\n{text}"},
        ],
        max_tokens=1500,
        config=config,
        usage=usage,
    )
    return _parse_levels(raw)


_SCREENSHOT_NOTE = (
    "The article's page has no extractable text — attached is a screenshot of "
    "the rendered page (often a comic, chart or infographic). Read the image "
    "and summarize what it shows."
)


async def summarize_screenshot(
    title: str,
    image_jpeg: bytes,
    *,
    config: LLMConfig | None = None,
    usage: TokenUsage | None = None,
) -> tuple[str, str, str]:
    """Same three-level summary, grounded on a screenshot of the rendered page
    instead of prose. Only called for vision-capable configs."""
    image_b64 = base64.b64encode(image_jpeg).decode()
    raw = await _complete(
        [
            {"role": "system", "content": SUMMARY_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Article title: {title}\n\n{_SCREENSHOT_NOTE}"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                    },
                ],
            },
        ],
        max_tokens=1500,
        config=config,
        usage=usage,
    )
    return _parse_levels(raw)


SHARE_MESSAGE_SYSTEM = """You write the short note someone sends alongside a news link in a work chat (Slack or Microsoft Teams).

Rules:
- One to three sentences, at most 50 words, plain text.
- Sound like a person, not a marketer: no hashtags, no "Check this out!", no greetings or sign-offs.
- Lead with why the article matters to the people reading it.
- Use only facts from the provided title and summary; never invent details.
- Do not include the URL — it is attached separately.
- Output only the message text, nothing else."""


async def share_message(
    title: str,
    summary: str,
    draft: str = "",
    tone: str | None = None,
    target_name: str | None = None,
    *,
    config: LLMConfig | None = None,
    usage: TokenUsage | None = None,
) -> str:
    """One short human-sounding message to accompany a shared article link."""
    parts = [f"Article title: {title}"]
    if summary:
        parts.append(f"Article summary:\n{summary}")
    if target_name:
        parts.append(f"It will be posted to: {target_name}")
    if tone:
        parts.append(f"Tone: {tone}")
    if draft.strip():
        parts.append(
            "Polish this draft — keep its intent and any personal remarks, "
            f"just make it clearer and tighter:\n{draft.strip()}"
        )
    else:
        parts.append("Write the message from scratch.")
    return await _complete(
        [
            {"role": "system", "content": SHARE_MESSAGE_SYSTEM},
            {"role": "user", "content": "\n\n".join(parts)},
        ],
        max_tokens=300,
        config=config,
        usage=usage,
    )


# Article Q&A lives in qa_agent.py (pydantic_ai, tool-calling, streaming).
