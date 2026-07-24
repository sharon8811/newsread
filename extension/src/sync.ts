import { DEFAULT_SYNC_STATE } from "./config.js";
import {
  applyServerPolicies,
  clearConnectionData,
  contentForBatch,
  deleteUnreferencedObjects,
  deleteQueued,
  deleteVisitAggregates,
  downgradeFailedObject,
  imageBytes,
  markContentUploaded,
  readSyncBatch,
  toSyncRecord,
} from "./outbox.js";
import {
  getSettings,
  getSyncState,
  saveSettings,
  saveSyncState,
} from "./settings.js";
import type {
  DomainRule,
  ExtensionSettings,
  QueuedCapture,
  SystemRule,
} from "./types.js";

interface SyncResponse {
  accepted: { record_id: string }[];
  rejected: { record_id: string; code: string }[];
  sync_revision: number;
  domain_rules: DomainRule[];
  system_policy_revision?: number;
  system_rules?: SystemRule[];
  content_capability_revision?: number;
}

interface ContentStatusResponse {
  documents: Record<string, boolean>;
  images: Record<string, boolean>;
  sync_revision: number;
  domain_rules: DomainRule[];
  system_policy_revision: number;
  system_rules: SystemRule[];
  content_capability_revision: number;
}

class SyncRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryAfterMs?: number,
  ) {
    super(message);
  }
}

class PermanentUploadError extends SyncRequestError {}

function apiUrl(settings: ExtensionSettings, path: string): string {
  return `${settings.serverUrl.replace(/\/+$/, "")}/api${path}`;
}

function retryDelay(attempt: number): number {
  const base = Math.min(5 * 60_000, 5_000 * 2 ** Math.min(attempt, 6));
  return Math.round(base * (0.75 + Math.random() * 0.5));
}

async function recordFailure(message: string, retryAfterMs?: number): Promise<void> {
  const current = await getSyncState();
  const attempt = current.attempt + 1;
  await saveSyncState({
    attempt,
    nextRetryAt: Date.now() + (retryAfterMs ?? retryDelay(attempt)),
    lastError: message,
  });
}

export async function checkConnection(
  serverUrl: string,
  token: string,
): Promise<ExtensionSettings> {
  const current = await getSettings();
  const temporary = { ...current, serverUrl, token };
  const response = await fetch(apiUrl(temporary, "/history/sync/status"), {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new Error(
      response.status === 401
        ? "Pairing token was rejected"
        : "Could not reach this NewsRead server",
    );
  }
  const body = await response.json();
  const nextOrigin = new URL(serverUrl).origin;
  let currentOrigin = "";
  try {
    currentOrigin = current.serverUrl ? new URL(current.serverUrl).origin : "";
  } catch {
    // Invalid legacy settings are treated as a different server identity.
    currentOrigin = "invalid";
  }
  if (
    (current.token && current.token !== token) ||
    (currentOrigin && currentOrigin !== nextOrigin) ||
    (current.connectionId !== null &&
      current.connectionId !== body.connection.id)
  ) {
    await clearConnectionData();
  }
  return saveSettings({
    serverUrl: nextOrigin,
    token,
    knownRevision: body.settings.sync_revision,
    domainRules: body.domain_rules,
    systemPolicyRevision: body.system_policy_revision ?? 0,
    systemRules: body.system_rules ?? temporary.systemRules,
    contentCapabilityRevision: body.content_capability_revision ?? 0,
    connectionStatus: "paired",
    connectionId: body.connection.id,
    connectionName: body.connection.name,
    userName: body.user_name,
  });
}

export async function syncNow(force = false): Promise<void> {
  const settings = await getSettings();
  if (!settings.token || !settings.serverUrl || settings.paused) return;
  const syncState = await getSyncState();
  if (!force && syncState.nextRetryAt > Date.now()) return;
  let batch = await readSyncBatch();
  if (!batch.length) {
    await saveSyncState(DEFAULT_SYNC_STATE);
    return;
  }
  try {
    let activeSettings = settings;
    if (settings.contentCapabilityRevision >= 2) {
      const prepared = await ensureContentUploaded(batch, settings);
      batch = prepared.batch;
      activeSettings = prepared.settings;
      if (!batch.length) {
        await saveSyncState(DEFAULT_SYNC_STATE);
        return;
      }
    }
    const response = await fetch(apiUrl(activeSettings, "/history/sync"), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${activeSettings.token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        records: batch.map((capture) =>
          toSyncRecord(capture, activeSettings.contentCapabilityRevision),
        ),
      }),
    });
    if (response.status === 401) {
      throw new SyncRequestError("Connection revoked", 401);
    }
    if (response.status === 429) {
      const retryAfter = Number(response.headers.get("Retry-After") || "60");
      throw new SyncRequestError(
        "NewsRead asked the extension to slow down",
        429,
        retryAfter * 1000,
      );
    }
    if (!response.ok) throw new Error(`Sync failed (${response.status})`);
    const body = (await response.json()) as SyncResponse;
    const responseSystemRules = body.system_rules ?? activeSettings.systemRules;
    const terminalIds = new Set([
      ...body.accepted.map((item) => item.record_id),
      ...body.rejected
        .filter((item) => item.code !== "content_missing")
        .map((item) => item.record_id),
    ]);
    batch = await applyServerPolicies(
      batch,
      body.domain_rules,
      responseSystemRules,
    );
    await deleteQueued(
      batch
        .filter((capture) => terminalIds.has(capture.record_id))
        .map((capture) => capture.urlHash),
    );
    // stale_revision means the server deleted this page; drop the local visit
    // aggregate so a later genuine revisit restarts its history from scratch.
    const staleIds = new Set(
      body.rejected
        .filter((item) => item.code === "stale_revision")
        .map((item) => item.record_id),
    );
    await deleteVisitAggregates(
      batch
        .filter((capture) => staleIds.has(capture.record_id))
        .map((capture) => capture.urlHash),
    );
    await deleteUnreferencedObjects();
    await saveSettings({
      knownRevision: body.sync_revision,
      domainRules: body.domain_rules,
      systemPolicyRevision:
        body.system_policy_revision ?? activeSettings.systemPolicyRevision,
      systemRules: responseSystemRules,
      contentCapabilityRevision:
        body.content_capability_revision ??
        activeSettings.contentCapabilityRevision,
      connectionStatus: "paired",
      lastSyncAt: new Date().toISOString(),
    });
    await saveSyncState(DEFAULT_SYNC_STATE);
  } catch (error) {
    if (error instanceof SyncRequestError && error.status === 401) {
      await saveSettings({ connectionStatus: "revoked" });
      await recordFailure(error.message);
      return;
    }
    if (error instanceof SyncRequestError && error.status === 429) {
      await recordFailure(error.message, error.retryAfterMs);
      return;
    }
    await saveSettings({ connectionStatus: "error" });
    await recordFailure(error instanceof Error ? error.message : "Sync failed");
  }
}

