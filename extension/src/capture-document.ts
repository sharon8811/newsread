import type {
  CaptureBlock,
  CaptureBlockKind,
  CaptureDocument,
} from "./types.js";

export const CONTENT_HASH_PREFIX = "newsread-history-content-v1\0";
export const MAX_BLOCKS = 512;
export const MAX_BLOCK_CHARS = 8_000;
export const MAX_DOCUMENT_CHARS = 200_000;
const MIN_USEFUL_CHARACTERS = 200;

const selectors = [
  ["h1,h2,h3,h4,h5,h6", "heading"],
  ["p", "paragraph"],
  ["li", "list_item"],
  ["blockquote", "quote"],
  ["pre", "code"],
] as const;

export function normalizeBlockText(
  value: string,
  kind: CaptureBlockKind,
): string {
  const normalized = value
    .replace(/\r\n?/g, "\n")
    .normalize("NFC")
    .replace(
      /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}]/gu,
      (character) => (character === "\n" || character === "\t" ? character : ""),
    );
  if (kind !== "code") return normalized.replace(/\s+/g, " ").trim();
  return normalized
    .split("\n")
    .map((line) => line.replace(/\s+/g, " ").trim())
    .join("\n")
    .replace(/^\n+|\n+$/g, "");
}

export function canonicalizeCaptureDocument(document: CaptureDocument): {
  document: CaptureDocument;
  canonicalJson: string;
  text: string;
} {
  let characterCount = 0;
  const blocks: CaptureBlock[] = [];
  for (const source of document.blocks.slice(0, MAX_BLOCKS)) {
    const text = normalizeBlockText(source.text, source.kind).slice(
      0,
      MAX_BLOCK_CHARS,
    );
    if (!text) continue;
    const remaining = MAX_DOCUMENT_CHARS - characterCount;
    if (remaining <= 0) break;
    const bounded = text.slice(0, remaining);
    blocks.push({
      id: `b${String(blocks.length + 1).padStart(4, "0")}`,
      kind: source.kind,
      text: bounded,
    });
    characterCount += bounded.length;
  }
  if (!blocks.length) throw new Error("Capture document has no text blocks");
  const canonical: CaptureDocument = {
    schema_version: 1,
    extraction_version: "history-dom-v2",
    content_type: document.content_type,
    language: normalizeLanguage(document.language),
    blocks,
  };
  return {
    document: canonical,
    canonicalJson: JSON.stringify(canonical),
    text: blocks.map((block) => block.text).join("\n"),
  };
}

export async function hashCaptureDocument(canonicalJson: string): Promise<string> {
  const bytes = new TextEncoder().encode(
    `${CONTENT_HASH_PREFIX}${canonicalJson}`,
  );
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return hex(new Uint8Array(digest));
}

export async function sha256Bytes(value: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    Uint8Array.from(value).buffer,
  );
  return hex(new Uint8Array(digest));
}

export function hasHistoryCaptureOptOut(source: Document = document): boolean {
  return (
    source
      .querySelector<HTMLMetaElement>('meta[name="newsread-history"]')
      ?.content.trim()
      .toLowerCase() === "no-capture"
  );
}

export function extractCaptureDocument(
  source: Document = document,
): CaptureDocument | null {
  if (!source.contentType.toLowerCase().startsWith("text/html")) return null;
  const article = source.querySelector<HTMLElement>("article");
  const root =
    article ?? source.querySelector<HTMLElement>("main") ?? source.body;
  if (!root) return null;

  const candidates: { element: HTMLElement; kind: CaptureBlockKind }[] = [];
  for (const [selector, kind] of selectors) {
    for (const element of root.querySelectorAll<HTMLElement>(selector)) {
      if (
        kind === "paragraph" &&
        element.closest("li, blockquote") !== null
      ) {
        continue;
      }
      candidates.push({ element, kind });
    }
  }
  candidates.sort((left, right) => {
    const position = left.element.compareDocumentPosition(right.element);
    if (position & Node.DOCUMENT_POSITION_PRECEDING) return 1;
    if (position & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
    return 0;
  });

  const blocks: CaptureBlock[] = [];
  let characters = 0;
  for (const candidate of candidates) {
    if (!isVisible(candidate.element)) continue;
    const text = normalizeBlockText(
      candidate.element.innerText,
      candidate.kind,
    );
    if (!text || (candidate.kind === "paragraph" && text.length < 20)) continue;
    const remaining = MAX_DOCUMENT_CHARS - characters;
    if (remaining <= 0 || blocks.length >= MAX_BLOCKS) break;
    const bounded = text.slice(0, Math.min(MAX_BLOCK_CHARS, remaining));
    blocks.push({
      id: `b${String(blocks.length + 1).padStart(4, "0")}`,
      kind: candidate.kind,
      text: bounded,
    });
    characters += bounded.length;
  }

  if (characters < MIN_USEFUL_CHARACTERS) {
    const fallback = normalizeBlockText(root.innerText, "paragraph");
    if (fallback.length < MIN_USEFUL_CHARACTERS) return null;
    blocks.length = 0;
    blocks.push({
      id: "b0001",
      kind: "paragraph",
      text: fallback.slice(0, MAX_DOCUMENT_CHARS),
    });
  }
  return canonicalizeCaptureDocument({
    schema_version: 1,
    extraction_version: "history-dom-v2",
    content_type:
      article ||
      source
        .querySelector<HTMLMetaElement>('meta[property="og:type"]')
        ?.content.toLowerCase() === "article"
        ? "article"
        : "page",
    language: source.documentElement.lang,
    blocks,
  }).document;
}

function normalizeLanguage(value: string): string {
  const normalized = value.trim().replace(/_/g, "-").slice(0, 35);
  return /^[A-Za-z0-9][A-Za-z0-9-]{0,34}$/.test(normalized)
    ? normalized.toLowerCase()
    : "";
}

function isVisible(element: HTMLElement): boolean {
  const style = getComputedStyle(element);
  return (
    style.display !== "none" &&
    style.visibility !== "hidden" &&
    element.getAttribute("aria-hidden") !== "true"
  );
}

function hex(bytes: Uint8Array): string {
  return [...bytes]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}
