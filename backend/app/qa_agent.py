"""Article Q&A agent: pydantic_ai over any OpenAI-compatible endpoint, with
optional web search/extract tools.

Two search providers: Tavily (hosted, TAVILY_API_KEY) or SearXNG (self-hosted
metasearch, SEARXNG_BASE_URL). With SearXNG, page extraction runs locally via
the same scrapling + trafilatura pipeline the article enricher uses."""

import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
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
from scrapling.fetchers import AsyncFetcher
from tavily import AsyncTavilyClient

from . import llm
from .config import settings

logger = logging.getLogger(__name__)

# Hard cap on model round-trips per question; bounds latency and search spend.
_LIMITS = UsageLimits(request_limit=6)

_EXTRACT_MAX_CHARS = 8_000

_SEARCH_MAX_RESULTS = 5


def is_configured() -> bool:
    return bool(settings.openai_api_key and settings.openai_model)


def search_provider() -> str | None:
    """Which web-tool backend is configured: 'searxng', 'tavily', or None."""
    if settings.searxng_base_url:
        return "searxng"
    if settings.tavily_api_key:
        return "tavily"
    return None


def search_enabled() -> bool:
    return search_provider() is not None


QA_INSTRUCTIONS = """You are NewsRead's reading assistant. The user is reading the article below and asking questions about it.

Today's date: {today}.

Ground your answers in the article text. If web tools are available, use web_search only when the question needs information the article does not contain — product or model details, follow-up developments, background on people, companies or technologies. Use web_extract when a specific page (a search result, a link from the article) likely holds the answer but you only have a snippet. When you use information from the web, cite the source inline as a markdown link. If neither the article nor the web yields the answer, say so plainly.

Be concise. Answer in markdown.

Article title: {title}
Article URL: {url}
{published}
Article text:
{text}{entities}"""


PROJECT_QA_INSTRUCTIONS = """You are NewsRead's research assistant for the project "{name}". The user collects articles around this project and asks questions across the whole collection.

Today's date: {today}.

Ground your answers in the collected articles below — titles, summaries, each article's ticket status ("done" means the project considers it handled; no status means still open), and the members' discussion threads. When you draw on an article, cite it inline as a markdown link to its URL. If web tools are available, use web_extract to read an article's full text when its summary is not enough, and web_search only for information none of the articles contain. If neither yields the answer, say so plainly.

Be concise. Answer in markdown.
{description}
Collected articles (newest first):

{corpus}"""


async def web_search(query: str) -> list[dict] | str:
    """Search the web. Returns results with title, url and a content snippet."""
    base = settings.searxng_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{base}/search",
                params={"q": query, "format": "json"},
                # SearXNG expects a reverse proxy to set this; without it,
                # botdetection logs an ERROR on every request.
                headers={"X-Forwarded-For": "127.0.0.1"},
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning("web_search failed for %r: %s", query, exc)
        return f"Search failed: {exc}"
    return [
        {
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "content": item.get("content") or "",
        }
        for item in (data.get("results") or [])[:_SEARCH_MAX_RESULTS]
    ]


async def web_extract(url: str) -> str:
    """Fetch a web page and return its main content as text.

    Use when a specific page likely contains the answer but you only have its
    URL or a short snippet.
    """
    if search_provider() == "searxng":
        content = await _extract_local(url)
    else:
        content = await _extract_tavily(url)
    if content.startswith("Could not extract"):
        return content
    if len(content) > _EXTRACT_MAX_CHARS:
        content = content[:_EXTRACT_MAX_CHARS] + "\n\n[page truncated]"
    return content


async def _extract_tavily(url: str) -> str:
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
    return results[0].get("raw_content") or ""


async def _extract_local(url: str) -> str:
    """Extraction without Tavily: scrapling fetch (browser impersonation, like
    the article enricher) + trafilatura, as markdown so links survive for
    the agent's inline citations."""
    try:
        page = await AsyncFetcher.get(url, impersonate="chrome")
    except Exception as exc:
        logger.warning("web_extract failed for %s: %s", url, exc)
        return f"Could not extract {url}: {exc}"
    if page.status != 200:
        return f"Could not extract {url}: the page returned HTTP {page.status}."
    text = trafilatura.extract(
        page.html_content,
        output_format="markdown",
        include_links=True,
        include_comments=False,
    ) or ""
    if not text:
        return f"Could not extract {url}: the page returned no content."
    return _absolutize_links(text, url)


_MD_LINK_TARGET = re.compile(r"\]\(([^)\s]+)\)")


def _absolutize_links(markdown: str, base_url: str) -> str:
    """Resolve relative link targets so the agent can cite them verbatim."""
    return _MD_LINK_TARGET.sub(
        lambda m: f"]({urljoin(base_url, m.group(1))})", markdown
    )


def _tools() -> list:
    provider = search_provider()
    if provider == "searxng":
        return [web_search, web_extract]
    if provider == "tavily":
        return [
            tavily_search_tool(settings.tavily_api_key, max_results=_SEARCH_MAX_RESULTS),
            web_extract,
        ]
    return []


def _model(config: llm.LLMConfig | None = None) -> OpenAIChatModel:
    config = config or llm.system_config()
    if config is None:
        # Routes gate on is_configured(); this protects any direct caller.
        raise RuntimeError("No LLM is configured")
    return OpenAIChatModel(
        config.model,
        provider=OpenAIProvider(base_url=config.base_url, api_key=config.api_key),
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
    if name in ("tavily_search", "web_search") and isinstance(content, list):
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
    config: llm.LLMConfig | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run the article agent, yielding UI-shaped events.

    Event types: status | tool_call | tool_result | delta | result.
    The final event is always {"type": "result", "content", "tool_events",
    "usage"}.
    """
    async for event in _stream_agent(
        _instructions(title, url, text, published_at, entities), history, question, config
    ):
        yield event


async def stream_project_answer(
    *,
    name: str,
    description: str,
    corpus: str,
    history: list[tuple[str, str]],
    question: str,
    config: llm.LLMConfig | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Same event stream as stream_answer, over a project's collection."""
    instructions = PROJECT_QA_INSTRUCTIONS.format(
        today=datetime.now(timezone.utc).date().isoformat(),
        name=name,
        description=f"\nProject description: {description}\n" if description else "",
        corpus=corpus,
    )
    async for event in _stream_agent(instructions, history, question, config):
        yield event


async def _stream_agent(
    instructions: str,
    history: list[tuple[str, str]],
    question: str,
    config: llm.LLMConfig | None = None,
) -> AsyncIterator[dict[str, Any]]:
    agent = Agent(
        _model(config),
        instructions=instructions,
        tools=_tools(),
        model_settings=ModelSettings(temperature=0.3, max_tokens=2000),
    )

    tool_events: list[dict] = []
    by_call_id: dict[str, dict] = {}
    output = ""
    usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}

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
                run_usage = event.result.usage
                usage = {
                    "prompt_tokens": run_usage.input_tokens or 0,
                    "completion_tokens": run_usage.output_tokens or 0,
                }

    yield {
        "type": "result",
        "content": output.strip(),
        "tool_events": tool_events,
        "usage": usage,
    }
