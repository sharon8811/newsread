import { DEFAULT_SYNC_STATE } from "./config.js";
import {
  clearConnectionData,
  deleteQueued,
  readSyncBatch,
  toSyncRecord,
} from "./outbox.js";
import {
  getSettings,
  getSyncState,
  saveSettings,
  saveSyncState,
} from "./settings.js";
import type { DomainRule, ExtensionSettings } from "./types.js";

interface SyncResponse {
  accepted: { record_id: string }[];
  rejected: { record_id: string; code: string }[];
  sync_revision: number;
  domain_rules: DomainRule[];
}

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
  const temporary = { ...(await getSettings()), serverUrl, token };
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
  if (
    temporary.connectionId !== null &&
    temporary.connectionId !== body.connection.id
  ) {
    await clearConnectionData();
  }
  return saveSettings({
    serverUrl: new URL(serverUrl).origin,
    token,
    knownRevision: body.settings.sync_revision,
    domainRules: body.domain_rules,
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
  const batch = await readSyncBatch();
  if (!batch.length) {
    await saveSyncState(DEFAULT_SYNC_STATE);
    return;
  }
  try {
    const response = await fetch(apiUrl(settings, "/history/sync"), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${settings.token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ records: batch.map(toSyncRecord) }),
    });
    if (response.status === 401) {
      await saveSettings({ connectionStatus: "revoked" });
      await recordFailure("Connection revoked");
      return;
    }
    if (response.status === 429) {
      const retryAfter = Number(response.headers.get("Retry-After") || "60");
      await recordFailure("NewsRead asked the extension to slow down", retryAfter * 1000);
      return;
    }
    if (!response.ok) throw new Error(`Sync failed (${response.status})`);
    const body = (await response.json()) as SyncResponse;
    const terminalIds = new Set([
      ...body.accepted.map((item) => item.record_id),
      ...body.rejected.map((item) => item.record_id),
    ]);
    await deleteQueued(
      batch
        .filter((capture) => terminalIds.has(capture.record_id))
        .map((capture) => capture.urlHash),
    );
    await saveSettings({
      knownRevision: body.sync_revision,
      domainRules: body.domain_rules,
      connectionStatus: "paired",
      lastSyncAt: new Date().toISOString(),
    });
    await saveSyncState(DEFAULT_SYNC_STATE);
  } catch (error) {
    await saveSettings({ connectionStatus: "error" });
    await recordFailure(error instanceof Error ? error.message : "Sync failed");
  }
}
