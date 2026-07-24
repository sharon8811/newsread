import { getSettings } from "../settings.js";
import type { ExtensionStatus } from "../types.js";
import { normalizeHostname } from "../url.js";

const element = <T extends HTMLElement>(id: string) =>
  document.getElementById(id) as T;
let importing = false;

async function message<T>(payload: object): Promise<T> {
  const response = await chrome.runtime.sendMessage(payload);
  if (!response?.ok) throw new Error(response?.error || "Extension action failed");
  return response.value as T;
}

function notice(value: string): void {
  element("notice").textContent = value;
}

async function renderDomains(): Promise<void> {
  const settings = await getSettings();
  const list = element<HTMLUListElement>("domains");
  list.replaceChildren(
    ...settings.excludedDomains.map((domain) => {
      const item = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = domain;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "Remove";
      remove.addEventListener("click", async () => {
        await message({
          type: "SAVE_EXCLUSIONS",
          domains: settings.excludedDomains.filter((value) => value !== domain),
        });
        await renderDomains();
      });
      item.append(label, remove);
      return item;
    }),
  );
}

element("exclude-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = element<HTMLInputElement>("domain");
  const domain = normalizeHostname(input.value);
  if (!domain) {
    notice("Enter a full domain such as example.com.");
    return;
  }
  const settings = await getSettings();
  await message({
    type: "SAVE_EXCLUSIONS",
    domains: [...new Set([...settings.excludedDomains, domain])].sort(),
  });
  input.value = "";
  notice("Exclusion saved.");
  await renderDomains();
});

element("import").addEventListener("click", async () => {
  const granted = await chrome.permissions.request({ permissions: ["history"] });
  if (!granted) {
    notice("Chrome history access was not granted.");
    return;
  }
  importing = true;
  element<HTMLButtonElement>("import").disabled = true;
  element<HTMLButtonElement>("cancel-import").disabled = false;
  const progress = element("import-progress");
  try {
    const entries = await chrome.history.search({
      text: "",
      startTime: Date.now() - 365 * 24 * 60 * 60 * 1000,
      maxResults: 20_000,
    });
    let imported = 0;
    for (let offset = 0; offset < entries.length && importing; offset += 100) {
      const result = await message<{ imported: number }>({
        type: "IMPORT_HISTORY",
        entries: entries.slice(offset, offset + 100),
      });
      imported += result.imported;
      progress.textContent = `Imported ${imported} of ${entries.length}`;
    }
    progress.textContent = importing
      ? `Import complete: ${imported} pages queued`
      : `Import cancelled: ${imported} pages queued`;
  } catch (error) {
    progress.textContent =
      error instanceof Error ? error.message : "History import failed";
  } finally {
    importing = false;
    element<HTMLButtonElement>("import").disabled = false;
    element<HTMLButtonElement>("cancel-import").disabled = true;
  }
});

element("cancel-import").addEventListener("click", () => {
  importing = false;
});

element("clear-queue").addEventListener("click", async () => {
  if (!confirm("Clear every capture waiting on this device?")) return;
  const status = await message<ExtensionStatus>({ type: "CLEAR_QUEUE" });
  notice(`Local queue cleared. ${status.queueCount} captures remain.`);
});

element("disconnect").addEventListener("click", async () => {
  if (!confirm("Disconnect this browser from NewsRead?")) return;
  await message({ type: "DISCONNECT" });
  notice("Browser disconnected. The local queue is unchanged.");
});

void renderDomains();
