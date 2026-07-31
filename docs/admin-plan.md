# Instance administration & analytics plan (#113)

Working plan for the admin area: roles/bootstrap (#114), metrics
instrumentation (#115), admin APIs + audit log (#116), the `/admin` web UI
(#117), and user tiers with monthly article allowances (#119). One PR per
sub-issue, in that order (#114 ∥ #115, then #116, #119, #117 last).

Principles (from #113): works for self-hosted and hosted; authorization is
per-user roles, never `NEWSREAD_DEPLOYMENT`; no billing/checkout in this
phase; admin surfaces never expose private content, credentials, or tokens.

## #114 — Roles and secure bootstrap (this PR)

- `users.role` (`owner` | `admin` | `user`, default `user`) and
  `users.status` (`active` | `suspended`, default `active`); migration 0016.
- `app/roles.py` owns the role vocabulary and the final-owner safeguards
  (`change_role` / `change_status` raise `FinalOwnerError` when the last
  *active* owner would be demoted or suspended). #116's endpoints reuse these
  helpers, which also prevents self-lockout (acting on yourself as the only
  owner hits the same guard).
- `get_current_user` loads the user row per request already; it now rejects
  `suspended` with 403 immediately, no token revocation machinery needed.
  Login rejects suspended accounts too.
- Dependencies `AdminUser` (owner+admin) and `OwnerUser` in `deps.py`;
  the API is the security boundary, frontend checks are UX only.
- Bootstrap: `NEWSREAD_FIRST_ACCOUNT_OWNER` (derived default: on for
  self_hosted, off for staging/prod). First-account decisions serialize on a
  pg advisory xact lock so concurrent registrations on an empty instance
  can't both become owner. Hosted/existing installs promote via
  `scripts/set_role.py` (shell access ⇒ already trusted; no env-email
  auto-promotion because email ownership is unverified).
- `UserOut.role` so clients can gate navigation (#117).

## #115 — Metrics instrumentation

Reuse transactional tables where possible (`users.created_at`,
`subscriptions.created_at`, `articles.fetched_at` / `summary_generated_at` /
`summary_skipped_reason`, `user_article_states.read_at`, `reading_activity`,
`llm_usage`). Additions:

1. **Active users** — new `user_activity_days` table (`user_id`, `day` UTC,
   PK on both). Upserted (`ON CONFLICT DO NOTHING`) from an auth-path hook,
   throttled by a small in-process TTL cache so it writes at most once per
   user per hour, not on every request. Gives historical DAU/WAU/MAU by
   `COUNT(DISTINCT user_id)` over day windows; `users.last_seen_at` alone
   couldn't reconstruct trends.
2. **System-key LLM metering** — drop the `user_owned` gate in
   `llm.record_usage`; add `llm_usage.billing_source` (`user` | `system`)
   and make `user_id` nullable (worker batch summaries have no acting user).
   Add the missing call sites: worker batch summarization, translation,
   topics fallback, system-key image generation. Add index on
   (`billing_source`, `created_at`) or plain `created_at` for instance-wide
   range scans.
3. **Processing outcomes** — new `article_processing_events` table
   (`article_id`, `stage` enrich|summarize|embed, `outcome` ok|failed|skipped,
   `detail` short code only — never error text with user content,
   `created_at`). Written where the worker currently only logs
   (`_for_each_article`, `_summarize_quietly`, extractor failures). Success
   rows for summarize/enrich are optional — successes are already derivable
   from article timestamps; failures/retries are not.
4. **Semantics** — documented per metric: articles processed are global
   (articles are shared); articles read/consumed are per-user. Straight
   PostgreSQL aggregation first; daily rollups only if it gets slow.
5. **Indexes** — `users.created_at`, `subscriptions.created_at`,
   `articles.fetched_at`, `user_article_states.read_at` (partial, WHERE
   read_at IS NOT NULL), `llm_usage.created_at`.

## #116 — Admin APIs, privacy, audit log

- Router `app/routers/admin.py`, all routes under `/api/admin`, `AdminUser`
  dependency; role/status mutations `OwnerUser` where required.
- `GET /overview`, `GET /trends?range=`, `GET /users` (paginated, search,
  filter by role/status/tier, sort), `GET /users/{id}` (aggregates only),
  `PATCH /users/{id}/role` (owner only), `PATCH /users/{id}/status`.
- Response schemas are explicit allowlists: account metadata + aggregate
  usage. Never password hashes, keys, tokens, LLM error text, article or
  history content.
- Mutations go through `roles.change_role`/`change_status` (final-owner and
  self-lockout guards) and write `admin_audit_log` (`actor_id`,
  `target_user_id`, `action`, `before`/`after` minimal JSONB, `created_at`).
- Bounded date ranges; pagination follows the existing keyset pattern
  (`routers/usage.py` events).

## #117 — Admin web UI

- `app/(app)/admin/page.tsx` (overview + range switcher) and
  `app/(app)/admin/users/page.tsx`. Reuses `StatTile`/`Delta`/range patterns
  from `/activity` and `/usage`, `buildChartData`/recharts for trends,
  `ConfirmButton` for role/suspend actions, keyset pagination UI from
  history.
- Sidebar item gated on `user.role` (`useAuth()`); direct unauthorized visit
  renders not-found (history-page precedent). API stays authoritative.
- Web only; no mobile surface initially.

## #119 — Tiers and monthly article allowances

- `tiers` table seeded with Free ($0, 100/mo), Paid ($5, 1000/mo),
  Unlimited ($20, ∞); names/prices/limits editable data, not code. Prices are
  informational only. `users.tier_id` nullable → effective default: hosted
  new users Free; self-hosted owner Unlimited (documented); owners/admins
  never quota-blocked.
- **Qualifying event (needs sign-off before implementation):** an article is
  charged to a user the first time NewsRead completes AI processing of that
  article *for them* — i.e. when a summarized article is first delivered to
  that user (list/detail/stories), or when they trigger on-demand
  summarization/import. Opens/re-reads/translations/cache hits never
  re-charge. Mechanics: `user_article_charges` (`user_id`, `article_id`,
  `period` YYYY-MM UTC, tier snapshot; UNIQUE (`user_id`,`article_id`)) —
  the unique key makes duplicate jobs/retries idempotent, at most one charge
  per article per user ever.
- Concurrency: allowance reservation via row lock on the user's monthly
  counter row (`user_quota_periods`: `user_id`, `period`, `used`,
  `allowance_snapshot`, `tier_snapshot`) — the `SELECT … FOR UPDATE` pattern
  from `history.py`'s rate limiter, not the racy image-gen read-then-claim.
  Calendar-month reset in UTC = new period row; no reset job.
- Snapshotting the allowance on the period row keeps historic usage
  queryable after tier config changes; tier changes mid-month update the
  snapshot going forward (documented: moving below current usage simply
  stops further charges — nothing retroactive is clawed back).
- Over-allowance behavior: reading existing/processed content keeps working;
  AI processing of *new* articles for that user stops (no new summaries
  delivered/generated for them) with a clear API state + UI notice. Users
  see tier, usage, and reset date read-only; no purchase/upgrade UI.
- Admin: tier column + filter in `/admin/users`, tier change (audited),
  effective allowance + reset date on the user detail.

## Open decisions

1. **#119 qualifying event** — recommendation above (first AI-processed
   delivery per user per article); alternatives considered: charge on read
   (undercounts the processing NewsRead pays for), charge on ingest into a
   subscribed feed (charges for articles the user never sees; punishing for
   high-volume feeds).
2. **Quota timezone** — UTC everywhere (matches image-gen budget and
   `llm_usage` bucketing) vs. user-local. Recommendation: UTC.
3. **Suspended UX** — currently a bare 403; #117 could map it to a
   dedicated "account suspended" screen.
