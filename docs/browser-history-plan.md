# Browser History feature plan

> **Status:** Phase 1 committed; Phase 2 in progress; amended review decisions preserved
> (normalization contract, counter regression, offline-deletion tradeoff,
> public-deployment privacy posture, dev seed script)
>
> **Date:** July 23, 2026
>
> **Working name:** History
>
> **Reference prototype:** `/Users/sharontourjeman/chrome-smart-history`

## Goal

Add a Chrome extension that captures useful context from pages the user visits,
syncs it to their NewsRead instance, and powers a new **History** page where they
can find previously visited pages by meaning, keyword, domain, and date.

The feature should feel like a private memory layer for the web:

- “What was that article about React rendering I read last month?”
- “Show the pages I visited about local embedding models.”
- “Which GitHub repository did I open yesterday?”

This is separate from **Imported**. Importing is an explicit action that creates a
normal NewsRead article and participates in summaries, projects, sharing, and
related coverage. History is automatic, private, high-volume, and should not enter
the inbox or trigger article enrichment.

## Proposed defaults

These are recommendations, not locked decisions. Review them before implementation.

| Decision | Proposed choice | Rationale |
|---|---|---|
| Storage architecture | Sync history to the NewsRead backend | A Next.js page cannot directly read extension IndexedDB; server storage enables the integrated page and cross-device search |
| Data model | Dedicated history tables, not `Article`/Imported rows | Avoids inbox noise, subscriptions, summaries, NER, image generation, and article retention rules |
| Embeddings | Generate on the backend with NewsRead's configured embedding model | Keeps query and document vectors in the same model space and does not depend on Chrome's experimental `SemanticEmbedder` |
| No embedding provider | Fall back to PostgreSQL full-text/substring search | History stays useful without AI configuration |
| Captured content | Title, safe URL, domain, timestamps, and up to 6,000 characters of visible text | Enough semantic context without storing full page archives |
| Visit storage | Aggregate per page and connected browser, not an append-only visit log | Supports recency and visit count without unbounded event growth |
| Existing Chrome history | Optional metadata-only backfill | Chrome can provide old URLs/titles/times, but not the old page body |
| Retention | 90 days by default, configurable to 30/90/365/forever | Matches the prototype's privacy posture while allowing intentional long-term memory |
| Incognito | Never capture; declare the extension unavailable in incognito | Avoids surprising collection of deliberately private browsing |
| Extension location | Add `extension/` to this repository and adapt the prototype into it | Keeps the API contract, tests, and release version aligned with NewsRead |
| Mobile | No mobile capture or History page in v1 | The source is Chrome desktop; mobile can read synced history later if useful |
| Product rollout | Feature flag until extension pairing, deletion, and privacy controls are complete | Prevents exposing an unusable or unsafe partial surface |

## Why the reference prototype is not the final architecture

The prototype proves the core interaction: extract text after a page loads, index
it, and retrieve pages using semantic similarity. Several details should change for
NewsRead:

- It is local-only, so a NewsRead web page cannot access its IndexedDB.
- It captures pages from future tab loads; despite requesting the `history`
  permission, it does not currently enumerate old Chrome history.
- Its vectors come from Chrome's experimental `SemanticEmbedder`. That API should
  be treated as an optional future optimization, not a production dependency.
- Its offscreen setup needs correction before reuse: the manifest is missing the
  `offscreen` permission, `EMBEDDING` is not a documented offscreen reason, and the
  offscreen document must be ensured immediately before messaging it.
- It uses both a content script and `scripting.executeScript` for extraction. The
  production extension should have one capture path and one retryable sync queue.

Useful pieces to adapt:

- DOM extraction priority (`article` → `main` → visible body text)
- URL-based deduplication and content hashes
- local IndexedDB queue
- pause, retention, clear, and index-current-tab controls
- popup search/status interaction patterns

Pieces not to carry over:

- local vector ranking as the only search path
- hard dependency on Canary/EPP flags
- raw query-string URL storage
- broad capture without domain/privacy controls

## User experience

### First-time setup

Setup lives in an integration/installation section under **Settings → Browser
history**. The extension requires Chrome or a Chromium-based browser, and the
settings page detects the current browser (user-agent / `navigator.userAgentData`
brand check): when NewsRead is not being viewed from Chrome/Chromium, the pairing
flow still works but shows a clear disclaimer that the extension must be
installed in Chrome/Chromium, with the pairing token intended to be entered
there.

