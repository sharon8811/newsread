"""Summarization LLM access via any OpenAI-compatible endpoint (OpenAI, vLLM, LiteLLM, Ollama)."""

import logging
import re

from openai import AsyncOpenAI

from .config import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def is_configured() -> bool:
    return bool(settings.openai_api_key and settings.openai_model)


def get_client() -> AsyncOpenAI:
    """Shared client for the endpoint; also used by embeddings.py."""
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            timeout=120,
        )
    return _client


def _clean(content: str) -> str:
    return _THINK_RE.sub("", content).strip()


async def _complete(messages: list[dict], max_tokens: int) -> str:
    response = await get_client().chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.3,
    )
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


async def summarize(title: str, text: str) -> tuple[str, str, str]:
    """Return (one-liner, paragraph, full) summaries from a single completion."""
    raw = await _complete(
        [
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": f"Article title: {title}\n\nArticle text:\n{text}"},
        ],
        max_tokens=1500,
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
    )


# Article Q&A lives in qa_agent.py (pydantic_ai, tool-calling, streaming).
