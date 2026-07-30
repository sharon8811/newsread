"""Translating AI summaries into the reader's language.

Only the FULL summary is translated, and only when a reader asks: it is the one
a person actually sits down to read, and translating the card one-liners of a
scrolling list would spend a call on every article that scrolls past.

Translations are cached globally, keyed by (article, language, source hash) —
the second reader wanting Hebrew pays nothing, and a regenerated summary hashes
differently and simply misses the cache instead of serving a stale translation.
"""

import hashlib
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from . import llm
from .models import Article, SummaryTranslation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Language:
    code: str  # ISO 639-1
    name: str  # English name — also what the prompt names, and what lingua reports
    native_name: str
    rtl: bool = False


# The languages worth offering. Plain languages only, no regional variants:
# the source language comes from a detector that reports "Portuguese", so a
# "pt-BR" target would never compare equal to it and every Brazilian article
# would be "translated" into its own language.
LANGUAGES: tuple[Language, ...] = (
    Language("en", "English", "English"),
    Language("ar", "Arabic", "العربية", rtl=True),
    Language("zh", "Chinese", "中文"),
    Language("nl", "Dutch", "Nederlands"),
    Language("fr", "French", "Français"),
    Language("de", "German", "Deutsch"),
    Language("el", "Greek", "Ελληνικά"),
    Language("he", "Hebrew", "עברית", rtl=True),
    Language("hi", "Hindi", "हिन्दी"),
    Language("id", "Indonesian", "Bahasa Indonesia"),
    Language("it", "Italian", "Italiano"),
    Language("ja", "Japanese", "日本語"),
    Language("ko", "Korean", "한국어"),
    Language("fa", "Persian", "فارسی", rtl=True),
    Language("pl", "Polish", "Polski"),
    Language("pt", "Portuguese", "Português"),
    Language("ro", "Romanian", "Română"),
    Language("ru", "Russian", "Русский"),
    Language("es", "Spanish", "Español"),
    Language("sv", "Swedish", "Svenska"),
    Language("tr", "Turkish", "Türkçe"),
    Language("uk", "Ukrainian", "Українська"),
    Language("ur", "Urdu", "اردو", rtl=True),
    Language("vi", "Vietnamese", "Tiếng Việt"),
)

_BY_CODE = {language.code: language for language in LANGUAGES}

# Language *names* as lingua reports them, for the languages written right to
# left. The detector and this table share a vocabulary (checked by a test), so
# a stored `summary_language` answers "which way does this article read?"
# without re-detecting anything.
RTL_LANGUAGE_NAMES = frozenset(language.name for language in LANGUAGES if language.rtl)


def is_rtl_language(name: str | None) -> bool:
    return name in RTL_LANGUAGE_NAMES


def article_language(article: Article) -> str | None:
    """Detect and remember an article's language, from its summary when there
    is one and its title otherwise. Detection costs milliseconds, which is
    nothing once per article and far too much per row of a list — so callers
    must be on a single-article path, and the answer is stored."""
    if article.summary_language:
        return article.summary_language
    sample = article.summary or f"{article.title}\n{article.excerpt}"
    detected = llm.detect_language(sample)
    if detected is not None:
        article.summary_language = detected
    return detected


def language_for(code: str) -> Language | None:
    return _BY_CODE.get(code.strip().lower())


class UnknownLanguage(Exception):
    """The requested target language isn't one we offer."""


class NothingToTranslate(Exception):
    """The article has no summary yet, so there is nothing to translate."""


def source_hash(summary: str) -> str:
    return hashlib.sha256(summary.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TranslationResult:
    language: str
    text: str
    model: str | None
    # Whether the target language is written right to left. Sent to the client
    # because the direction of a translation is a fact about the language we
    # translated into — never something to guess from the text, which routinely
    # opens with a Latin brand name ("OpenAI משיקה…").
    rtl: bool
    cached: bool
    # False when the summary was already in the target language: `text` is the
    # original, untouched, and no model was called.
    translated: bool
    source_language: str | None


async def summary_language(session: AsyncSession, article: Article) -> str | None:
    """The language of the article's stored summary. Detected at generation
    time; articles summarized before that column existed are detected here and
    the answer written back, so the cost is paid once per article."""
    if article.summary_language:
        return article.summary_language
    detected = llm.detect_language(article.summary)
    if detected is not None:
        article.summary_language = detected
        session.add(article)
        await session.commit()
    return detected


async def translate_summary(
    session: AsyncSession,
    article: Article,
    code: str,
    *,
    config: llm.LLMConfig | None = None,
    usage: llm.TokenUsage | None = None,
) -> TranslationResult:
    """Translate one article's FULL summary, serving the shared cache first."""
    language = language_for(code)
    if language is None:
        raise UnknownLanguage(code)
    if not article.summary:
        raise NothingToTranslate()

    source = await summary_language(session, article)
    if source is not None and source == language.name:
        # Nothing to do, and saying so is more honest than paying a model to
        # rewrite a Hebrew summary into Hebrew.
        return TranslationResult(
            language=language.code,
            text=article.summary,
            model=None,
            rtl=language.rtl,
            cached=False,
            translated=False,
            source_language=source,
        )

    digest = source_hash(article.summary)
    cached = await _cached(session, article.id, language.code, digest)
    if cached is not None:
        return TranslationResult(
            language=language.code,
            text=cached.text,
            model=cached.model,
            rtl=language.rtl,
            cached=True,
            translated=True,
            source_language=source,
        )

    text = await llm.translate(article.summary, language.name, config=config, usage=usage)
    model = config.model if config is not None else None
    await _store(session, article.id, language.code, digest, text, model)
    return TranslationResult(
        language=language.code,
        text=text,
        model=model,
        rtl=language.rtl,
        cached=False,
        translated=True,
        source_language=source,
    )


async def _cached(
    session: AsyncSession, article_id: int, code: str, digest: str
) -> SummaryTranslation | None:
    result = await session.execute(
        select(SummaryTranslation).where(
            SummaryTranslation.article_id == article_id,
            SummaryTranslation.language == code,
            SummaryTranslation.source_hash == digest,
        )
    )
    return result.scalar_one_or_none()


async def _store(
    session: AsyncSession, article_id: int, code: str, digest: str, text: str, model: str | None
) -> None:
    """Insert the translation, conceding to whoever got there first: two
    readers can ask for the same language at the same time, and both answers
    are equally good."""
    await session.execute(
        insert(SummaryTranslation)
        .values(
            article_id=article_id,
            language=code,
            source_hash=digest,
            text=text,
            model=model,
        )
        .on_conflict_do_nothing(index_elements=["article_id", "language", "source_hash"])
    )
    await session.commit()