1. User opens **Settings → Browser history** in NewsRead.
2. NewsRead explains what is captured and what is excluded.
3. User creates a connection named after the browser/device, for example
   “Sharon's MacBook Chrome.”
4. NewsRead displays a one-time pairing token.
5. User installs the extension, enters their NewsRead server URL and token, and
   verifies the connection.
6. User chooses whether to import existing Chrome history metadata.
7. Extension begins capturing new page loads and syncing in the background.

The normal NewsRead login JWT must never be copied from browser `localStorage` into
the extension.

### History page

Add **History** near Imported/Saved in the fixed sidebar section.

The sidebar link is shown only when history is actually enabled for the user:
the feature flag is on **and** the user has at least one non-revoked browser
connection (or stored history). Until then, the feature is discoverable only
through **Settings → Browser history**, not the sidebar. Direct navigation to
`/history` while it is not enabled shows the setup/empty state (flag on) or the
standard not-found handling (flag off) — it never renders an unusable page.

The `/history` page contains:

- a prominent natural-language search field;
- recent pages when the search field is empty;
- result cards with title, safe URL/domain, snippet, last visit, visit count, and
  source browser;
- date range and domain filters;
- sort by relevance or most recent;
- open-original, delete-page, and exclude-domain actions;
- connection and sync health status;
- an empty state that links to extension setup.

Search is scoped to History in v1. A later global search could fuse articles,
projects, Imported, and browsing history.

### Extension popup

Keep the popup operationally small:

- capture on/off;
- connected NewsRead instance and last successful sync;
- queued/failed item count;
- “Index current tab”;
- “Open History in NewsRead”;
- “Options.”

Search belongs primarily on the NewsRead History page. A compact popup search can
be retained later if users find it valuable.

### Extension options

- server URL and connection state;
- disconnect/re-pair;
- retention preference;
- existing-history metadata import;
- excluded domains;
- capture mode: metadata + text or metadata only;
- clear local queue;
- request server-side deletion of all synced history.

## System design

```text
Chrome page load
    ↓
Extract visible text + normalize URL
    ↓
IndexedDB outbox (retryable, content-hash deduped)
    ↓ batch sync with extension credential
FastAPI /api/history/sync
    ↓
Postgres history pages + per-connection aggregates
    ↓
Worker embeds new/changed text with configured model
    ↓
/api/history hybrid search
    ↓
Next.js /history
```

The local outbox is a delivery queue, not the source of truth after sync. Clearing
the outbox must not imply server deletion, and server deletion must be represented
as a tombstone or acknowledged command so stale queued records cannot immediately
recreate deleted data.

## Data model

Names are provisional.

### `browser_connections`

One row per paired extension installation.

| Column | Notes |
|---|---|
| `id` | Primary key; also the source-device identity |
| `user_id` | Owner FK, indexed |
| `name` | User-visible browser/device name |
| `token_prefix` | Short non-secret identifier for UI and lookup |
| `token_hash` | SHA-256 of a randomly generated high-entropy token; never store plaintext |
| `created_at` | Pairing time |
| `last_seen_at` | Updated on authenticated sync |
| `revoked_at` | Non-null immediately rejects future requests |

The raw token is shown once. Use a recognizable prefix such as `nrh_` so logs and
support reports can identify the credential type without exposing it. `token_prefix`
is unique and indexed so authentication can select one candidate row before comparing
the full hash.

### `browser_history_pages`

One row per `(user, normalized URL)`.

| Column | Notes |
|---|---|
| `id` | Primary key |
| `user_id` | Owner FK, indexed; every query must scope by it |
| `url_hash` | SHA-256 of normalized URL |
| `url` | Safe openable URL with fragment and sensitive tracking/auth parameters removed |
| `title` | Last non-empty captured title |
| `hostname` | Normalized hostname, indexed |
| `text` | Extracted visible text, capped |
| `text_excerpt` | Short result-card snippet or generated from `text` |
| `content_hash` | Hash of the exact embedding input |
| `first_visited_at` | Earliest aggregate across connections |
| `last_visited_at` | Latest aggregate across connections, indexed |
| `visit_count` | Cached sum across connection aggregates |
| `captured_at` | When body text was last captured |
| `created_at` / `updated_at` | Audit timestamps |

