import {
  MAX_QUEUE_AGE_MS,
  MAX_QUEUE_ENTRIES,
  MAX_SYNC_BYTES,
  MAX_SYNC_RECORDS,
} from "./config.js";
import type {
  CaptureCandidate,
  ExtensionSettings,
  QueuedCapture,
} from "./types.js";
import { hostnameMatches, normalizeCaptureUrl } from "./url.js";

const DB_NAME = "newsread-history";
const DB_VERSION = 2;
const STORE = "outbox";
const VISITS_STORE = "visits";
let databasePromise: Promise<IDBDatabase> | null = null;

interface VisitAggregate {
  urlHash: string;
  firstVisitedAt: string;
  lastVisitedAt: string;
  visitCount: number;
}

function openDatabase(): Promise<IDBDatabase> {
  if (databasePromise) return databasePromise;
  databasePromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(STORE)) {
        const store = database.createObjectStore(STORE, { keyPath: "urlHash" });
        store.createIndex("queuedAt", "queuedAt");
        store.createIndex("recordId", "record_id", { unique: true });
      }
      if (!database.objectStoreNames.contains(VISITS_STORE)) {
        database.createObjectStore(VISITS_STORE, { keyPath: "urlHash" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return databasePromise;
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function withStore<T>(
  mode: IDBTransactionMode,
  operation: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const database = await openDatabase();
  const transaction = database.transaction(STORE, mode);
  return requestResult(operation(transaction.objectStore(STORE)));
}

async function withNamedStore<T>(
  storeName: string,
  mode: IDBTransactionMode,
  operation: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const database = await openDatabase();
  const transaction = database.transaction(storeName, mode);
  return requestResult(operation(transaction.objectStore(storeName)));
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function effectiveCaptureMode(
  hostname: string,
  settings: ExtensionSettings,
): "full" | "metadata_only" | "exclude" {
  if (
    settings.excludedDomains.some((domain) =>
      hostnameMatches(hostname, domain, true),
    )
  ) {
    return "exclude";
  }
  let mode: "full" | "metadata_only" = settings.captureMode;
  for (const rule of settings.domainRules) {
    if (!hostnameMatches(hostname, rule.hostname, rule.match_subdomains)) continue;
    if (rule.mode === "exclude") return "exclude";
    mode = "metadata_only";
  }
  return mode;
}

export async function enqueueCapture(
  candidate: CaptureCandidate,
  settings: ExtensionSettings,
): Promise<boolean> {
  if (settings.paused || !settings.token || !settings.serverUrl) return false;
  const normalized = normalizeCaptureUrl(candidate.url);
  if (!normalized) return false;
  if (normalized.origin === new URL(settings.serverUrl).origin) return false;
  const mode = effectiveCaptureMode(normalized.hostname, settings);
  if (mode === "exclude") return false;

  const text = mode === "metadata_only" ? "" : candidate.text.slice(0, 6000);
  const excerpt =
    mode === "metadata_only" ? "" : candidate.textExcerpt.slice(0, 500);
  const embeddingText = `${candidate.title}\n\n${normalized.hostname}\n\n${text}`.slice(
    0,
    6000,
  );
  const [urlHash, contentHash] = await Promise.all([
    sha256(normalized.href),
    sha256(embeddingText),
  ]);
  const existing = await withStore<QueuedCapture | undefined>(
    "readonly",
    (store) => store.get(urlHash),
  );
  const visits = await withNamedStore<VisitAggregate | undefined>(
    VISITS_STORE,
    "readonly",
    (store) => store.get(urlHash),
  );
  const now = candidate.capturedAt;
  const aggregate: VisitAggregate = {
    urlHash,
    firstVisitedAt: visits?.firstVisitedAt ?? now,
    lastVisitedAt: now,
    visitCount: Math.min(1_000_000, (visits?.visitCount ?? 0) + 1),
  };
  await withNamedStore(VISITS_STORE, "readwrite", (store) =>
    store.put(aggregate),
  );
  const capture: QueuedCapture = {
    urlHash,
    record_id: existing?.record_id ?? crypto.randomUUID(),
    url: normalized.href,
    title: candidate.title.slice(0, 512),
    text:
      existing && existing.contentHash === contentHash ? existing.text : text,
    text_excerpt:
      existing && existing.contentHash === contentHash
        ? existing.text_excerpt
        : excerpt || text.slice(0, 400),
    first_visited_at: aggregate.firstVisitedAt,
    last_visited_at: aggregate.lastVisitedAt,
    captured_at: text ? now : null,
    visit_count: aggregate.visitCount,
    known_revision: settings.knownRevision,
    contentHash,
    queuedAt: Date.now(),
  };
  await withStore("readwrite", (store) => store.put(capture));
  await enforceQueueLimit();
  return true;
}

export async function enqueueHistoryMetadata(
  url: string,
  title: string,
  lastVisitTime: number,
  visitCount: number,
  settings: ExtensionSettings,
): Promise<boolean> {
  const capturedAt = new Date(lastVisitTime).toISOString();
  const queued = await enqueueCapture(
    {
      url,
      title,
      text: "",
      textExcerpt: "",
      capturedAt,
    },
    { ...settings, captureMode: "metadata_only" },
  );
  if (!queued) return false;
  const normalized = normalizeCaptureUrl(url);
  if (!normalized) return false;
  const urlHash = await sha256(normalized.href);
  const existing = await withStore<QueuedCapture | undefined>(
    "readonly",
    (store) => store.get(urlHash),
  );
  if (existing) {
    const count = Math.min(
      1_000_000,
      Math.max(existing.visit_count, visitCount),
    );
    existing.visit_count = count;
    await withStore("readwrite", (store) => store.put(existing));
    const visits = await withNamedStore<VisitAggregate | undefined>(
      VISITS_STORE,
      "readonly",
      (store) => store.get(urlHash),
    );
    if (visits && visits.visitCount < count) {
      visits.visitCount = count;
      await withNamedStore(VISITS_STORE, "readwrite", (store) =>
        store.put(visits),
      );
    }
  }
  return true;
}

async function enforceQueueLimit(): Promise<void> {
  const captures = await listQueued();
  if (captures.length <= MAX_QUEUE_ENTRIES) return;
  captures.sort((a, b) => a.queuedAt - b.queuedAt);
  await deleteQueued(
    captures
      .slice(0, captures.length - MAX_QUEUE_ENTRIES)
      .map((capture) => capture.urlHash),
  );
}

export async function listQueued(): Promise<QueuedCapture[]> {
  return withStore("readonly", (store) => store.getAll());
}

export async function countQueued(): Promise<number> {
  return withStore("readonly", (store) => store.count());
}

export async function deleteQueued(urlHashes: string[]): Promise<void> {
  if (!urlHashes.length) return;
  const database = await openDatabase();
  const transaction = database.transaction(STORE, "readwrite");
  const store = transaction.objectStore(STORE);
  for (const urlHash of urlHashes) store.delete(urlHash);
  await new Promise<void>((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
}

export async function clearQueued(): Promise<void> {
  await withStore("readwrite", (store) => store.clear());
}

export async function clearConnectionData(): Promise<void> {
  await clearQueued();
  await withNamedStore(VISITS_STORE, "readwrite", (store) => store.clear());
}

export async function readSyncBatch(now = Date.now()): Promise<QueuedCapture[]> {
  const captures = await listQueued();
  const expired = captures.filter(
    (capture) => now - capture.queuedAt > MAX_QUEUE_AGE_MS,
  );
  await deleteQueued(expired.map((capture) => capture.urlHash));
  const active = captures
    .filter((capture) => now - capture.queuedAt <= MAX_QUEUE_AGE_MS)
    .sort((a, b) => a.queuedAt - b.queuedAt);
  const batch: QueuedCapture[] = [];
  for (const capture of active) {
    if (batch.length >= MAX_SYNC_RECORDS) break;
    const candidate = [...batch, capture].map(toSyncRecord);
    if (
      new TextEncoder().encode(JSON.stringify({ records: candidate })).length >
      MAX_SYNC_BYTES
    ) {
      break;
    }
    batch.push(capture);
  }
  return batch;
}

export function toSyncRecord(capture: QueuedCapture) {
  return {
    record_id: capture.record_id,
    url: capture.url,
    title: capture.title,
    text: capture.text,
    text_excerpt: capture.text_excerpt,
    first_visited_at: capture.first_visited_at,
    last_visited_at: capture.last_visited_at,
    captured_at: capture.captured_at,
    visit_count: capture.visit_count,
    known_revision: capture.known_revision,
  };
}

export function resetOutboxForTests(): void {
  databasePromise = null;
}
