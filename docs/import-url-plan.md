# Import-a-URL feature plan ("Imported")

User pastes any article URL → NewsRead extracts + summarizes it → it gets a normal
article page → appears in a new **Imported** sidebar page (history) → can be pinned to
projects and shared exactly like feed articles.

## Locked decisions

- **Architecture: hidden per-user system feed** (Option A). Each user gets one lazily
  created `Feed` row that only they are subscribed to. Everything downstream
  (`accessible_article`, article page, projects, shares, embeddings worker, related
  articles) works unchanged because access is subscription-based (`access.py:15-22`).
- **Dedup = copy, don't link.** If the URL already exists as an article anywhere, copy
  its content/summary columns into a new row in the user's import feed (no re-extract,
  no re-summarize) so it still appears in the user's Imported list.
- **Feeds the recommendation graph**: imported articles get embeddings/NER via the
  normal worker pipeline and participate in related-articles + project suggestions.
  (Automatic with Option A — no work needed.)
- **Naming: "Imported"** (sidebar label + page). Avoids collision with the existing
  Saved (bookmarks) tab.
- **Not in the main inbox.** Imported articles live on the Imported page only; the
  unified inbox/unread flow (and scroll-auto-read frontier) excludes them. Rationale:
  importing is "read this now", not "queue this in my stream", and letting them into
  the inbox would entangle unread counts and the auto-read frontier.

## Data model

One migration, one column:

- `feeds.owner_user_id: int | None` — FK → `users.id`, nullable, **unique**,
  `ondelete=CASCADE`. Non-null ⇒ this is that user's personal import feed.
  - No `kind` enum needed; `owner_user_id IS NOT NULL` is the discriminator.
- Import feed row: `url = f"newsread://imported/{user_id}"` (satisfies the unique
  non-null url), `title = "Imported"`, `ai_enabled = True` (so the worker pipeline
  picks its articles up), `image_gen_enabled = False` (imports usually have an
  og:image; avoid burning image budget).
- Imported `Article` rows: `guid = sha256(normalized_url)` — the existing
  `UniqueConstraint(feed_id, guid)` then gives per-user idempotent imports for free
  (guid is `String(1024)`, urls can be 2048, so hash rather than truncate).
  `published_at = now()` so the Imported list orders by import time.

Alembic migration in the usual chain (upgrade, not stamp).

## PR 1 — backend

### Endpoint: `POST /imports` (router `backend/app/routers/imports.py`)

Body `{url: str}`. Flow:

1. **Validate + normalize**: require http(s); strip tracking params (`utm_*`,
   `fbclid`, …); reject obviously non-page URLs. **SSRF guard**: resolve host and
   reject private/loopback/link-local ranges before fetching (this endpoint fetches
   arbitrary user URLs server-side and the public deploy proxies to the Mac).
2. **Get-or-create the import feed** (+ its `Subscription`) for the user.
3. **Idempotency**: if `(import_feed_id, sha256(url))` already exists → return the
   existing article with `200`.
4. **Copy-dedup**: else if any `Article` with the same normalized `url` exists
   globally, insert a new row in the import feed copying `title, author, url,
   content_html, excerpt, image_url, full_text, full_text_fetched_at, summary_short,
   summary_medium, summary, summary_model, summary_generated_at,
   summary_skipped_reason`. Embeddings/NER for the new row are recomputed by the
   worker (input-hash driven) — don't copy those tables.
5. **Fresh URL**: insert a stub row (title = URL host for now) and kick a
   background task that runs `extractor.enrich_article` → title/og-image backfill →
   `summarizer.generate_summaries(..., allow_vision=True)` with the user's LLM config
   via the same `_resolve_llm` path as `POST /articles/{id}/summarize`
   (`routers/ai.py:82`). Fetch failures never fail the import: keep the row with
   `summary_skipped_reason` set and let the page show "couldn't extract — open
   original".
6. Return `201` + `ArticleDetail` (the FE navigates straight to `/article/[id]`,
   which already renders a summarizing/pending state).

### Keep the system feed invisible + inert

- `routers/feeds.py::_feed_list_stmt` — add `Feed.owner_user_id.is_(None)` so it
  never appears in the sidebar feed list or feed settings.
- `worker.py::poll_feeds` — skip `owner_user_id IS NOT NULL` (its `newsread://` url
  is not fetchable).
- `routers/feeds.py` unsubscribe/settings endpoints — 404 for import feeds via the
  same filter in `_get_subscribed_feed` (unsubscribing from your own history makes
  no sense).
- `worker.py::suppress_articles_batch` — exclude import-feed articles from
  not-interested suppression (an explicit import is the strongest interest signal).
- `routers/articles.py::list_articles` + `_scoped_article_ids` — when `feed_id` is
  None (unified inbox), exclude `owner_user_id IS NOT NULL` feeds; when
  `feed_id=<import feed>` is passed explicitly, serve it (that IS the Imported page
  query). Search (`q=`) may keep imported articles in scope — they're the user's own.
  Frontier/auto-read endpoints inherit the inbox exclusion automatically since they
  share `_scoped_article_ids`.
- `GET /imports/feed` (tiny helper) — returns the user's import feed id (or creates
  it), so the FE doesn't guess.

### Tests (backend)

- Import fresh URL → article created in hidden feed, background summarize invoked.
- Same URL twice → 200 same article (idempotent).
- URL already in a subscribed feed → new copied row, summaries present, no LLM call.
- Import feed absent from `GET /feeds`; imported articles absent from inbox
  `GET /articles`, present in `GET /articles?feed_id=<import>`.
- SSRF: `http://127.0.0.1/...`, `http://10.0.0.5/...` → 400.
- Share + project-pin an imported article round-trip (serializer uses
  `feed.display_title` = "Imported" — assert it doesn't blow up).

## PR 2 — web frontend

- **Sidebar** (`components/Sidebar.tsx`): "Imported" entry in the fixed section
  (near Saved/Shared/Sent), plus a small "+ Add link" affordance (either on the
  entry or in the page header).
- **Page** `app/(app)/imported/page.tsx`: reuse `ArticleList` with the import
  feed id (from `GET /imports/feed`); empty state explains the feature with the
  add-link input front and center.
- **Add-link modal** (`components/ImportUrlModal.tsx`): URL input → `POST /imports`
  → navigate to `/article/[id]`. Show inline error on invalid URL; the article
  page's existing pending-summary state covers the async summarize.
- Article page needs no changes (feed name renders as "Imported"); verify the
  share modal + project picker work on an imported article.
- Tests to the FE 90% branch-coverage gate (vitest + jsdom; mock `fetch`).

## PR 3 — mobile (Expo)

- Mirror: Imported list screen + add-URL input; article screen already works by id.
- Follow-up (separate, later): OS share-sheet target ("Share to NewsRead") — the
  natural mobile entry point for this feature; needs a dev-client/native config
  change, so explicitly out of v1 (SDK 54 / Expo Go constraint).

## Explicitly out of scope (v1)

- Special handlers for PDFs / YouTube / tweets (trafilatura + screenshot-vision
  fallback is the v1 behavior for everything).
- Editing/deleting imports beyond the normal article affordances (a delete-import
  endpoint can come later if history hygiene matters).
- Browser extension / bookmarklet entry point.