Constraints:

- unique `(user_id, url_hash)`;
- reject non-HTTP(S) URLs at both schema and service layers;
- URLs and text are private to the owning user—no global copy-dedup like Imported.

### `browser_history_page_connections`

Per-connection absolute counters make retries idempotent and allow safe aggregation
across multiple browsers.

| Column | Notes |
|---|---|
| `page_id` | History page FK |
| `connection_id` | Browser connection FK |
| `first_visited_at` | Earliest time reported by this browser |
| `last_visited_at` | Latest time reported by this browser |
| `visit_count` | Absolute browser-side count, not an increment |
| `updated_at` | Last sync |

Unique `(page_id, connection_id)`.

Absolute counters can go backward if the user clears extension local storage
without re-pairing (same token, reset local counters). The server therefore
applies `max(existing, incoming)` per connection rather than blindly overwriting,
and takes the min/max of the reported first/last visit times. A reinstall with
re-pairing creates a new connection row, so its counts aggregate correctly on
their own.

### `browser_history_embeddings`

Mirror the useful guarantees of `ArticleEmbedding`:

- `page_id` primary key/FK;
- `model`;
- dimension-less pgvector `embedding`;
- `input_hash`;
- `embedded_at`.

Search filters by the currently configured model. A model change temporarily falls
back to keyword results until the worker re-embeds stale rows.

### `browser_history_settings`

One row per user, created lazily.

| Column | Notes |
|---|---|
| `user_id` | Primary key/FK |
| `retention_days` | `30`, `90`, `365`, or null for forever |
| `sync_revision` | Monotonic server revision for rules and deletion tombstones |
| `created_at` / `updated_at` | Audit timestamps |

Capture on/off remains per extension. Retention is server-owned because cleanup acts
on the shared cross-browser corpus.

### `browser_history_domain_rules`

Server-owned rules synchronize privacy choices across connections.

| Column | Notes |
|---|---|
| `id` | Primary key |
| `user_id` | Owner FK, indexed |
| `hostname` | Lowercase exact hostname or explicit suffix rule |
| `mode` | `exclude` or `metadata_only` |
| `created_at` / `updated_at` | Audit timestamps |

Unique `(user_id, hostname)`. Extension health/sync responses include the current
rules and a revision so the extension can enforce them before local queueing. The
backend also enforces them defensively during sync.

### `browser_history_deletions`

Small tombstones make deletion durable against an extension that was offline when
the user deleted data.

| Column | Notes |
|---|---|
| `id` | Primary key |
| `user_id` | Owner FK, indexed |
| `scope` | `page`, `domain`, or `all` |
| `scope_key` | URL hash for a page, hostname for a domain, empty for all |
| `revision` | Server-assigned revision that made older queued captures stale |
| `created_at` | Audit/cleanup timestamp |

Each queued capture records the last server `sync_revision` known to that extension.
Before upserting, sync checks the newest applicable page/domain/all tombstone and
rejects a capture whose known revision is older. This avoids trusting client clocks.
After receiving the new revision, a genuine later revisit can recreate a deleted
page; an old queued capture cannot. “Exclude domain” is different from “delete
domain”: exclusion blocks future capture, while a deletion tombstone invalidates
already queued data.

Accepted tradeoff: a *genuine* revisit captured while the extension is offline
still carries the pre-deletion revision and is rejected along with the stale
queue. This loses a small amount of legitimate data in exchange for never
trusting client clocks. The user-facing privacy documentation must state it
plainly: “pages you visit shortly after deleting history, while the extension is
offline, may not be recorded until the extension reconnects.”

## URL and capture policy

URL normalization is security-sensitive and belongs in one shared extension module
plus a defensive backend implementation.

The normalization contract must be explicit about who owns the final URL, because
the backend cannot see the page and therefore cannot recompute a
`<link rel="canonical">` choice:

- the extension computes the final normalized URL (including any canonical
  preference) and sends it;
- the backend **validates** that URL — scheme, host rules, stripped parameters,
  length — and rejects it if invalid; it never re-derives a different normalized
  URL, so extension and backend can never disagree on `url_hash`;
- if a page's canonical URL changes between visits, the new capture simply creates
  or updates a different row; no attempt is made to merge rows across canonical
  changes in v1.

Always:

