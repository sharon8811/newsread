# Instance metric definitions (#115)

Every metric the admin dashboard (#116/#117) reports, with its exact
definition and source. All date bucketing is **UTC** unless noted. Straight
PostgreSQL aggregation over transactional tables — no external analytics
service, no rollup tables (introduce daily rollups only if these queries get
slow on real volumes).

## Users

| Metric | Definition | Source |
|---|---|---|
| Total users | Count of user rows | `users` |
| New users | Users created in the range | `users.created_at` (indexed) |
| Daily/weekly/monthly active | Users with ≥1 authenticated API request on ≥1 day in the window | `user_activity_days` (one row per user per UTC day, throttled upsert from the auth dependency; presence, not engagement) |

## Subscriptions

| Metric | Definition | Source |
|---|---|---|
| Subscriptions over time | Subscription rows created in the range (current subscriptions only — unsubscribing deletes the row, so this is net-surviving signups, not gross) | `subscriptions.created_at` (indexed) |

## Articles — processed (global)

Articles are shared rows: one article processed once serves every
subscriber. Processing metrics are therefore **instance-global**, never
per-user.

| Metric | Definition | Source |
|---|---|---|
| Ingested | Article rows first fetched in the range | `articles.fetched_at` (indexed) |
| Summarized | Articles whose current summary was generated in the range (regeneration re-dates it) | `articles.summary_generated_at` |
| Skipped | Summary skips stamped in the range: `too_short`, `needs_full_page`, `unusable_page` | `article_processing_events` (stage `summarize`, outcome `skipped`, detail = reason) |
| Processing failures | Per-article pipeline failures by stage: `poll`, `enrich`, `summarize`, `ner`, `import` | `article_processing_events` (outcome `failed`, detail = exception class name only — never error text) |

Notes: events exist because failures/skips have no dated column of their own;
successes deliberately have no event rows (`fetched_at` /
`summary_generated_at` already date them). Retries appear as repeated events
for the same article. Enrichment fetch failures inside
`extractor.enrich_article` are still invisible (it stamps unconditionally);
only exceptions that escape to the worker are counted — known limitation.

## Articles — consumed (per-user)

| Metric | Definition | Source |
|---|---|---|
| Articles read | `user_article_states` rows with `read_at` in the range (any `read_source`, including mark-all) | `user_article_states.read_at` (partial index) |
| Articles saved | State rows with `is_saved` | `user_article_states` (no dated column — point-in-time count only) |
| Reading time | Sum of `reading_activity.seconds` in the range. **Client-local days**, not UTC — the one non-UTC source, kept because streaks/graphs are user-facing | `reading_activity` |

## LLM usage

One `llm_usage` row per LLM call, whatever key it ran on.

| Metric | Definition | Source |
|---|---|---|
| Calls / tokens / errors / latency | Row count, `prompt_tokens + completion_tokens`, `status='error'` count, `duration_ms` distribution, in the range | `llm_usage.created_at` (indexed) |
| By feature / model | Group by `feature`, `model` | `llm_usage` |
| Billing source | `billing_source='user'` = the user's own key (BYO); `'system'` = the operator's server-wide key | `llm_usage.billing_source` |
| Per-user spend | Sum of tokens by `user_id`, both billing sources. System-key calls keep the acting user's id (on-demand summaries, QA, chat, share messages, translations, imports, topics, history summaries); batch/cron work nobody triggered (worker batch summaries, NER) carries `user_id` NULL = instance overhead. A user is never charged for cached or shared results — a cache/copy hit makes no LLM call and writes no row | `llm_usage.user_id` (survives account deletion via SET NULL) |

Not metered (known gaps): embedding calls (articles, catalog, history,
suppression vectors) and the entity-enricher pipeline — cheap relative to
generation; candidates for a follow-up `feature='embedding'`.

## Privacy

Analytics records never contain article text, browser-history content, API
keys, tokens, or raw error messages. `article_processing_events.detail` is a
fixed reason code or an exception class name; `llm_usage.error` (truncated
provider error) already exists for the *user's own* debugging and must not be
surfaced in admin responses (#116 boundary).
