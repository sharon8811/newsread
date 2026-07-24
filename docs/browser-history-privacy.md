# Browser History — what is collected and how to control it

NewsRead's Browser History feature is **off by default on every deployment**.
Nothing on this page applies until the server operator sets
`NEWSREAD_BROWSER_HISTORY_ENABLED=true` *and* you pair the Chrome extension
yourself. For a self-hosted instance you administer, that one environment
variable is the only switch; on shared or public instances the operator should
review the abuse limits and this document before enabling it.

## What the extension captures

When you pair the NewsRead History extension and capture is on, each ordinary
page you finish loading in Chrome contributes:

- the page **title** and a cleaned, openable **URL** (see exclusions below);
- the page's **hostname** and visit times/counts;
- up to **6,000 characters of visible text** (what you could read on the page —
  never form fields, passwords, page storage, or raw HTML).

Everything is stored **only on the NewsRead server you paired with**, scoped to
your account. Other users of the same server can never see your pages, domains,
counts, or connection names. Captured text is used for search on your History
page and, if the server has an embedding model configured, for private semantic
search vectors. It is never fed to summaries, image generation, recommendations,
sharing, or any LLM feature.

## What is never captured

- Incognito windows — the extension is disabled there by the manifest.
- Non-web pages: browser UI, other extensions, file downloads, PDFs viewers.
- Localhost, private-network, and reserved hostnames (`.internal`, `.local`,
  `.test`, single-label intranet names, private IPs).
- Your NewsRead server's own pages.
- URL fragments, tracking parameters (`utm_*`, `fbclid`, `gclid`, …) and query
  parameters whose names suggest secrets (`token`, `session`, `code`, `key`,
  `password`, …) — stripped in the browser *before* anything is queued.
- Anything while capture is **paused**, and any domain you exclude.

Domains you mark **metadata-only** contribute title/URL/times but no page text.
Exclusions and metadata-only rules are enforced twice: in the extension before
queueing, and again on the server on every sync.

## Chrome permissions, explained

| Permission | Why the extension needs it |
|---|---|
| Read data on websites you visit | Reading the visible text of a loaded page is the product; the warning text is Chrome's standard phrasing for any content script. Nothing is sent anywhere except your paired NewsRead server. |
| Storage | Local settings and the offline sync queue. |
| Alarms | Waking up to retry syncing in the background. |
| Access to your NewsRead server (asked at pairing) | Granted only for the exact origin you type in — the extension can talk to no other site. |
| Browsing history (optional, asked on demand) | Only if you start the one-time "import existing history" step, which imports titles/URLs/times (no page text — Chrome does not keep old page bodies). Decline it and everything else still works. |

## Retention and deletion

- Captured pages are kept for **90 days by default**; you can choose 30/90/365
  days or forever in Settings → Browser history. Cleanup runs daily on the
  server.
- **Delete one page**, **exclude-and-delete a domain**, or **clear all
  history** from NewsRead at any time. Deletions write server-side tombstones,
  so a browser that was offline when you deleted cannot re-upload stale copies
  of the deleted pages later.
- One caveat of that protection: pages you genuinely revisit *while the
  extension is offline shortly after a deletion* may not be recorded until the
  extension reconnects and learns about the deletion.
- **Revoking a browser** in Settings takes effect on its next sync. Revoking
  stops future uploads; it does not delete already-synced pages unless you also
  delete them.
- Deleting your NewsRead account deletes every history row and connection.

## Pairing tokens

Pairing uses a one-time token created in Settings → Browser history. The server
stores only a hash; the token is shown once and cannot be recovered — revoke
and re-pair instead. The token authenticates *only* the history sync endpoints:
it cannot read your feeds, articles, projects, or anything else on your
account, and it never doubles as a login.

## Operator notes (self-hosted / public)

- Enable with `NEWSREAD_BROWSER_HISTORY_ENABLED=true`. The flag is deliberately
  opt-in in **every** deployment mode because captured page text is sensitive.
- Sync is rate-limited per paired browser (60 requests/minute, 100 records and
  1 MiB per request) and tokens are revocable instantly.
- Page text is stored in plaintext in PostgreSQL. For a public, multi-user
  deployment, review access to the database and backups before enabling, and
  treat encrypted-at-rest page text as a prerequisite (see the feature plan's
  privacy checklist).
- Server logs never include page text, titles, URLs, search queries, or tokens.