- accept only `http:` and `https:`;
- remove fragments;
- lowercase scheme and hostname;
- remove default ports;
- strip known tracking parameters (`utm_*`, `fbclid`, `gclid`, `mc_*`, etc.);
- strip parameters whose names suggest secrets (`token`, `access_token`, `auth`,
  `session`, `code`, `key`, `signature`, and similar);
- prefer a valid same-origin `<link rel="canonical">` when present;
- cap stored URL length;
- never log page text or full URLs at normal log levels.

Default exclusions:

- incognito;
- extension/browser internal pages;
- NewsRead's own UI and API origins;
- localhost, loopback, and private-network hosts;
- browser new-tab/search settings pages;
- user-configured domain suffixes.

Sensitive-domain policy needs deliberate product review. Webmail, banking, health,
password managers, cloud consoles, internal company tools, and document editors can
contain valuable history and highly sensitive content. Recommended v1 behavior:

- ship a conservative default metadata-only domain list;
- let the user explicitly enable text capture per excluded domain;
- show the current capture mode clearly in extension options;
- do not attempt to inspect form fields, input values, or page storage.

Text extraction:

- use `innerText`/visible text, not raw `textContent`;
- prefer `article`, then `main`, then body;
- include title and meta description;
- collapse whitespace;
- cap at 6,000 characters;
- send metadata-only when extraction fails or content is too thin;
- recapture and re-embed only when the content hash changes.

PDFs, browser viewers, authenticated downloads, video transcripts, and iframe-only
content are metadata-only in v1.

## API design

All response and request schemas must enter the checked-in OpenAPI document and
regenerate frontend types.

### Normal session-authenticated endpoints

- `POST /api/history/connections`
  - creates a connection and returns the raw token once;
  - rate-limit token creation;
  - response must set `Cache-Control: no-store`.
- `GET /api/history/connections`
  - lists name, prefix, created/last-seen/revoked state; never token/hash;
  - the response (or a small summary field on an existing bootstrap endpoint)
    also tells the frontend whether the user has any active connection or
    stored history, so the sidebar can decide History-link visibility without
    an extra request per page load.
- `DELETE /api/history/connections/{id}`
  - revokes immediately;
  - does not delete history unless requested separately.
- `GET /api/history`
  - parameters: `q`, `hostname`, `date_from`, `date_to`, `sort`, `limit`, cursor;
  - keyset pagination for recency; ranked pagination for search.
- `DELETE /api/history/{page_id}`
  - writes a page tombstone, then owner-scoped hard delete plus cascades.
- `DELETE /api/history`
  - explicit confirmation body;
  - supports all history or one hostname;
  - writes and returns a deletion tombstone acknowledged by the extension.
- `GET/PATCH /api/history/settings`
  - retention and current policy revision.
- `GET/POST/DELETE /api/history/domain-rules`
  - list, create/update, or remove synchronized `exclude`/`metadata_only` rules;
  - optionally delete already stored records when a rule is created.

### Extension-authenticated endpoints

- `POST /api/history/sync`
  - bearer token resolves a non-revoked `browser_connections` row;
  - batch of at most 100 records;
  - request size cap;
  - each record includes the server revision known when it was queued;
  - idempotent absolute per-connection aggregates;
  - upserts metadata/text only when the incoming capture is newer or fills a blank;
  - applies server-side domain rules even if the extension is stale or malicious;
  - rejects captures whose known revision predates an applicable deletion tombstone;
  - returns accepted/rejected record identifiers, current deletion state, domain
    rule revision, and server time.
- `GET /api/history/sync/status`
  - lightweight pairing/health check;
  - returns connection name, user display name, retention, and capture policy.

Do not make extension credentials valid for the normal NewsRead API. The extension
dependency should authenticate only the small `/history/sync*` surface.

## Search and indexing

Reuse the approach in `routers/articles.py::_hybrid_search_ids`, but keep history
scoping and tables separate.

Keyword leg:

- PostgreSQL generated `tsvector` over weighted title, hostname, and text;
- `websearch_to_tsquery`;
- ILIKE fallback for queries that produce no useful English tsquery or when tests
  run without the production generated column.

Vector leg:

- embed normalized query with `embeddings.embed_query`;
- cosine distance over current-model history vectors;
- exact scan initially; revisit an ANN index only after measuring real corpus size;
- fuse keyword and vector rankings with the existing reciprocal-rank fusion helper.

