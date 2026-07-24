# Browser History operations

This runbook covers the encrypted content pipeline. The metadata-only history
feature can run without object storage; content capture cannot.

## Rollout and cutover

1. Configure a private S3-compatible bucket, TLS for any non-local endpoint,
   least-privilege credentials, and
   `NEWSREAD_HISTORY_ENCRYPTION_MASTER_KEY`.
2. Set `NEWSREAD_BROWSER_HISTORY_CONTENT_ENABLED=true`. Capability revision 2
   uploads encrypted structured documents and keeps a temporary conversion
   path for old extensions that still send inline text.
3. During the dual-read window, choose representative real search queries and
   run from `backend/`:

   ```bash
   PYTHONPATH=. .venv/bin/python scripts/audit_history_search_cutover.py \
     --query "first representative query" \
     --query "second representative query"
   ```

   The command is read-only and reports aggregate result coverage without
   printing user IDs, URLs, titles, or page text. Investigate low overlap
   before proceeding. It must run before migration 0011 removes the legacy
   page index.
4. Back up all three required restore components described below.
5. Deploy migration 0011 and set
   `NEWSREAD_BROWSER_HISTORY_FINALIZE_ENABLED=true`. The server advertises
   capability revision 3 and old inline bodies become metadata-only. Leave the
   flag false if application instances have not all been upgraded.

Migration 0011 removes legacy page bodies, hashes, keyword indexes, and page
vectors. Pages never revisited by a v2 extension remain searchable by title
and hostname only.

## Storage and network controls

- SeaweedFS is an internal S3-compatible implementation detail. Do not expose
  its bucket or management endpoints as a content CDN.
- The default Compose endpoint is bound to loopback. Use TLS and certificate
  verification whenever traffic crosses a host boundary.
- Give backend and worker credentials access only to the History bucket.
- Bucket bytes are encrypted application ciphertext, but bucket access remains
  sensitive metadata and must be audited.
- Keep the master wrapping key outside PostgreSQL and SeaweedFS, preferably in
  the deployment secret manager.

## Backup and restore

A usable backup is one coordinated restore point containing:

1. PostgreSQL, including wrapped per-user data keys, document metadata,
   summaries, vectors, and the deletion outbox;
2. the complete History object bucket/SeaweedFS volume;
3. the current master wrapping key and every previous wrapping-key version
   still referenced by `browser_history_user_keys`.

Losing the database loses the map and wrapped data keys. Losing the bucket
loses captured bodies and images. Losing a referenced master key makes those
objects cryptographically unreadable.

For the strongest consistency, pause backend and worker writes, take the
database and bucket snapshots, record their common timestamp, then resume.
Provider-native volume snapshots or S3 versioned backups are preferred over
copying live files.

Restore into a private environment first:

1. restore PostgreSQL and the bucket from the same restore point;
2. restore the exact wrapping-key versions before starting the application;
3. start one backend and one worker with content capture still disabled;
4. verify document reads for sampled owners and inspect the deletion and
   embedding backlog metrics;
5. enable content capture only after verification.

The orphan sweeper deletes only managed History keys older than the configured
grace period. Keep the default grace period during restore so objects from a
slightly newer bucket snapshot are not removed immediately. A database newer
than the bucket can contain rows whose objects are missing; restore from a
consistent point rather than manufacturing empty replacements.

## Deletion and garbage collection

Deleting a page, domain, all history, or an account first removes relational
links. Unreferenced document/image rows are removed after
`NEWSREAD_HISTORY_OBJECT_GC_GRACE_HOURS`; database triggers enqueue their keys
in `browser_history_object_deletions`. The worker deletes bucket objects
idempotently and retries failures with bounded exponential backoff.

Separately, the orphan sweep removes old managed bucket keys that never reached
database finalization. It ignores young objects, referenced objects, and keys
outside the strict `users/{id}/history/{documents|images}/sha256/...`
namespace. A durable database cursor advances bounded scans through large
buckets without starving keys beyond the first page.

Do not manually delete only PostgreSQL rows or only bucket objects. If emergency
manual work is unavoidable, preserve the deletion outbox and the grace window.

## Quotas, metrics, and alerts

Per-user limits:

- stored document plus image bytes:
  `NEWSREAD_HISTORY_USER_STORAGE_MAX_BYTES`;
- newly embedded documents per UTC day:
  `NEWSREAD_HISTORY_EMBEDDING_DAILY_LIMIT`.

Users see their storage and indexing/deletion state in Settings → Browser
history. The worker emits aggregate structured log lines every 15 minutes and
warnings when:

- the oldest embedding backlog item exceeds
  `NEWSREAD_HISTORY_EMBEDDING_BACKLOG_ALERT_HOURS`;
- the oldest object-deletion item exceeds
  `NEWSREAD_HISTORY_DELETION_BACKLOG_ALERT_HOURS`;
- any owner exceeds `NEWSREAD_HISTORY_STORAGE_ALERT_RATIO` of quota.

Route warning logs into the deployment's alerting system. Alert on repeated
worker crashes and object-store authentication/TLS errors as well; a green API
alone does not prove that embeddings or deletion are progressing.

## Wrapping-key rotation

Objects use per-user data keys, so rotating the master key does not rewrite the
bucket. Add the old version and key to
`NEWSREAD_HISTORY_ENCRYPTION_PREVIOUS_MASTER_KEYS`, configure a new
`NEWSREAD_HISTORY_ENCRYPTION_WRAPPING_KEY_VERSION` and current master key, then
rewrap every `browser_history_user_keys` row under the new version:

```bash
cd backend
PYTHONPATH=. .venv/bin/python scripts/rewrap_history_keys.py
```

The command locks and commits bounded batches and does not rewrite encrypted
objects. Do not remove an old key until no row references its version and
sampled object reads succeed.

Never reuse a wrapping-key version for different key bytes. Back up the new
secret before rotation and retain the previous secret until the post-rotation
backup has been restored successfully in a private verification environment.
