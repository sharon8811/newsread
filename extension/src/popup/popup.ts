import type { ExtensionStatus } from "../types.js";
import { permissionPattern } from "../url.js";

const element = <T extends HTMLElement>(id: string) =>
  document.getElementById(id) as T;

async function message<T>(payload: object): Promise<T> {
  const response = await chrome.runtime.sendMessage(payload);
  if (!response?.ok) throw new Error(response?.error || "Extension action failed");
  return response.value as T;
}

function showError(error: unknown): void {
  element("error").textContent =
    error instanceof Error ? error.message : "Extension action failed";
}

function render(status: ExtensionStatus): void {
  element("pairing").hidden = status.paired;
  element("controls").hidden = !status.paired;
  element("status-dot").dataset.state = status.connectionStatus;
  element("status-text").textContent = status.paired
    ? status.connectionStatus === "paired"
      ? `${status.userName} · ${status.connectionName}`
      : status.lastError || "Connection needs attention"
    : "Pair this browser with NewsRead";
  element("queue-count").textContent = String(status.queueCount);
  (element<HTMLInputElement>("paused")).checked = !status.paused;
  (element<HTMLSelectElement>("capture-mode")).value = status.captureMode;
  if (status.serverUrl) {
    (element<HTMLAnchorElement>("open-history")).href =
      `${status.serverUrl}/history`;
  }
}

element("pairing").addEventListener("submit", async (event) => {
  event.preventDefault();
  element("error").textContent = "";
  try {
    const serverUrl = element<HTMLInputElement>("server-url").value.trim();
    const token = element<HTMLInputElement>("token").value.trim();
    const granted = await chrome.permissions.request({
      origins: [permissionPattern(serverUrl)],
    });
    if (!granted) throw new Error("Server access was not granted");
    render(await message({ type: "PAIR", serverUrl, token }));
    element<HTMLInputElement>("token").value = "";
  } catch (error) {
    showError(error);
  }
});

element("paused").addEventListener("change", async () => {
  try {
    render(
      await message({
        type: "SET_PAUSED",
        paused: !element<HTMLInputElement>("paused").checked,
      }),
    );
  } catch (error) {
    showError(error);
  }
});

element("capture-mode").addEventListener("change", async () => {
  try {
    render(
      await message({
        type: "SET_CAPTURE_MODE",
        captureMode: element<HTMLSelectElement>("capture-mode").value,
      }),
    );
  } catch (error) {
    showError(error);
  }
});

element("index-current").addEventListener("click", async () => {
  try {
    render(await message({ type: "INDEX_CURRENT" }));
  } catch (error) {
    showError(error);
  }
});

element("sync-now").addEventListener("click", async () => {
  try {
    render(await message({ type: "SYNC_NOW" }));
  } catch (error) {
    showError(error);
  }
});

element("open-options").addEventListener("click", () => {
  void chrome.runtime.openOptionsPage();
});

void message<ExtensionStatus>({ type: "GET_STATUS" }).then(render, showError);