Embedding input:

```text
{title}

{hostname}

{visible text}
```

Capped consistently and hashed. History embeddings do not trigger summaries, NER,
image generation, related-article links, dislike suppression, or usage-facing LLM
features.

The worker gets a bounded history-embedding batch alongside the existing article
embedding work. Failures leave rows keyword-searchable and retryable.

## Retention and deletion

- Run retention cleanup in the worker once per day.
- Delete rows whose `last_visited_at` is older than the user's retention period.
- Cascade embeddings and per-connection aggregates.
- Deleting a connection does not implicitly delete its pages because another
  connection may also have visited them.
- “Delete this page,” “Delete this domain,” and “Delete all history” must be
  available from NewsRead.
- Page/domain/all tombstones use monotonic server revisions to reject previously
  queued captures, preventing offline replay without trusting client clocks.
- Revoking a token takes effect immediately.
- Account deletion cascades every history and connection row.

Export is a follow-up unless NewsRead adds a general account-data export first.

## Implementation phases

Each phase should be one reviewable PR. Phase 4 may live in the same repository but
produce a separately packaged Chrome extension artifact.

### Phase 1 — Backend foundation and connection auth (M)

- [x] Add models and Alembic migration for connections, settings, domain rules,
      pages, per-connection aggregates, deletion tombstones, and embeddings.
- [x] Add high-entropy token generation, hashing, prefix lookup, revocation, and a
      dedicated extension-auth dependency.
- [x] Add connection create/list/revoke endpoints.
- [x] Add History settings and domain-rule schemas with the proposed defaults.
- [x] Add feature flag and expose its effective value through `GET /api/config`.
- [x] Add ownership, token, revocation, cascade, and secret-non-disclosure tests.

Merge gate:

- migration upgrade/downgrade reviewed;
- plaintext extension tokens never persist;
- an extension token cannot access any non-sync protected endpoint;
- cross-user connection access returns 404.

### Phase 2 — Sync, retention, and hybrid search (L)

Normalization, sanitization, request limits, idempotent batch sync, absolute
per-connection aggregates, synchronized-rule enforcement, and stale-tombstone
rejection are implemented. Search, deletion endpoints, embeddings, retention,
and the dev seed script remain.

- [ ] Implement defensive URL normalization and capture validation, including
      the per-field sanitization from “Untrusted-content and injection
      defenses” (control/bidi stripping, timestamp clamping, count caps,
      wildcard-escaped ILIKE).
- [x] Add batch sync with absolute per-connection visit aggregates.
- [x] Enforce synchronized domain rules and expose their revision to extensions.
- [ ] Add content-hash stale detection and bounded worker embedding.
- [ ] Add hybrid search/list endpoint with filters and pagination.
- [ ] Add page/domain/all deletion tombstones and stale-queue protection.
- [ ] Add daily retention cleanup.
- [ ] Add a small dev-only seed/sync script that pushes realistic fake history
      through the real sync endpoint, so Phase 3 UI work can be visually
      verified against a populated corpus before the extension exists.
- [ ] Export OpenAPI and regenerate frontend types.
- [ ] Cover idempotent retry, out-of-order capture, multi-connection aggregation,
      keyword-only operation, current-model vectors, deletion, and retention.

Merge gate:

- retrying the same batch does not increase counts;
- revoked tokens are rejected;
- no query can return another user's history;
- search works with embeddings disabled or failed;
- deletion cannot be undone by replaying an old queued batch.

### Phase 3 — NewsRead web UI (M–L)

- [x] Add History sidebar link, shown only when the feature flag is on **and**
      the user has an active connection or stored history; otherwise the only
      entry point is Settings → Browser history.
- [x] Add `/history` with search, filters, loading/error/empty states, ranked and
      recent results, delete actions, and domain exclusion.
- [x] Add Settings → Browser history (integration/installation section) with
      connection creation, one-time token reveal, copy action, connection
      health, revoke, retention, and clear-all.
- [x] Detect the current browser in the settings page and show a
      Chrome/Chromium-required disclaimer when NewsRead is opened from another
      browser, without blocking token creation.
- [x] Add SWR keys/hooks/mutators and generated API aliases.
- [x] Add frontend tests to the existing 90% branch-coverage gate.
- [x] Verify keyboard navigation, screen-reader labels, destructive confirms,
      dark mode, narrow desktop widths, and long URL/title handling.

