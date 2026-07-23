import type { ExtensionSettings, SyncState } from "./types.js";

export const SETTINGS_KEY = "newsreadHistorySettings";
export const SYNC_STATE_KEY = "newsreadHistorySyncState";
export const SYNC_ALARM = "newsread-history-sync";
export const MAX_QUEUE_AGE_MS = 30 * 24 * 60 * 60 * 1000;
export const MAX_QUEUE_ENTRIES = 20_000;
export const MAX_SYNC_RECORDS = 100;
export const MAX_SYNC_BYTES = 900 * 1024;

export const DEFAULT_SETTINGS: ExtensionSettings = {
  serverUrl: "",
  token: "",
  paused: false,
  captureMode: "full",
  excludedDomains: [],
  knownRevision: 0,
  domainRules: [],
  connectionStatus: "unpaired",
  connectionId: null,
  connectionName: "",
  userName: "",
  lastSyncAt: null,
};

export const DEFAULT_SYNC_STATE: SyncState = {
  attempt: 0,
  nextRetryAt: 0,
  lastError: "",
};
