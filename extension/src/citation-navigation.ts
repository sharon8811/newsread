import {
  isAllowedCitationSourceSender,
  isAllowedCitationTargetSender,
} from "./message-sender.js";
import { getSettings } from "./settings.js";
import type {
  CitationAnchor,
  CitationNavigation,
  PendingCitation,
} from "./types.js";

const PENDING_CITATION_PREFIX = "newsreadPendingCitation:";
const PENDING_CITATION_TTL_MS = 15_000;
const MAX_URL_LENGTH = 8_192;
const MAX_QUOTE_LENGTH = 1_000;
const MAX_CONTEXT_LENGTH = 500;

type CitationMessageSender = Pick<
  chrome.runtime.MessageSender,
  "id" | "tab" | "url"
>;

function boundedString(
  value: unknown,
  maximumLength: number,
  allowEmpty = false,
): string | null {
  if (typeof value !== "string" || value.length > maximumLength) return null;
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized || allowEmpty ? normalized : null;
}

function httpUrl(value: unknown): URL | null {
  if (typeof value !== "string" || value.length > MAX_URL_LENGTH) return null;
  try {
    const url = new URL(value);
    if (
      (url.protocol !== "http:" && url.protocol !== "https:") ||
      url.username ||
      url.password
    ) {
      return null;
    }
    return url;
  } catch {
    return null;
  }
}

function sameDocument(left: URL, right: URL): boolean {
  return (
    left.origin === right.origin &&
    left.pathname === right.pathname &&
    left.search === right.search
  );
}

function parseAnchor(value: unknown): CitationAnchor | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  const quote = boundedString(candidate.quote, MAX_QUOTE_LENGTH);
  const prefix =
    candidate.prefix === null || candidate.prefix === undefined
      ? null
      : boundedString(candidate.prefix, MAX_CONTEXT_LENGTH, true);
  const suffix =
    candidate.suffix === null || candidate.suffix === undefined
      ? null
      : boundedString(candidate.suffix, MAX_CONTEXT_LENGTH, true);
  if (
    !quote ||
    (prefix === null && candidate.prefix != null) ||
    (suffix === null && candidate.suffix != null)
  ) {
    return null;
  }
  return { quote, prefix: prefix || null, suffix: suffix || null };
}

export function parseCitationNavigation(
  value: unknown,
): CitationNavigation | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  if (candidate.version !== 1) return null;

  const sourceUrl = httpUrl(candidate.url);
  const highlightUrl = httpUrl(candidate.highlightUrl);
  const anchor = parseAnchor(candidate.anchor);
  if (
    !sourceUrl ||
    !highlightUrl ||
    !anchor ||
    !sameDocument(sourceUrl, highlightUrl) ||
    !highlightUrl.hash.includes(":~:text=")
  ) {
    return null;
  }
  return {
    version: 1,
    url: sourceUrl.toString(),
    highlightUrl: highlightUrl.toString(),
    anchor,
  };
}

export function parseCitationData(
  serialized: string | undefined,
  highlightUrl: string,
): CitationNavigation | null {
  if (!serialized || serialized.length > 4_096) return null;
  try {
    const value = JSON.parse(serialized) as Record<string, unknown>;
    return parseCitationNavigation({ ...value, highlightUrl });
  } catch {
    return null;
  }
}

function pendingKey(tabId: number): string {
  return `${PENDING_CITATION_PREFIX}${tabId}`;
}

export async function openCitation(
  value: unknown,
  sender: CitationMessageSender,
  now = Date.now(),
): Promise<boolean> {
  const settings = await getSettings();
  if (
    !settings.token ||
    !isAllowedCitationSourceSender(
      sender,
      chrome.runtime.id,
      settings.serverUrl,
    )
  ) {
    throw new Error("Citation navigation rejected");
  }

  const citation = parseCitationNavigation(value);
  if (!citation) throw new Error("Invalid citation navigation");

  const tab = await chrome.tabs.create({
    active: true,
    url: citation.highlightUrl,
  });
  if (tab.id === undefined) throw new Error("Could not open citation tab");

  const pending: PendingCitation = {
    targetUrl: citation.url,
    anchor: citation.anchor,
    expiresAt: now + PENDING_CITATION_TTL_MS,
  };
  try {
    await chrome.storage.session.set({ [pendingKey(tab.id)]: pending });
  } catch {
    // The native fragment tab is already open and remains the safe fallback.
    // Do not report failure and cause the content script to open a duplicate.
  }
  return true;
}

export async function claimPendingCitation(
  sender: CitationMessageSender,
  now = Date.now(),
): Promise<CitationAnchor | null> {
  if (
    !isAllowedCitationTargetSender(sender, chrome.runtime.id) ||
    sender.tab?.id === undefined ||
    !sender.url
  ) {
    return null;
  }

  const key = pendingKey(sender.tab.id);
  const stored = await chrome.storage.session.get(key);
  const pending = stored[key] as PendingCitation | undefined;
  if (!pending) return null;

  const currentUrl = httpUrl(sender.url);
  const targetUrl = httpUrl(pending.targetUrl);
  if (
    pending.expiresAt < now ||
    !currentUrl ||
    !targetUrl ||
    !sameDocument(currentUrl, targetUrl)
  ) {
    await chrome.storage.session.remove(key);
    return null;
  }

  await chrome.storage.session.remove(key);
  return parseAnchor(pending.anchor);
}