Phase 3 currently consumes a bounded 50-row keyword result set. Cursor
pagination, tsvector ranking, embeddings, hybrid fusion, retention cleanup, and
the realistic seed script remain explicit Phase 2 follow-ups above.

Merge gate:

- one-time secret is not recoverable after leaving the creation state;
- deleting results updates all relevant SWR caches;
- the page is hidden and directly routed access is handled when the flag is off;
- the sidebar link never appears for a user with no connection and no history,
  and direct `/history` access in that state lands on the setup/empty state;
- visual verification against the running backend.

### Phase 4 — Chrome extension (L)

- [ ] Add `extension/` and adapt—not copy blindly—the reference prototype.
- [ ] Choose and document the minimal build/test setup; TypeScript is preferred if
      it does not complicate Chrome Web Store packaging.
- [ ] Correct MV3 permissions and offscreen handling; omit offscreen entirely if
      no browser-local model needs it.
- [ ] Use optional runtime host permissions for the selected NewsRead origin rather
      than permanently granting the extension network access to arbitrary origins.
- [ ] Implement pairing, connection health, and revocation handling.
- [ ] Implement one DOM extraction path.
- [ ] Implement IndexedDB outbox, content-hash dedup, bounded batches, a maximum
      queue age, exponential retry with jitter, alarms, and visible failure state.
- [ ] Implement pause, index-current, exclusions, capture modes, and open-History.
- [ ] Optionally import old `chrome.history` metadata with progress/cancel.
- [ ] Add unit tests using mocked Chrome APIs plus a manual unpacked-extension test
      checklist.
- [ ] Add packaging/version instructions and required license/NOTICE attribution
      for reused prototype code.

Merge gate:

- normal browsing does not produce unhandled service-worker errors;
- offline captures sync after reconnection;
- no text or token appears in extension logs;
- pause and exclusion rules are enforced before queueing;
- incognito capture is impossible;
- Chrome Stable works without `SemanticEmbedder`.

### Phase 5 — End-to-end hardening and rollout (M)

- [ ] Run the full backend and frontend test suites.
- [ ] Use the repo verification workflow to launch FastAPI + Next.js and drive the
      complete pair → visit → sync → search → delete → revoke journey.
- [ ] Test multiple NewsRead users and two extension connections for one user.
- [ ] Test large queues, backend downtime, expired/revoked credentials, retention,
      server URL changes, and model changes.
- [ ] Measure sync request size, embedding backlog, query latency, and database
      growth on a realistic corpus.
- [ ] Add user-facing privacy documentation and extension permission explanations.
- [ ] Enable the feature for self-hosted deployments; keep public deployment
      rollout separately controlled until abuse/rate limits are verified.

## Test matrix

### Backend

- token creation is one-time and `Cache-Control: no-store`;
- hashes/prefixes cannot authenticate after revocation;
- connection/user isolation;
- normalization strips fragments, tracking, and sensitive parameters;
- malformed, oversized, private-host, and non-HTTP records are rejected per item
  without failing a valid batch;
- titles/text containing script tags, control characters, bidi overrides, and
  SQL/ILIKE metacharacters are stored inert and searchable without side
  effects;
- `javascript:` and `data:` URLs are rejected; future timestamps are clamped;
  absurd visit counts are capped;
- search queries containing `%`, `_`, quotes, and tsquery operators return
  safely instead of erroring or matching everything;
- batch replay and out-of-order delivery;
- content only updates from a newer capture;
- two connections produce correct first/last/count aggregates;
- a regressed absolute count (cleared extension storage, same token) never
  lowers the stored count;
- keyword-only and hybrid search ordering;
- embedding model switch and stale re-embed;
- page/domain/all deletion;
- retention boundaries and account cascade.

### Frontend

- flag-gated sidebar and direct route;
- sidebar link hidden until a connection or history exists, appears after
  pairing, and disappears again after the last connection is revoked and
  history is cleared;
- Chrome/Chromium disclaimer shown for non-Chromium user agents and absent in
  Chrome;
- recent and searched states;
- debounce and stale-response behavior;
- domain/date/sort filters;
- pairing secret reveal/copy/dismiss;
- revoke and clear confirmations;
- API failure and empty states;
- accessible result and destructive controls;
- a result whose title/excerpt is an XSS payload renders as inert text;
- result links carry `rel="noopener noreferrer"` and only http(s) hrefs are
  rendered;
