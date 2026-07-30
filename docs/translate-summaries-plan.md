# Translate AI summaries (issue #101)

Translate an article's or video's AI **FULL** summary into the reader's language,
on demand, cached globally, on a configurable OpenRouter model.

## Locked decisions

| Decision | Choice |
| --- | --- |
| Scope | FULL summary only (`articles.summary`). Short/medium stay in the source language; titles and article body out of scope. |
| Trigger | Lazy — a Translate action per summary. Never automatic, never on list render. |
| Language | Asked once on first use, saved as `users.translation_language`, reused after. Changeable in web Settings and from the picker itself. |
| Model | Dedicated `NEWSREAD_TRANSLATION_*` config (mirrors `image_generation_*`), defaulting to the system LLM when unset. |
| RTL | Content-level bidirectional text on every article surface, web + mobile. The UI chrome is **not** mirrored. |
| Delivery | One PR: backend + web + mobile. |

Configured locally for testing: `NEWSREAD_TRANSLATION_MODEL=nvidia/nemotron-3-super-120b-a12b:free`
plus a key, with no base URL — so `translation_base_url` defaults to
`https://openrouter.ai/api/v1` (consulted only when a translation model is set).

## Why this is cheaper than it looks

- YouTube videos are `Article` rows (feed kind `youtube`, `source_kind="transcript"`),
  so "article + video" is a single code path — the second acceptance criterion is free.
- Summaries are already written in the source language (`llm._language_note`), so this
  is exactly "read a Hebrew summary in English", not a re-summarize.
- `lingua` is already a dependency: source-language detection costs no LLM call.

## Backend

### 1. Config (`config.py`)

```
translation_base_url  NEWSREAD_TRANSLATION_BASE_URL  / TRANSLATION_BASE_URL
translation_model     NEWSREAD_TRANSLATION_MODEL     / TRANSLATION_MODEL
translation_api_key   NEWSREAD_TRANSLATION_API_KEY   / TRANSLATION_API_KEY
```

All default `""`, same `AliasChoices` shape as the `image_generation_*` block. The
Nemotron free model ID goes in `.env.example` + README, not baked into a default —
free-model IDs rot, and the issue explicitly asks for it to be configurable.

`llm.translation_config()`:
- `translation_model` set → `LLMConfig(provider="custom", model, base_url=translation_base_url or None, api_key=translation_api_key or openai_api_key)`
- unset → `system_config()` (so self-hosters get translation with zero extra config)
- neither → `None` → endpoint answers 503 like the other AI routes.

Never billed to a user's BYO key: translation is an operator-funded, globally cached
resource, so `user_owned` stays false and it does not touch `llm_usage`.

### 2. Schema (Alembic `0015_summary_translations`)

```python
class SummaryTranslation(Base):
    __tablename__ = "summary_translations"
    __table_args__ = (UniqueConstraint("article_id", "language", "source_hash"),)

    id: int
    article_id: int          # FK articles.id ON DELETE CASCADE, indexed
    language: str            # BCP-47-ish code, String(16), e.g. "he", "pt-BR"
    source_hash: str         # String(64) sha256 of articles.summary at translation time
    text: str                # the translated FULL summary (GFM markdown)
    model: str | None        # String(120), which model produced it
    created_at: datetime
```

Global, not per-user — the second reader asking for Hebrew pays nothing (issue
requirement). `source_hash` makes regeneration invalidation automatic: a regenerated
summary hashes differently, misses the cache, and the stale row is simply never read
again. No cleanup job; rows are small. (A later GC can delete rows whose hash no longer
matches their article.)

Plus one column on `articles`:

```python
summary_language: str | None  # String(32), detected at generation time
```

Populated from the detection `_language_note` already runs (extended to also report
English instead of returning `None`). NULL on pre-existing rows and that is fine — the
translate endpoint re-detects on demand when it is NULL.

### 3. `backend/app/translation.py`

- `LANGUAGES`: ~25 entries `{code, name, native_name}` — the languages worth offering,
  single source of truth for both clients.
- `source_hash(summary: str) -> str`
- `translate_summary(session, article, language, *, config, usage) -> TranslationResult`
  1. 422 if `article.summary` is empty.
  2. Detected source language == target → return the original, `translated=False`, no LLM call.
  3. Cache lookup on `(article_id, language, source_hash)` → hit returns immediately, `cached=True`.
  4. One completion, insert, return. Insert races (two readers, same article+language)
     resolve with `ON CONFLICT DO NOTHING` + re-select.

Prompt (`TRANSLATION_SYSTEM`): translate into the named language; preserve the GFM
structure exactly (lists, tables, emphasis); no preamble, no notes, no commentary; keep
proper nouns and quoted material faithful; add nothing and drop nothing. The summary is
untrusted input — instruction-injection wording mirrors `HISTORY_SUMMARY_SYSTEM`.

### 4. Routes (`routers/ai.py`)

