# Browser History — what is collected and how to control it

NewsRead captures nothing until you pair the Chrome extension. Availability
follows the deployment mode, but self-hosted operators must explicitly enable
the feature with `NEWSREAD_BROWSER_HISTORY_ENABLED=true`.

## What the extension captures

For an ordinary HTML page you finish loading, the extension can collect:

- the title, hostname, cleaned URL, visit times, and URL-level visit count;
- up to 200,000 characters of structured, visible page text, split into
  bounded headings, paragraphs, lists, and quotes;
- a bounded lead image and favicon when Chrome permits canvas re-encoding.

Extraction happens in the browser. The backend never fetches or scrapes the
visited URL. Short, non-HTML, or not-yet-rendered pages still contribute
metadata without content.

The extension canonicalizes the document and computes its SHA-256 digest
locally. NewsRead recomputes the digest before accepting the upload and
deduplicates unchanged content only inside your account. The same digest from
another account is an independent encrypted object and database row.

Structured content is embedded automatically for private semantic search.
Summaries and page Q&A are different: neither runs until you explicitly click
the corresponding action. Generated summaries include citations back to
captured passages.

## What is never captured

- Incognito windows; the extension is disabled there.
- Browser UI, other extensions, file URLs, and non-HTML/PDF viewer pages.
- Localhost, private-network, reserved, and single-label intranet hosts.
- Your paired NewsRead server itself.
- Form values, passwords, page storage, cookies, or raw HTML.
- URL fragments, tracking parameters, and query parameters whose names suggest
  secrets; these are stripped before queueing.
- Anything while capture is paused or on an excluded domain.

Metadata-only domains contribute title, URL, and times but no document or
image. Exclusions are enforced in both the extension and the server.

## Chrome permissions, explained

| Permission | Why the extension needs it |
|---|---|
| Read data on websites you visit | Extract visible text and eligible images from loaded HTTP(S) pages. Content is sent only to the paired NewsRead origin. |
| Storage | Keep settings, the bounded offline queue, and content awaiting upload. Citation anchors use memory-only session storage and disappear on browser restart. |
| Alarms | Retry offline synchronization in the background. |
| Access to your NewsRead server | Requested for only the exact origin entered during pairing. |
| Browsing history (optional) | Used only for a user-started metadata import. Chrome has no old page bodies to import. |

Citation navigation first uses Chrome's native Text Fragment support. When the
paired extension assists, it validates the NewsRead sender and highlights a
temporary `Range` through the CSS Custom Highlight API; it does not rewrite the
page DOM.

## Storage and encryption

Document and image bytes are encrypted before they reach SeaweedFS or another
S3-compatible store. NewsRead uses a per-user AES-256 data key, wrapped by the
operator's versioned master key. Ciphertext authentication binds the object
type, user ID, and content hash, preventing an object from being replayed as
another user's content or as a different object type.

PostgreSQL stores metadata, short excerpts, keyword indexes, semantic vectors,
and generated summaries/citations. It does not store the captured document
body after the finalized migration. These derived values remain sensitive and
must be protected by database access controls and encrypted backups.

Server logs do not include page text, titles, URLs, search queries, tokens, or
summary/Q&A content.

## Retention and deletion

- Pages are kept for 90 days by default; Settings offers 30, 90, 365 days, or
  forever.
- Retention applies to each page–document version link. If the current version
  expires, the newest surviving version becomes current; otherwise the page
  becomes metadata-only.
- Delete one page, exclude-and-delete a domain, or clear all history at any
  time. Deletion tombstones stop an offline browser from restoring stale rows.
- Unlinked document and image rows are removed after a grace period. A durable
  deletion outbox then removes their encrypted objects, including objects
  reached through account deletion.
- Revoking a browser stops future uploads but does not delete existing history.
- Deleting your account cascades every history row and queues every private
  object for deletion.

Storage use and indexing/deletion backlogs are visible under Settings →
Browser history.

## Operator notes

Content capture requires both
`NEWSREAD_BROWSER_HISTORY_CONTENT_ENABLED=true` and a private object store plus
a valid encryption master key. Run the dual-read search audit before enabling
the final capability flag and applying the destructive legacy-body migration.

Use TLS for any non-local object-store connection, least-privilege bucket
credentials, coordinated PostgreSQL/object-store backups, and external backup
of the encryption master key. Losing any one of the database, bucket, or
master key can make a restore incomplete or unreadable. See
[Browser History operations](browser-history-operations.md).
