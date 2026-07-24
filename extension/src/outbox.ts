import {
  MAX_QUEUE_AGE_MS,
  MAX_QUEUE_ENTRIES,
  MAX_SYNC_BYTES,
  MAX_SYNC_RECORDS,
} from "./config.js";
import {
  canonicalizeCaptureDocument,
  hashCaptureDocument,
  sha256Bytes,
} from "./capture-document.js";
import { matchingSystemRule } from "./system-policy.js";
import type {
  CaptureCandidate,
  CapturedImage,
  DomainRule,
  ExtensionSettings,
  QueuedCapture,
  QueuedContent,
  QueuedImage,
  SystemRule,
} from "./types.js";
import { hostnameMatches, normalizeCaptureUrl } from "./url.js";

const DB_NAME = "newsread-history";
const DB_VERSION = 3;
const OUTBOX_STORE = "outbox";
const VISITS_STORE = "visits";
const CONTENT_STORE = "content";
const IMAGE_STORE = "images";
let databasePromise: Promise<IDBDatabase> | null = null;

interface VisitAggregate {
  urlHash: string;
  hostname: string;
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
      const outbox = database.objectStoreNames.contains(OUTBOX_STORE)
        ? request.transaction!.objectStore(OUTBOX_STORE)
        : database.createObjectStore(OUTBOX_STORE, { keyPath: "urlHash" });
      if (!outbox.indexNames.contains("queuedAt")) {
        outbox.createIndex("queuedAt", "queuedAt");
      }
      if (!outbox.indexNames.contains("recordId")) {
        outbox.createIndex("recordId", "record_id", { unique: true });
      }
      if (!outbox.indexNames.contains("contentHash")) {
        outbox.createIndex("contentHash", "content_hash");
      }
      if (!database.objectStoreNames.contains(VISITS_STORE)) {
        database.createObjectStore(VISITS_STORE, { keyPath: "urlHash" });
      }
      if (!database.objectStoreNames.contains(CONTENT_STORE)) {
        database.createObjectStore(CONTENT_STORE, { keyPath: "contentHash" });
      }
      if (!database.objectStoreNames.contains(IMAGE_STORE)) {
        database.createObjectStore(IMAGE_STORE, { keyPath: "imageHash" });
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
  return sha256Bytes(new TextEncoder().encode(value));
}

function effectiveCaptureMode(
  url: URL,
  settings: ExtensionSettings,
): "full" | "metadata_only" | "exclude" {
  if (matchingSystemRule(url, settings.systemRules)) return "exclude";
  if (
    settings.excludedDomains.some((domain) =>
      hostnameMatches(url.hostname, domain, true),
    )
  ) {
    return "exclude";
  }
  let mode: "full" | "metadata_only" = settings.captureMode;
  for (const rule of settings.domainRules) {
    if (!hostnameMatches(url.hostname, rule.hostname, rule.match_subdomains)) {
      continue;
    }
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
  const mode = effectiveCaptureMode(normalized, settings);
  if (mode === "exclude") return false;

  const canonical =
    mode === "full" && candidate.document
      ? canonicalizeCaptureDocument(candidate.document)
      : null;
  const legacyText = canonical?.text.slice(0, 6000) ?? "";
  const legacyExcerpt =
    mode === "metadata_only"
      ? ""
      : candidate.textExcerpt.slice(0, 500) || legacyText.slice(0, 400);
  const usesContentV2 =
    settings.contentCapabilityRevision >= 2 && canonical !== null;
  let contentHash: string | null = null;
  if (usesContentV2 && canonical) {
    contentHash = await hashCaptureDocument(canonical.canonicalJson);
    await putContentIfAbsent(contentHash, canonical.canonicalJson);
  }
  const [leadImageHash, faviconImageHash] = usesContentV2
    ? await Promise.all([
        queueImage(candidate.leadImage),
        queueImage(candidate.favicon),
      ])
    : [null, null];

  const embeddingText = `${candidate.title}\n\n${normalized.hostname}\n\n${legacyText}`.slice(
    0,
    6000,
  );
  const [urlHash, legacyContentHash] = await Promise.all([
    sha256(normalized.href),
    sha256(embeddingText),
  ]);
  const existing = await withNamedStore<QueuedCapture | undefined>(
    OUTBOX_STORE,
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
    hostname: normalized.hostname,
    firstVisitedAt: visits?.firstVisitedAt ?? now,
    lastVisitedAt: now,
    visitCount: Math.min(1_000_000, (visits?.visitCount ?? 0) + 1),
  };
  await withNamedStore(VISITS_STORE, "readwrite", (store) =>
    store.put(aggregate),
  );
  const existingVersion =
    existing?.content_hash ??
    (existing as (QueuedCapture & { contentHash?: string }) | undefined)
      ?.contentHash;
  const incomingVersion = contentHash ?? legacyContentHash;
  const capture: QueuedCapture = {
    urlHash,
    record_id: existing?.record_id ?? crypto.randomUUID(),
    url: normalized.href,
    title: candidate.title.slice(0, 512),
    legacy_text:
      existing && existingVersion === incomingVersion
        ? legacyTextFor(existing)
        : legacyText,
    legacy_text_excerpt:
      existing && existingVersion === incomingVersion
        ? legacyExcerptFor(existing)
        : legacyExcerpt,
    content_hash: contentHash,
    lead_image_hash: leadImageHash,
    favicon_image_hash: faviconImageHash,
    first_visited_at: aggregate.firstVisitedAt,
    last_visited_at: aggregate.lastVisitedAt,
    captured_at: canonical ? now : null,
    visit_count: aggregate.visitCount,
    known_revision: settings.knownRevision,
    queuedAt: Date.now(),
  };
  await withNamedStore(OUTBOX_STORE, "readwrite", (store) =>
    store.put(capture),
  );
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
      document: null,
      leadImage: null,
      favicon: null,
      textExcerpt: "",
      capturedAt,
    },
    { ...settings, captureMode: "metadata_only" },
  );
  if (!queued) return false;
  const normalized = normalizeCaptureUrl(url);
  if (!normalized) return false;
  const urlHash = await sha256(normalized.href);
  const existing = await withNamedStore<QueuedCapture | undefined>(
    OUTBOX_STORE,
    "readonly",
    (store) => store.get(urlHash),
  );
  if (existing) {
    const count = Math.min(
      1_000_000,
      Math.max(existing.visit_count, visitCount),
    );
    existing.visit_count = count;
    await withNamedStore(OUTBOX_STORE, "readwrite", (store) =>
      store.put(existing),
    );
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
  await deleteUnreferencedObjects();
}

export async function listQueued(): Promise<QueuedCapture[]> {
  return withNamedStore(OUTBOX_STORE, "readonly", (store) => store.getAll());
}

export async function countQueued(): Promise<number> {
  return withNamedStore(OUTBOX_STORE, "readonly", (store) => store.count());
}

export async function deleteQueued(urlHashes: string[]): Promise<void> {
  if (!urlHashes.length) return;
  const database = await openDatabase();
  const transaction = database.transaction(OUTBOX_STORE, "readwrite");
  const store = transaction.objectStore(OUTBOX_STORE);
  for (const urlHash of urlHashes) store.delete(urlHash);
  await transactionDone(transaction);
}

export async function clearQueued(): Promise<void> {
  await withNamedStore(OUTBOX_STORE, "readwrite", (store) => store.clear());
  await deleteUnreferencedObjects();
}

export async function clearConnectionData(): Promise<void> {
  const database = await openDatabase();
  const stores = [OUTBOX_STORE, VISITS_STORE, CONTENT_STORE, IMAGE_STORE];
  const transaction = database.transaction(stores, "readwrite");
  for (const store of stores) transaction.objectStore(store).clear();
  await transactionDone(transaction);
}

export async function deleteVisitAggregates(urlHashes: string[]): Promise<void> {
  for (const urlHash of urlHashes) {
    await withNamedStore(VISITS_STORE, "readwrite", (store) =>
      store.delete(urlHash),
    );
  }
}

export async function purgeDomains(domains: string[]): Promise<void> {
  if (!domains.length) return;
  const matches = (hostname: string | undefined) =>
    typeof hostname === "string" &&
    domains.some((domain) => hostnameMatches(hostname, domain, true));
  const queued = await listQueued();
  await deleteQueued(
    queued
      .filter((capture) => {
        try {
          return matches(new URL(capture.url).hostname);
        } catch {
          return false;
        }
      })
      .map((capture) => capture.urlHash),
  );
  const visits = await withNamedStore<VisitAggregate[]>(
    VISITS_STORE,
    "readonly",
    (store) => store.getAll(),
  );
  await deleteVisitAggregates(
    visits
      .filter((visit) => matches(visit.hostname))
      .map((visit) => visit.urlHash),
  );
  await deleteUnreferencedObjects();
}

export async function applyServerPolicies(
  batch: QueuedCapture[],
  domainRules: DomainRule[],
  systemRules: SystemRule[],
): Promise<QueuedCapture[]> {
  const queued = await listQueued();
  const excludedUrlHashes: string[] = [];
  const metadataOnly: QueuedCapture[] = [];
  for (const capture of queued) {
    const mode = serverCaptureMode(capture.url, domainRules, systemRules);
    if (mode === "exclude") {
      excludedUrlHashes.push(capture.urlHash);
    } else if (mode === "metadata_only") {
      metadataOnly.push({
        ...capture,
        legacy_text: "",
        legacy_text_excerpt: "",
        content_hash: null,
        lead_image_hash: null,
        favicon_image_hash: null,
        captured_at: null,
      });
    }
  }
  await deleteQueued(excludedUrlHashes);
  await deleteVisitAggregates(excludedUrlHashes);
  for (const capture of metadataOnly) {
    await withNamedStore(OUTBOX_STORE, "readwrite", (store) =>
      store.put(capture),
    );
  }
  if (excludedUrlHashes.length || metadataOnly.length) {
    await deleteUnreferencedObjects();
  }
  const remaining = new Map(
    (await listQueued()).map((capture) => [capture.urlHash, capture]),
  );
  return batch
    .map((capture) => remaining.get(capture.urlHash))
    .filter((capture): capture is QueuedCapture => capture !== undefined);
}

export async function downgradeFailedObject(
  batch: QueuedCapture[],
  storeName: "content" | "images",
  hash: string,
): Promise<QueuedCapture[]> {
  const queued = await listQueued();
  const database = await openDatabase();
  const transaction = database.transaction(
    [OUTBOX_STORE, storeName],
    "readwrite",
  );
  const outbox = transaction.objectStore(OUTBOX_STORE);
  for (const capture of queued) {
    let downgraded: QueuedCapture | null = null;
    if (storeName === CONTENT_STORE && capture.content_hash === hash) {
      downgraded = {
        ...capture,
        legacy_text: "",
        legacy_text_excerpt: "",
        content_hash: null,
        lead_image_hash: null,
        captured_at: null,
      };
    } else if (
      storeName === IMAGE_STORE &&
      (capture.lead_image_hash === hash || capture.favicon_image_hash === hash)
    ) {
      downgraded = {
        ...capture,
        lead_image_hash:
          capture.lead_image_hash === hash ? null : capture.lead_image_hash,
        favicon_image_hash:
          capture.favicon_image_hash === hash
            ? null
            : capture.favicon_image_hash,
      };
    }
    if (downgraded) {
      outbox.put(downgraded);
    }
  }
  transaction.objectStore(storeName).delete(hash);
  await transactionDone(transaction);
  await deleteUnreferencedObjects();
  const remaining = new Map(
    (await listQueued()).map((capture) => [capture.urlHash, capture]),
  );
  return batch
    .map((capture) => remaining.get(capture.urlHash))
    .filter((capture): capture is QueuedCapture => capture !== undefined);
}

export async function readSyncBatch(now = Date.now()): Promise<QueuedCapture[]> {
  const captures = await listQueued();
  const expired = captures.filter(
    (capture) => now - capture.queuedAt > MAX_QUEUE_AGE_MS,
  );
  await deleteQueued(expired.map((capture) => capture.urlHash));
  if (expired.length) await deleteUnreferencedObjects();
  const active = captures
    .filter((capture) => now - capture.queuedAt <= MAX_QUEUE_AGE_MS)
    .sort((a, b) => a.queuedAt - b.queuedAt);
  const batch: QueuedCapture[] = [];
  for (const capture of active) {
    if (batch.length >= MAX_SYNC_RECORDS) break;
    const candidate = [...batch, capture].map((item) => toSyncRecord(item));
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

export function toSyncRecord(
  capture: QueuedCapture,
  contentCapabilityRevision = 0,
) {
  const useContentV2 =
    contentCapabilityRevision >= 2 && Boolean(capture.content_hash);
  return {
    record_id: capture.record_id,
    url: capture.url,
    title: capture.title,
    text: useContentV2 ? "" : legacyTextFor(capture),
    text_excerpt: useContentV2 ? "" : legacyExcerptFor(capture),
    first_visited_at: capture.first_visited_at,
    last_visited_at: capture.last_visited_at,
    captured_at: capture.captured_at,
    visit_count: capture.visit_count,
    known_revision: capture.known_revision,
    content_hash: useContentV2 ? capture.content_hash : undefined,
    lead_image_hash: useContentV2 ? capture.lead_image_hash : undefined,
    favicon_image_hash:
      contentCapabilityRevision >= 2
        ? capture.favicon_image_hash
        : undefined,
  };
}

export async function contentForBatch(
  batch: QueuedCapture[],
): Promise<{ documents: QueuedContent[]; images: QueuedImage[] }> {
  const documentHashes = new Set(
    batch
      .map((capture) => capture.content_hash)
      .filter((value): value is string => Boolean(value)),
  );
  const imageHashes = new Set(
    batch
      .flatMap((capture) => [
        capture.lead_image_hash,
        capture.favicon_image_hash,
      ])
      .filter((value): value is string => Boolean(value)),
  );
  const documents = await Promise.all(
    [...documentHashes].map((hash) =>
      withNamedStore<QueuedContent | undefined>(
        CONTENT_STORE,
        "readonly",
        (store) => store.get(hash),
      ),
    ),
  );
  const images = await Promise.all(
    [...imageHashes].map((hash) =>
      withNamedStore<QueuedImage | undefined>(
        IMAGE_STORE,
        "readonly",
        (store) => store.get(hash),
      ),
    ),
  );
  if (documents.some((item) => !item) || images.some((item) => !item)) {
    throw new Error("Queued history content is missing from local storage");
  }
  return {
    documents: documents.filter(
      (item): item is QueuedContent => item !== undefined,
    ),
    images: images.filter((item): item is QueuedImage => item !== undefined),
  };
}

export async function markContentUploaded(
  storeName: "content" | "images",
  hash: string,
): Promise<void> {
  const key = storeName === CONTENT_STORE ? "contentHash" : "imageHash";
  const value = await withNamedStore<
    QueuedContent | QueuedImage | undefined
  >(storeName, "readonly", (store) => store.get(hash));
  if (!value) return;
  value.uploadState = "uploaded";
  value.lastError = "";
  value.updatedAt = Date.now();
  await withNamedStore(storeName, "readwrite", (store) =>
    store.put({ ...value, [key]: hash }),
  );
}

export async function deleteUnreferencedObjects(): Promise<void> {
  const captures = await listQueued();
  const referencedContent = new Set(
    captures
      .map((capture) => capture.content_hash)
      .filter((value): value is string => Boolean(value)),
  );
  const referencedImages = new Set(
    captures
      .flatMap((capture) => [
        capture.lead_image_hash,
        capture.favicon_image_hash,
      ])
      .filter((value): value is string => Boolean(value)),
  );
  const [contents, images] = await Promise.all([
    withNamedStore<QueuedContent[]>(CONTENT_STORE, "readonly", (store) =>
      store.getAll(),
    ),
    withNamedStore<QueuedImage[]>(IMAGE_STORE, "readonly", (store) =>
      store.getAll(),
    ),
  ]);
  for (const content of contents) {
    if (!referencedContent.has(content.contentHash)) {
      await withNamedStore(CONTENT_STORE, "readwrite", (store) =>
        store.delete(content.contentHash),
      );
    }
  }
  for (const image of images) {
    if (!referencedImages.has(image.imageHash)) {
      await withNamedStore(IMAGE_STORE, "readwrite", (store) =>
        store.delete(image.imageHash),
      );
    }
  }
}

export function imageBytes(image: QueuedImage): Uint8Array {
  const binary = atob(image.bytesBase64);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

export function resetOutboxForTests(): void {
  databasePromise = null;
}

async function putContentIfAbsent(
  contentHash: string,
  canonicalJson: string,
): Promise<void> {
  const existing = await withNamedStore<QueuedContent | undefined>(
    CONTENT_STORE,
    "readonly",
    (store) => store.get(contentHash),
  );
  if (existing) return;
  await withNamedStore(CONTENT_STORE, "readwrite", (store) =>
    store.put({
      contentHash,
      canonicalJson,
      uploadState: "pending",
      lastError: "",
      updatedAt: Date.now(),
    } satisfies QueuedContent),
  );
}

async function queueImage(image: CapturedImage | null): Promise<string | null> {
  if (!image) return null;
  if (
    !Number.isInteger(image.width) ||
    !Number.isInteger(image.height) ||
    image.width < 1 ||
    image.height < 1 ||
    image.width > 640 ||
    image.height > 640 ||
    image.bytesBase64.length > 280_000
  ) {
    return null;
  }
  let bytes: Uint8Array;
  try {
    bytes = imageBytes({
      imageHash: "",
      bytesBase64: image.bytesBase64,
      contentType: image.contentType,
      uploadState: "pending",
      lastError: "",
      updatedAt: 0,
    });
  } catch {
    return null;
  }
  if (bytes.byteLength > 200 * 1024) return null;
  const imageHash = await sha256Bytes(bytes);
  const existing = await withNamedStore<QueuedImage | undefined>(
    IMAGE_STORE,
    "readonly",
    (store) => store.get(imageHash),
  );
  if (!existing) {
    await withNamedStore(IMAGE_STORE, "readwrite", (store) =>
      store.put({
        imageHash,
        bytesBase64: image.bytesBase64,
        contentType: image.contentType,
        uploadState: "pending",
        lastError: "",
        updatedAt: Date.now(),
      } satisfies QueuedImage),
    );
  }
  return imageHash;
}

function legacyTextFor(capture: QueuedCapture): string {
  return (
    capture.legacy_text ??
    (capture as QueuedCapture & { text?: string }).text ??
    ""
  );
}

function legacyExcerptFor(capture: QueuedCapture): string {
  return (
    capture.legacy_text_excerpt ??
    (capture as QueuedCapture & { text_excerpt?: string }).text_excerpt ??
    ""
  );
}

function serverCaptureMode(
  url: string,
  domainRules: DomainRule[],
  systemRules: SystemRule[],
): "full" | "metadata_only" | "exclude" {
  let normalized: URL;
  try {
    normalized = new URL(url);
  } catch {
    return "exclude";
  }
  if (matchingSystemRule(normalized, systemRules)) return "exclude";
  let mode: "full" | "metadata_only" = "full";
  for (const rule of domainRules) {
    if (!hostnameMatches(normalized.hostname, rule.hostname, rule.match_subdomains)) {
      continue;
    }
    if (rule.mode === "exclude") return "exclude";
    mode = "metadata_only";
  }
  return mode;
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}