- `POST /articles/{id}/translate` — body `{ "language": "he" }`, authz via
  `accessible_article`. Returns
  `{ language, text, model, cached, translated, source_language }`.
  Wrapped in `llm.usage_tracker(feature="translation", …)`; failures raise
  `LLMRequestFailed` → 502, and **nothing about the article changes** — the original
  summary is untouched by construction, since translations live in their own table.
- `GET /translation/languages` — the `LANGUAGES` table for the pickers.
- `AiStatusOut` gains `translation: bool` so clients can hide the action entirely when
  no translation model is configured.

### 5. User preference

`users.translation_language: String(16) | None` → `UserOut`, and `UserUpdateIn` with
presence-based semantics (`"translation_language" in model_fields_set`), matching the
existing `image_gen_monthly_limit` pattern, so an explicit null clears it.

## Web

`AiSummary.tsx` grows a translate control in the header row next to the regenerate
button:

- No saved language → opens the language picker (`Modal.tsx`), saves it via
  `PATCH /users/me`, then translates.
- Saved language → translates straight away; a caret in the picker row still allows
  "translate to another language this once" without changing the default.
- While translating: reuse the existing skeleton, label "Translating…".
- After: render the translation, with a meta line — `Translated to Hebrew · <model> ·
  **Show original**`. Toggling is instant and client-side; both texts are held in
  component state, so the original is never lost (acceptance criterion).
- Error: `ErrorText` + "Try again", **original summary still rendered underneath**.
## RTL

There is no `dir` handling anywhere in either client today. Hebrew feeds have gotten away
with it because whole cards were Hebrew and browsers guess per paragraph; translating
*into* Hebrew or Arabic puts RTL text inside an otherwise LTR card, where punctuation and
alignment visibly break. Scope is **content direction, not UI mirroring** — the chrome
(sidebar, buttons, meta lines) stays LTR; anything that renders publisher or model text
gets its own direction.

Web — `dir="auto"` (the browser's own first-strong-character algorithm) on:
- `ArticleCard` / `ArticleRow` / `FeedArticleRow`: title, one-liner, expanded medium summary
- `StoriesView`: title + summary
- article detail: title, excerpt, `content_html` body, and the `AiSummary` block
- `RelatedArticles`, entity pages, and the imported/saved lists that reuse those rows

Mobile — React Native has no `dir="auto"`, and `writingDirection` is iOS-only, so a small
`src/lib/rtl.ts` implements the same first-strong-character test and returns
`{ textAlign, writingDirection }` for a string. Applied to the same surfaces
(`StoriesView`, article detail title/excerpt/summary markdown, related coverage).

Punctuation and neutral runs (dates, domains, `·` separators) are precisely what the
first-strong-character algorithm exists to place correctly, so the meta lines that mix
Latin metadata with RTL titles are left as their own LTR elements rather than inheriting
the title's direction.

Settings gets a **Translation** block (new `TranslationSettingsSection`, sibling of
`ReadingSettingsSection`): a language select bound to `users.translation_language`, plus
a "no default yet" state. Hidden when `/ai/status.translation` is false.

## Mobile

There is no settings screen in the Expo app, so the picker is the settings surface:

- `src/app/article/[id]/index.tsx` — a "Translate" pressable in the AI-summary card
  header, same three states, and a "Show original" toggle after success.
- A language picker sheet (RN `Modal` + `FlatList` over `GET /translation/languages`),
  shown on first use and reachable afterwards from a "Change language" row, saving the
  same `users.translation_language` — so the default set on the phone applies on the web
  and vice versa.
- `writingDirection: "auto"` on the summary markdown styles for RTL targets.
- API helper in `src/lib/articles.ts`.

## Tests

- **Backend**: cache hit skips the LLM; regenerated summary (new hash) misses; same-source-
  and-target-language short-circuits with no call; failure surfaces 502 and leaves
  `articles.summary` intact; `translation_config()` resolution and 503 when unconfigured;
  concurrent-insert conflict path.
- **Web** (vitest, repo gate is 90% branch coverage): first-use picker → PATCH → translate;
  saved-language path skips the picker; show-original toggle; error keeps the original;
  control hidden when translation is unconfigured.
- **Mobile**: picker + translate flow in the existing `src/lib/__tests__` style.

## Known trade-offs

1. **Title mismatch.** A translated summary sits under an untranslated headline, and the
   cards/list one-liners stay in the source language. Deliberate: titles are out of scope
   per the issue, and translating the list view would mean bulk calls on a free model.
2. **Free-tier rate limits.** OpenRouter `:free` models are limited per-minute and per-day.
   Failures degrade to "Try again" with the original still on screen. If 429s turn out to
   be common in practice, the follow-up is a comma-separated fallback chain in
   `TRANSLATION_MODEL` — the config shape here leaves room for it.
3. **Shared cache, per-user trigger.** Any logged-in user can spend an operator call to
   populate a globally readable row. Acceptable at this scale; a per-user hourly cap is the
   obvious lever if it is ever abused.
