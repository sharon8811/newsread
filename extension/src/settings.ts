import {
  DEFAULT_SETTINGS,
  DEFAULT_SYNC_STATE,
  SETTINGS_KEY,
  SYNC_STATE_KEY,
} from "./config.js";
import type { ExtensionSettings, SyncState } from "./types.js";

export async function getSettings(): Promise<ExtensionSettings> {
  const stored = await chrome.storage.local.get(SETTINGS_KEY);
  return {
    ...DEFAULT_SETTINGS,
    ...(stored[SETTINGS_KEY] as Partial<ExtensionSettings> | undefined),
  };
}

export async function saveSettings(
  patch: Partial<ExtensionSettings>,
): Promise<ExtensionSettings> {
  const settings = { ...(await getSettings()), ...patch };
  await chrome.storage.local.set({ [SETTINGS_KEY]: settings });
  return settings;
}

export async function getSyncState(): Promise<SyncState> {
  const stored = await chrome.storage.local.get(SYNC_STATE_KEY);
  return {
    ...DEFAULT_SYNC_STATE,
    ...(stored[SYNC_STATE_KEY] as Partial<SyncState> | undefined),
  };
}

export async function saveSyncState(state: SyncState): Promise<void> {
  await chrome.storage.local.set({ [SYNC_STATE_KEY]: state });
}
