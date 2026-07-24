import { SYNC_ALARM } from "./config.js";
import {
  clearQueued,
  countQueued,
  enqueueCapture,
  enqueueHistoryMetadata,
} from "./outbox.js";
import { isAllowedMessageSender } from "./message-sender.js";
import { getSettings, getSyncState, saveSettings } from "./settings.js";
import { checkConnection, syncNow } from "./sync.js";
import type { CaptureCandidate, ExtensionStatus } from "./types.js";

async function updateBadge(): Promise<void> {
  const [count, syncState] = await Promise.all([countQueued(), getSyncState()]);
  await chrome.action.setBadgeBackgroundColor({
    color: syncState.lastError ? "#b42318" : "#315d58",
  });
  await chrome.action.setBadgeText({
    text: syncState.lastError ? "!" : count ? String(Math.min(count, 99)) : "",
  });
}

async function status(): Promise<ExtensionStatus> {
  const [settings, queueCount, syncState] = await Promise.all([
    getSettings(),
    countQueued(),
    getSyncState(),
  ]);
  return {
    paired: Boolean(settings.serverUrl && settings.token),
    paused: settings.paused,
    captureMode: settings.captureMode,
    connectionStatus: settings.connectionStatus,
    connectionName: settings.connectionName,
    userName: settings.userName,
    serverUrl: settings.serverUrl,
    lastSyncAt: settings.lastSyncAt,
    queueCount,
    lastError: syncState.lastError,
  };
}

async function captureCandidate(candidate: CaptureCandidate): Promise<boolean> {
  const queued = await enqueueCapture(candidate, await getSettings());
  if (queued) {
    await updateBadge();
    await chrome.alarms.create(SYNC_ALARM, { delayInMinutes: 0.05 });
  }
  return queued;
}

async function indexCurrentTab(): Promise<void> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("No active page to index");
  await chrome.tabs.sendMessage(tab.id, { type: "CAPTURE_NOW" });
}

async function importEntries(
  entries: chrome.history.HistoryItem[],
): Promise<number> {
  const settings = await getSettings();
  let imported = 0;
  for (const entry of entries) {
    if (
      entry.url &&
      entry.lastVisitTime &&
      (await enqueueHistoryMetadata(
        entry.url,
        entry.title ?? "",
        entry.lastVisitTime,
        entry.visitCount ?? 1,
        settings,
      ))
    ) {
      imported += 1;
    }
  }
  await updateBadge();
  return imported;
}

chrome.runtime.onInstalled.addListener(() => {
  void chrome.alarms.create(SYNC_ALARM, { periodInMinutes: 1 });
  void updateBadge();
});

chrome.runtime.onStartup.addListener(() => {
  void chrome.alarms.create(SYNC_ALARM, { periodInMinutes: 1 });
  void syncNow();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== SYNC_ALARM) return;
  void syncNow().finally(updateBadge);
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const handle = async () => {
    if (!isAllowedMessageSender(message?.type, sender)) {
      throw new Error("Extension action rejected from a content script");
    }
    switch (message?.type) {
      case "CAPTURE_PAGE":
        return { queued: await captureCandidate(message.candidate) };
      case "GET_STATUS":
        return status();
      case "PAIR":
        await checkConnection(message.serverUrl, message.token);
        await chrome.alarms.create(SYNC_ALARM, { periodInMinutes: 1 });
        return status();
      case "SET_PAUSED":
        await saveSettings({ paused: Boolean(message.paused) });
        return status();
      case "SET_CAPTURE_MODE":
        await saveSettings({ captureMode: message.captureMode });
        return status();
      case "INDEX_CURRENT":
        await indexCurrentTab();
        return status();
      case "SYNC_NOW":
        await syncNow(true);
        await updateBadge();
        return status();
      case "CLEAR_QUEUE":
        await clearQueued();
        await updateBadge();
        return status();
      case "DISCONNECT":
        await saveSettings({
          serverUrl: "",
          token: "",
          connectionStatus: "unpaired",
          connectionName: "",
          userName: "",
          domainRules: [],
          knownRevision: 0,
        });
        return status();
      case "SAVE_EXCLUSIONS":
        await saveSettings({ excludedDomains: message.domains });
        return status();
      case "IMPORT_HISTORY":
        return { imported: await importEntries(message.entries) };
      default:
        return undefined;
    }
  };
  void handle().then(
    (value) => sendResponse({ ok: true, value }),
    (error) =>
      sendResponse({
        ok: false,
        error: error instanceof Error ? error.message : "Extension action failed",
      }),
  );
  return true;
});