async function ensureContentUploaded(
  batch: QueuedCapture[],
  settings: ExtensionSettings,
): Promise<{ batch: QueuedCapture[]; settings: ExtensionSettings }> {
  const requested = await contentForBatch(batch);
  if (!requested.documents.length && !requested.images.length) {
    return { batch, settings };
  }
  const response = await fetch(apiUrl(settings, "/history/sync/content-status"), {
    method: "POST",
    headers: {
      Authorization: `Bearer ${settings.token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      documents: requested.documents.map((item) => item.contentHash),
      images: requested.images.map((item) => item.imageHash),
    }),
  });
  if (response.status === 401) {
    throw new SyncRequestError("Connection revoked", 401);
  }
  if (response.status === 429) {
    const retryAfter = Number(response.headers.get("Retry-After") || "60");
    throw new SyncRequestError(
      "NewsRead asked the extension to slow down",
      429,
      retryAfter * 1000,
    );
  }
  if (!response.ok) {
    throw new SyncRequestError(
      `Content status failed (${response.status})`,
      response.status,
    );
  }
  const status = (await response.json()) as ContentStatusResponse;
  const activeSettings = await saveSettings({
    knownRevision: status.sync_revision,
    domainRules: status.domain_rules,
    systemPolicyRevision: status.system_policy_revision,
    systemRules: status.system_rules,
    contentCapabilityRevision: status.content_capability_revision,
  });
  let activeBatch = await applyServerPolicies(
    batch,
    status.domain_rules,
    status.system_rules,
  );
  if (activeSettings.contentCapabilityRevision < 2) {
    return { batch: activeBatch, settings: activeSettings };
  }

  const documents = (await contentForBatch(activeBatch)).documents;
  for (const document of documents) {
    if (!status.documents[document.contentHash]) {
      try {
        const encoded = await encodeDocumentUpload(document.canonicalJson);
        await upload(
          apiUrl(settings, `/history/sync/content/${document.contentHash}`),
          settings.token,
          encoded.body,
          "application/json",
          encoded.contentEncoding,
        );
      } catch (error) {
        if (!(error instanceof PermanentUploadError)) throw error;
        activeBatch = await downgradeFailedObject(
          activeBatch,
          "content",
          document.contentHash,
        );
        continue;
      }
    }
    await markContentUploaded("content", document.contentHash);
  }
  const images = (await contentForBatch(activeBatch)).images;
  for (const image of images) {
    if (!status.images[image.imageHash]) {
      try {
        await upload(
          apiUrl(settings, `/history/sync/image/${image.imageHash}`),
          settings.token,
          imageBytes(image),
          image.contentType,
        );
      } catch (error) {
        if (!(error instanceof PermanentUploadError)) throw error;
        activeBatch = await downgradeFailedObject(
          activeBatch,
          "images",
          image.imageHash,
        );
        continue;
      }
    }
    await markContentUploaded("images", image.imageHash);
  }
  return { batch: activeBatch, settings: activeSettings };
}

async function upload(
  url: string,
  token: string,
  body: string | ArrayBuffer | Uint8Array,
  contentType: string,
  contentEncoding?: string,
): Promise<void> {
  const requestBody =
    body instanceof Uint8Array ? Uint8Array.from(body).buffer : body;
  const response = await fetch(url, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": contentType,
      ...(contentEncoding ? { "Content-Encoding": contentEncoding } : {}),
    },
    body: requestBody,
  });
  if (!response.ok) {
    if (response.status === 401) {
      throw new SyncRequestError("Connection revoked", 401);
    }
    if (response.status === 429) {
      const retryAfter = Number(response.headers.get("Retry-After") || "60");
      throw new SyncRequestError(
        "NewsRead asked the extension to slow down",
        429,
        retryAfter * 1000,
      );
    }
    const message = `History content upload failed (${response.status})`;
    if (response.status >= 400 && response.status < 500) {
      throw new PermanentUploadError(message, response.status);
    }
    throw new SyncRequestError(message, response.status);
  }
}

async function encodeDocumentUpload(
  canonicalJson: string,
): Promise<{ body: string | ArrayBuffer; contentEncoding?: string }> {
  if (typeof CompressionStream === "undefined") {
    return { body: canonicalJson };
  }
  try {
    const compressed = new Blob([canonicalJson])
      .stream()
      .pipeThrough(new CompressionStream("gzip"));
    return {
      body: await new Response(compressed).arrayBuffer(),
      contentEncoding: "gzip",
    };
  } catch {
    return { body: canonicalJson };
  }
}
