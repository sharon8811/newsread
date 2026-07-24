import type { CitationAnchor } from "./types.js";

const BLOCK_TAGS = new Set([
  "ADDRESS",
  "ARTICLE",
  "ASIDE",
  "BLOCKQUOTE",
  "DD",
  "DIV",
  "DL",
  "DT",
  "FIGCAPTION",
  "FIGURE",
  "FOOTER",
  "H1",
  "H2",
  "H3",
  "H4",
  "H5",
  "H6",
  "HEADER",
  "LI",
  "MAIN",
  "NAV",
  "OL",
  "P",
  "PRE",
  "SECTION",
  "TABLE",
  "TD",
  "TH",
  "TR",
  "UL",
]);
const EXCLUDED_SELECTOR =
  "script,style,noscript,template,[hidden],[aria-hidden='true']";
const HIGHLIGHT_NAME = "newsread-citation";
const HIGHLIGHT_DURATION_MS = 8_000;
const RETRY_WINDOW_MS = 10_000;

interface CharacterLocation {
  node: Text;
  startOffset: number;
  endOffset: number;
}

interface NormalizedDocumentText {
  text: string;
  locations: CharacterLocation[];
}

interface HighlightRegistry {
  set(name: string, highlight: unknown): void;
  delete(name: string): void;
}

type HighlightConstructor = new (...ranges: Range[]) => unknown;

function normalizeText(value: string | null | undefined): string {
  return value?.replace(/\s+/g, " ").trim() ?? "";
}

function nearestBlock(element: Element | null): Element | null {
  let current = element;
  while (current) {
    if (BLOCK_TAGS.has(current.tagName)) return current;
    current = current.parentElement;
  }
  return null;
}

function isVisibleTextNode(node: Text, view: Window): boolean {
  const parent = node.parentElement;
  if (!parent || parent.closest(EXCLUDED_SELECTOR)) return false;
  const style = view.getComputedStyle(parent);
  return style.display !== "none" && style.visibility !== "hidden";
}

function appendBoundary(
  text: string[],
  locations: CharacterLocation[],
  node: Text,
): void {
  if (text.length === 0 || text[text.length - 1] === " ") return;
  text.push(" ");
  locations.push({ node, startOffset: 0, endOffset: 0 });
}

function normalizedDocumentText(
  document: Document,
  view: Window,
): NormalizedDocumentText {
  const text: string[] = [];
  const locations: CharacterLocation[] = [];
  const walker = document.createTreeWalker(
    document.body ?? document.documentElement,
    NodeFilter.SHOW_TEXT,
  );
  let previousBlock: Element | null = null;

  for (let current = walker.nextNode(); current; current = walker.nextNode()) {
    const node = current as Text;
    if (!isVisibleTextNode(node, view) || !node.data) continue;
    const block = nearestBlock(node.parentElement);
    if (previousBlock && block && block !== previousBlock) {
      appendBoundary(text, locations, node);
    }

    for (let index = 0; index < node.data.length; index += 1) {
      const character = node.data[index]!;
      if (/\s/.test(character)) {
        const startOffset = index;
        while (
          index + 1 < node.data.length &&
          /\s/.test(node.data[index + 1]!)
        ) {
          index += 1;
        }
        if (text.length > 0 && text[text.length - 1] !== " ") {
          text.push(" ");
          locations.push({
            node,
            startOffset,
            endOffset: index + 1,
          });
        }
      } else {
        text.push(character);
        locations.push({
          node,
          startOffset: index,
          endOffset: index + 1,
        });
      }
    }
    previousBlock = block;
  }

  while (text[text.length - 1] === " ") {
    text.pop();
    locations.pop();
  }
  return { text: text.join(""), locations };
}

export function findCitationRange(
  document: Document,
  view: Window,
  anchor: CitationAnchor,
): Range | null {
  const quote = normalizeText(anchor.quote);
  const prefix = normalizeText(anchor.prefix);
  const suffix = normalizeText(anchor.suffix);
  if (!quote) return null;

  const normalized = normalizedDocumentText(document, view);
  let start = normalized.text.indexOf(quote);
  while (start !== -1) {
    const end = start + quote.length;
    const prefixMatches =
      !prefix || normalized.text.slice(0, start).trimEnd().endsWith(prefix);
    const suffixMatches =
      !suffix || normalized.text.slice(end).trimStart().startsWith(suffix);
    if (prefixMatches && suffixMatches) {
      const first = normalized.locations[start];
      const last = normalized.locations[end - 1];
      if (!first || !last) return null;
      const range = document.createRange();
      range.setStart(first.node, first.startOffset);
      range.setEnd(last.node, last.endOffset);
      return range;
    }
    start = normalized.text.indexOf(quote, start + 1);
  }
  return null;
}

export function highlightCitationRange(
  document: Document,
  view: Window,
  range: Range,
  durationMs = HIGHLIGHT_DURATION_MS,
): void {
  const css = (view as Window & { CSS?: { highlights?: HighlightRegistry } }).CSS;
  const Highlight = (view as Window & { Highlight?: HighlightConstructor })
    .Highlight;
  const registry = css?.highlights;
  const style = document.createElement("style");
  style.dataset.newsreadCitationHighlight = "true";
  style.textContent = `::highlight(${HIGHLIGHT_NAME}) { background: #ffe08a; color: inherit; }`;
  document.head?.append(style);

  if (registry && Highlight) {
    registry.set(HIGHLIGHT_NAME, new Highlight(range));
  }
  const target =
    range.startContainer.nodeType === Node.ELEMENT_NODE
      ? (range.startContainer as Element)
      : range.startContainer.parentElement;
  target?.scrollIntoView({ behavior: "smooth", block: "center" });

  view.setTimeout(() => {
    registry?.delete(HIGHLIGHT_NAME);
    style.remove();
  }, durationMs);
}

export function highlightCitation(
  document: Document,
  view: Window,
  anchor: CitationAnchor,
): boolean {
  const range = findCitationRange(document, view, anchor);
  if (!range) return false;
  highlightCitationRange(document, view, range);
  return true;
}

export function highlightCitationWithRetry(
  document: Document,
  view: Window,
  anchor: CitationAnchor,
  retryWindowMs = RETRY_WINDOW_MS,
): void {
  if (highlightCitation(document, view, anchor)) return;

  let retryTimer: number | null = null;
  let stopped = false;
  const observer = new MutationObserver(() => {
    if (retryTimer !== null || stopped) return;
    retryTimer = view.setTimeout(() => {
      retryTimer = null;
      if (highlightCitation(document, view, anchor)) {
        stopped = true;
        observer.disconnect();
      }
    }, 100);
  });
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
  });
  view.setTimeout(() => {
    stopped = true;
    observer.disconnect();
    if (retryTimer !== null) view.clearTimeout(retryTimer);
  }, retryWindowMs);
}
