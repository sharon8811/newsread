"""LLM access via any OpenAI-compatible endpoint (OpenAI, vLLM, LiteLLM, Ollama)."""

import logging
import re

from openai import AsyncOpenAI

from .config import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def is_configured() -> bool:
    return bool(settings.openai_api_key and settings.openai_model)


def _get_client() -> AsyncOpenAI:
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
    response = await _get_client().chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.3,
    )
    return _clean(response.choices[0].message.content or "")


SUMMARY_SYSTEM = """You summarize news articles for a busy reader.

Output format (plain text, no markdown headers):
- One or two sentences with the core takeaway.
- A blank line.
- Three to five key points, each on its own line starting with "• ".

Be concrete and specific. Never pad, never editorialize, never mention that you are summarizing."""


async def summarize(title: str, text: str) -> str:
    return await _complete(
        [
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": f"Article title: {title}\n\nArticle text:\n{text}"},
        ],
        max_tokens=1200,
    )


QA_SYSTEM = """You are NewsRead's reading assistant. The user is reading the article below and asking questions about it.

Ground your answers in the article text. If the article does not contain the answer, say so plainly before adding any general knowledge, and keep that clearly separated. Be concise; plain text only.

Article title: {title}

Article text:
{text}"""


async def answer(
    title: str, text: str, history: list[tuple[str, str]], question: str
) -> str:
    messages: list[dict] = [
        {"role": "system", "content": QA_SYSTEM.format(title=title, text=text)}
    ]
    for role, content in history[-20:]:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})
    return await _complete(messages, max_tokens=1500)
