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
// A hydrating page reaches readyState "complete" before it renders the cited
// text, so an attempt made the moment the DOM changes lands mid-render: the
// nodes we highlight are replaced again and any smooth scroll we started is
// cancelled by the page's own scroll restoration. Wait for the mutations to
// go quiet, and never wait longer than the cap on a page that never settles.
const SETTLE_MS = 300;
const MAX_SETTLE_WAIT_MS = 1_200;

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

function rangeTarget(range: Range): Element | null {
  return range.startContainer.nodeType === Node.ELEMENT_NODE
    ? (range.startContainer as Element)
    : range.startContainer.parentElement;
}

/** Removing a highlighted node moves the live range's boundaries up to the
 * surviving parent, so `isConnected` still reports true while the range no
 * longer covers anything. Compare the text it actually spans. */
function rangeIsLive(range: Range, anchor: CitationAnchor): boolean {
  return (
    range.startContainer.isConnected &&
    normalizeText(range.toString()) === normalizeText(anchor.quote)
  );
}

/** Whether the cited text sits outside the viewport and needs scrolling to.
 * A layout-less environment reports an all-zero rect; treat that as "visible"
 * so we never fight a document whose geometry we cannot measure. */
function needsScroll(target: Element, view: Window): boolean {
  const rect = target.getBoundingClientRect();
  if (!rect.top && !rect.bottom && !rect.height) return false;
  return rect.top < 0 || rect.bottom > view.innerHeight;
}

function scrollToCitation(target: Element, smooth: boolean): void {
  // An interrupted smooth scroll leaves the reader wherever the page wanted
  // them, so only ask for one once the document has stopped changing.
  target.scrollIntoView({
    behavior: smooth ? "smooth" : "auto",
    block: "center",
  });
}

class CitationHighlighter {
  private readonly registry?: HighlightRegistry;
  private readonly Highlight?: HighlightConstructor;
  private style: HTMLStyleElement | null = null;
  private applied: Range | null = null;
  private settleTimer: number | null = null;
  private deadlineTimer: number | null = null;
  private removalTimer: number | null = null;
  private lastMutationAt: number | null = null;
  private userTookOver = false;
  private stopped = false;
  private readonly observer: MutationObserver;
  private readonly takeOver = () => {
    this.userTookOver = true;
  };

  constructor(
    private readonly document: Document,
    private readonly view: Window,
    private readonly anchor: CitationAnchor,
    private readonly durationMs: number,
  ) {
    const css = (view as Window & { CSS?: { highlights?: HighlightRegistry } })
      .CSS;
    this.registry = css?.highlights;
    this.Highlight = (view as Window & { Highlight?: HighlightConstructor })
      .Highlight;
    this.observer = new MutationObserver(() => this.onMutation());
  }

  /** Watch for the whole window: the first match may arrive late, and a page
   * that re-renders after we highlight would otherwise silently drop it. */
  start(windowMs: number): void {
    for (const type of ["wheel", "touchstart", "keydown", "mousedown"]) {
      this.view.addEventListener(type, this.takeOver, {
        passive: true,
        capture: true,
      });
    }
    this.evaluate();
    this.observer.observe(this.document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    this.view.setTimeout(() => this.stop(), windowMs);
  }

  private onMutation(): void {
    if (this.stopped) return;
    this.lastMutationAt = Date.now();
    if (this.settleTimer !== null) this.view.clearTimeout(this.settleTimer);
    this.settleTimer = this.view.setTimeout(() => {
      this.settleTimer = null;
      this.evaluate();
    }, SETTLE_MS);
    if (this.deadlineTimer === null) {
      this.deadlineTimer = this.view.setTimeout(() => {
        this.deadlineTimer = null;
        this.evaluate();
      }, MAX_SETTLE_WAIT_MS);
    }
  }

  private evaluate(): void {
    if (this.stopped) return;
    if (this.applied && rangeIsLive(this.applied, this.anchor)) {
      const target = rangeTarget(this.applied);
      // The range survived but the page may have scrolled away from it.
      if (target && !this.userTookOver && needsScroll(target, this.view)) {
        scrollToCitation(target, this.settled());
      }
      return;
    }
    const range = findCitationRange(this.document, this.view, this.anchor);
    if (range) this.apply(range);
  }

  private settled(): boolean {
    return (
      this.document.readyState === "complete" &&
      (this.lastMutationAt === null ||
        Date.now() - this.lastMutationAt >= SETTLE_MS)
    );
  }

  private apply(range: Range): void {
    if (this.registry && this.Highlight) {
      if (!this.style) {
        const style = this.document.createElement("style");
        style.dataset.newsreadCitationHighlight = "true";
        style.textContent = `::highlight(${HIGHLIGHT_NAME}) { background: #ffe08a; color: inherit; }`;
        this.document.head?.append(style);
        this.style = style;
      }
      this.registry.set(HIGHLIGHT_NAME, new this.Highlight(range));
    }
    this.applied = range;
    const target = rangeTarget(range);
    if (target && !this.userTookOver) {
      scrollToCitation(target, this.settled());
    }
    // Re-applying restarts the visible window, so a highlight that had to be
    // rebuilt after a re-render is still shown for its full duration.
    if (this.removalTimer !== null) this.view.clearTimeout(this.removalTimer);
    this.removalTimer = this.view.setTimeout(
      () => this.clear(),
      this.durationMs,
    );
  }

  private clear(): void {
    this.stop();
    this.registry?.delete(HIGHLIGHT_NAME);
    this.style?.remove();
    this.style = null;
    this.applied = null;
  }

  private stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    this.observer.disconnect();
    if (this.settleTimer !== null) this.view.clearTimeout(this.settleTimer);
    if (this.deadlineTimer !== null) this.view.clearTimeout(this.deadlineTimer);
    for (const type of ["wheel", "touchstart", "keydown", "mousedown"]) {
      this.view.removeEventListener(type, this.takeOver, { capture: true });
    }
  }
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
  rangeTarget(range)?.scrollIntoView({ behavior: "smooth", block: "center" });

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
  durationMs = HIGHLIGHT_DURATION_MS,
): void {
  new CitationHighlighter(document, view, anchor, durationMs).start(
    retryWindowMs,
  );
}