- highlighted search matches never pass through an HTML string.

### Extension

- first pair, reconnect, bad URL/token, and revoked token;
- ordinary article/main/body extraction;
- unsupported URL/content types;
- denylist, metadata-only mode, pause, and index-current;
- offline queue and retry;
- duplicate page loads/content hashes;
- service-worker suspension and restart;
- batch partial rejection;
- existing-history metadata import and cancellation;
- server deletion tombstone handling;
- no incognito operation;
- a web page cannot spoof extension-internal messages;
- popup/options render hostile titles as text, not markup.

### End to end

- newly visited page appears within 60 seconds under normal connectivity;
- revisiting updates time/count without duplicating the result;
- concept search finds captured content when embeddings are configured;
- exact title/domain search works without embeddings;
- deleting a page removes it from search and it stays deleted;
- revoking the connection stops the next sync;
- a second user cannot observe any page, domain, count, or connection metadata.

## Operational limits for v1

Initial values to validate under load:

- 100 records per sync batch;
- 1 MiB maximum sync request;
- 6,000 captured text characters per page;
- 200 results in each vector/keyword candidate pool;
- 50 results maximum per response;
- 20,000 entries maximum for optional initial metadata import;
- 90-day default retention;
- exact pgvector scans until measurements justify an ANN index.

Return `413`/structured per-item errors rather than silently truncating batches at
the transport layer. Extension backoff must honor `429` and `Retry-After`.

## Untrusted-content and injection defenses

Every stored field in this feature originates from arbitrary web pages (title,
text, URL, hostname) or from a client that may be compromised or malicious (the
extension holding a sync token). Treat all of it as hostile input at every
layer; no field is “ours” except server-generated ids and timestamps.

### Rendering in the web UI

- Titles, excerpts, text, hostnames, and connection names render as plain text
  through React's default escaping only — never `dangerouslySetInnerHTML` and
  never the markdown renderers used for article summaries. History text is not
  markdown and must not be interpreted as any markup language.
- Search-term highlighting is built from React element trees (split + wrap),
  never by assembling an HTML string.
- “Open original” re-validates on the client that the URL scheme is `http:` or
  `https:` before rendering an anchor, even though the backend already enforces
  it — a defense-in-depth guard against `javascript:` and `data:` URLs ever
  reaching an `href`.
- All history links open with `target="_blank"` and
  `rel="noopener noreferrer"` so a hostile destination page cannot reach the
  NewsRead tab through `window.opener` (reverse tabnabbing).
- Hostnames render with IDN spoofing in mind: show punycode (or a
  spoof-checked Unicode form) so `аpple.com` cannot impersonate `apple.com` in
  result cards and domain filters.

### Persistence and query layer

- All database access goes through the ORM with bound parameters; no SQL is
  ever assembled from captured strings.
