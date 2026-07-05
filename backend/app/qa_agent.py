"""Article Q&A agent: pydantic_ai over any OpenAI-compatible endpoint, with
optional Tavily web search/extract tools when TAVILY_API_KEY is set."""

import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPartDelta,
)
from pydantic_ai.common_tools.tavily import tavily_search_tool
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    UserPromptPart,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits
from tavily import AsyncTavilyClient

from .config import settings

logger = logging.getLogger(__name__)

# Hard cap on model round-trips per question; bounds latency and Tavily spend.
_LIMITS = UsageLimits(request_limit=6)

_EXTRACT_MAX_CHARS = 8_000


def is_configured() -> bool:
    return bool(settings.openai_api_key and settings.openai_model)


def search_enabled() -> bool:
    return bool(settings.tavily_api_key)


QA_INSTRUCTIONS = """You are NewsRead's reading assistant. The user is reading the article below and asking questions about it.

Today's date: {today}.

Ground your answers in the article text. If web tools are available, use web_search only when the question needs information the article does not contain — product or model details, follow-up developments, background on people, companies or technologies. Use web_extract when a specific page (a search result, a link from the article) likely holds the answer but you only have a snippet. When you use information from the web, cite the source inline as a markdown link. If neither the article nor the web yields the answer, say so plainly.

Be concise. Answer in markdown.

Article title: {title}
Article URL: {url}
{published}
Article text:
{text}{entities}"""


async def web_extract(url: str) -> str:
    """Fetch a web page and return its main content as text.

    Use when a specific page likely contains the answer but you only have its
    URL or a short snippet.
    """
    try:
        response = await AsyncTavilyClient(settings.tavily_api_key).extract(
            url, format="markdown", timeout=20
        )
    except Exception as exc:
        logger.warning("web_extract failed for %s: %s", url, exc)
        return f"Could not extract {url}: {exc}"
    results = response.get("results") or []
    if not results:
        return f"Could not extract {url}: the page returned no content."
    content = results[0].get("raw_content") or ""
    if len(content) > _EXTRACT_MAX_CHARS:
        content = content[:_EXTRACT_MAX_CHARS] + "\n\n[page truncated]"
    return content


def _tools() -> list:
    if not search_enabled():
        return []
    return [tavily_search_tool(settings.tavily_api_key, max_results=5), web_extract]


def _model() -> OpenAIChatModel:
    return OpenAIChatModel(
        settings.openai_model,
        provider=OpenAIProvider(
            base_url=settings.openai_base_url or None,
            api_key=settings.openai_api_key,
        ),
    )


def _entities_block(entities: list[dict]) -> str:
    """Compact context from the enricher cache (repo stats, paper metadata…)."""
    if not entities:
        return ""
    lines = []
    for entity in entities:
        facts = ", ".join(f"{k}: {v}" for k, v in entity["badge"].items())
        line = f"- {entity['kind']} {entity['key']} ({entity['url']})"
        if facts:
            line += f" — {facts}"
        lines.append(line)
    return "\n\nLinked resources already looked up (current data, no need to search for these):\n" + "\n".join(lines)


def _instructions(
    title: str,
    url: str,
    text: str,
    published_at: datetime | None,
    entities: list[dict],
) -> str:
    published = (
        f"Article published: {published_at.date().isoformat()}\n" if published_at else ""
    )
    return QA_INSTRUCTIONS.format(
        today=datetime.now(timezone.utc).date().isoformat(),
        title=title,
        url=url,
        published=published,
        text=text,
        entities=_entities_block(entities),
    )


def _to_message_history(history: list[tuple[str, str]]) -> list[ModelMessage]:
    messages: list[ModelMessage] = []
    for role, content in history[-20:]:
        if role == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(content=content)]))
        else:
            messages.append(ModelResponse(parts=[TextPart(content=content)]))
    return messages


def _tool_args(part) -> dict:
    try:
        args = part.args_as_dict()
    except Exception:
        return {}
    # Keep the payload UI-sized; queries and URLs are short, drop anything huge.
    return {k: v for k, v in args.items() if isinstance(v, (str, int, float, bool)) and len(str(v)) < 500}


def _domain(url: Any) -> str:
    try:
        return urlparse(str(url)).netloc or str(url)
    except Exception:
        return str(url)


def _summarize_tool_result(name: str, content: Any) -> str:
    """One line for the UI chip, not for the model (which gets the full content)."""
    if name == "tavily_search" and isinstance(content, list):
        domains = []
        for item in content:
            if isinstance(item, dict) and item.get("url"):
                domain = _domain(item["url"])
                if domain not in domains:
                    domains.append(domain)
        return f"{len(content)} results: {', '.join(domains[:4])}" if content else "no results"
    if name == "web_extract":
        text = str(content)
        if text.startswith("Could not extract"):
            return "page could not be read"
        return f"read {len(text):,} characters"
    return str(content)[:200]


async def stream_answer(
    *,
    title: str,
    url: str,
    text: str,
    published_at: datetime | None,
    entities: list[dict],
    history: list[tuple[str, str]],
    question: str,
) -> AsyncIterator[dict[str, Any]]:
    """Run the agent, yielding UI-shaped events.

    Event types: status | tool_call | tool_result | delta | result.
    The final event is always {"type": "result", "content", "tool_events"}.
    """
    agent = Agent(
        _model(),
        instructions=_instructions(title, url, text, published_at, entities),
        tools=_tools(),
        model_settings=ModelSettings(temperature=0.3, max_tokens=2000),
    )

    tool_events: list[dict] = []
    by_call_id: dict[str, dict] = {}
    output = ""

    async with agent.run_stream_events(
        question,
        message_history=_to_message_history(history),
        usage_limits=_LIMITS,
    ) as events:
        async for event in events:
            if isinstance(event, FunctionToolCallEvent):
                record = {
                    "name": event.part.tool_name,
                    "args": _tool_args(event.part),
                    "summary": None,
                }
                tool_events.append(record)
                by_call_id[event.part.tool_call_id] = record
                yield {
                    "type": "tool_call",
                    "id": event.part.tool_call_id,
                    "name": record["name"],
                    "args": record["args"],
                }
            elif isinstance(event, FunctionToolResultEvent):
                record = by_call_id.get(event.tool_call_id)
                summary = _summarize_tool_result(
                    record["name"] if record else "", event.part.content
                )
                if record is not None:
                    record["summary"] = summary
                yield {"type": "tool_result", "id": event.tool_call_id, "summary": summary}
            elif isinstance(event, PartStartEvent):
                if isinstance(event.part, TextPart) and event.part.content:
                    yield {"type": "delta", "text": event.part.content}
                elif isinstance(event.part, ThinkingPart):
                    yield {"type": "status", "state": "thinking"}
            elif isinstance(event, PartDeltaEvent):
                if isinstance(event.delta, TextPartDelta) and event.delta.content_delta:
                    yield {"type": "delta", "text": event.delta.content_delta}
            elif isinstance(event, AgentRunResultEvent):
                output = str(event.result.output)

    yield {"type": "result", "content": output.strip(), "tool_events": tool_events}