- The search string is passed only as a bound parameter to
  `websearch_to_tsquery` (which is designed for raw user input) and to ILIKE
  with `%`/`_`/`\` escaped so user input cannot act as wildcards.
- Sync performs strict per-field validation before persisting anything:
  scheme whitelist, RFC-valid hostname syntax, length caps on URL/title/
  text/excerpt/connection name, valid UTF-8, Unicode control and bidi-override
  and zero-width characters stripped from title and text, timestamps clamped
  to a sane window ending at server time, and visit counts capped at a sane
  maximum. Invalid items are rejected per item with structured errors, never
  silently repaired into something dangerous.
- Domain rules match by exact hostname or explicit suffix comparison — never
  by user-supplied regex (no ReDoS surface).

### Extension

- No `externally_connectable` in the manifest, and the background worker
  verifies `sender` on every runtime message, so an arbitrary web page can
  never spoof extension-internal messages or trigger capture/sync.
- Popup and options pages render captured titles and URLs with
  `textContent`/DOM APIs only, never `innerHTML` — the extension UI is just as
  much an XSS target as the web app.
- Extraction reads `innerText` of rendered content only; it never evaluates or
  reserializes page HTML.

### Downstream and future surfaces

- Captured text flows only into embeddings, which are injection-inert. Any
  future LLM feature over history (Q&A, summaries, recommendations) must treat
  page text as prompt-injection-hostile input; such features stay out of scope
  for v1 precisely so this boundary is deliberate.
- The server never fetches user-visited URLs, so there is no SSRF surface to
  harden; keep it that way.
- If a data export is added later, guard CSV/spreadsheet formula injection
  (`=`, `+`, `-`, `@` prefixes) in every captured field.

## Privacy and security review checklist

- [ ] Broad host permission copy explains that visible page text may be captured.
- [ ] Incognito is disabled in the manifest and enforced in code.
- [ ] Pairing tokens are scoped, revocable, high entropy, hashed, and shown once.
- [ ] Tokens and page content are redacted from server and extension logs.
- [ ] Sensitive URL parameters are stripped before local queueing, not only server-side.
- [ ] Server queries are owner-scoped before filtering/ranking/pagination.
- [ ] Request body, item count, field length, and rate limits are enforced.
- [ ] The untrusted-content defenses above are reviewed against the shipped
      code: no HTML/markdown interpretation of captured content anywhere, no
      unbound SQL, sanitized fields, `noopener` links, sender-verified
      extension messaging.
- [ ] Default exclusions and metadata-only domains are documented and tested.
- [ ] Page/domain/all deletion is obvious and durable against offline replay.
- [ ] Retention is visible during setup and editable later.
- [ ] Cloud/public deployments have explicit abuse limits and terms before rollout.
- [ ] Encrypted-at-rest page text is explicitly re-evaluated before any public
      deployment enables the flag. The reference instance
      (newsread.sharon8811.com) is publicly reachable, and storing up to 6,000
      characters of every visited page is the feature's largest liability;
      plaintext-at-rest is acceptable for self-hosted v1 only.

## Observability

Collect operational metadata only:

- accepted/rejected sync item counts;
- queue age reported by the extension;
- connection `last_seen_at`;
- embedding backlog count and age;
- sync/search latency and error class;
- aggregate stored-page count per instance for capacity planning.

Do not log search queries, page titles, URLs, snippets, or extracted text.

## Explicitly out of scope for v1

- Chrome `SemanticEmbedder` as a required dependency;
- Firefox/Safari/Edge store releases;
- incognito;
- exact append-only visit timelines;
- full HTML archives, screenshots, PDFs, or downloaded files;
- server-side refetch of every visited URL;
- summaries, NER, generated images, related coverage, or Q&A over history;
- sharing, project pinning, or converting a history result into an article
  (an explicit “Import into NewsRead” action can follow later);
- mobile capture;
- global search across History and articles;
- automatic old-page body recovery;
- browser-managed OAuth or Chrome Web Store publishing automation.

## Follow-up opportunities

- “Similar to current tab” using a server query from the extension popup;
- one-click “Import this history page” into the existing Imported workflow;
- clustered topics and “what I researched this week” views;
- history-aware article recommendations, only after an explicit opt-in;
- local/on-device embedding mode when Chrome's API is stable;
- encrypted-at-rest page text with a user-held key (required review item before
  public rollout — see the privacy checklist; a follow-up only for self-hosted);
- mobile read-only History page;
- domain-level analytics such as time/visit trends without storing exact visit events;
- global search across feeds, Imported, projects, and History.

## Decisions requested before implementation

1. **Confirm server sync.** Recommended: yes. Local-only data cannot naturally power
   the integrated NewsRead `/history` page.
2. **Confirm text capture.** Recommended: metadata + up to 6,000 visible characters,
   with conservative metadata-only defaults for sensitive domains.
3. **Confirm retention.** Recommended: 90 days, user-selectable.
4. **Confirm visit granularity.** Recommended: per-page/per-browser aggregates, not
   an exact event log.
5. **Confirm repository layout.** Recommended: a new `extension/` directory in this
   repository, with the prototype retained only as a reference.
6. **Confirm existing-history import.** Recommended: optional during onboarding,
   capped, and clearly labeled metadata-only until pages are revisited.
7. **Confirm rollout scope.** Recommended: self-hosted first; public instances remain
   feature-flagged until rate limits and privacy copy are reviewed.
8. **Confirm the initial sensitive-domain policy.** Recommended: ship the
   conservative built-in metadata-only list (webmail, banking, health, password
   managers, cloud consoles, document editors), with per-domain opt-in to full
   text capture. User-managed exclusions alone are not enough given that the
   reference instance is publicly deployed.

Implementation should not begin until these eight choices are accepted or amended.
