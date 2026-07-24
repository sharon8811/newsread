import { JSDOM } from "jsdom";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  findCitationRange,
  highlightCitation,
  highlightCitationWithRetry,
} from "./citation-highlight.js";

function documentView(html: string) {
  const dom = new JSDOM(html, { url: "https://article.example.com/" });
  vi.stubGlobal("Node", dom.window.Node);
  vi.stubGlobal("NodeFilter", dom.window.NodeFilter);
  vi.stubGlobal("MutationObserver", dom.window.MutationObserver);
  Object.defineProperty(dom.window.Element.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
  return { document: dom.window.document, view: dom.window };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("citation range matching", () => {
  it("uses prefix and suffix to select a repeated phrase", () => {
    const { document, view } = documentView(`
      <p>Wrong repeated phrase elsewhere.</p>
      <p>Before repeated phrase After.</p>
    `);
    const range = findCitationRange(document, view, {
      quote: "repeated phrase",
      prefix: "Before",
      suffix: "After.",
    });
    expect(range?.toString()).toBe("repeated phrase");
    expect(range?.startContainer.parentElement?.textContent).toContain(
      "Before repeated phrase After.",
    );
  });

  it("normalizes whitespace across inline elements and supports RTL", () => {
    const { document, view } = documentView(`
      <p>A <strong>precise</strong> source passage.</p>
      <p dir="rtl">לפני שלום עולם אחרי</p>
    `);
    expect(
      findCitationRange(document, view, {
        quote: "A precise source passage.",
        prefix: null,
        suffix: null,
      })?.toString(),
    ).toBe("A precise source passage.");
    expect(
      findCitationRange(document, view, {
        quote: "שלום עולם",
        prefix: "לפני",
        suffix: "אחרי",
      })?.toString(),
    ).toBe("שלום עולם");
  });

  it("does not search iframe content or choose a changed-page near match", () => {
    const { document, view } = documentView(`
      <p>The wording has changed.</p>
      <iframe srcdoc="<p>Original exact quote</p>"></iframe>
    `);
    expect(
      findCitationRange(document, view, {
        quote: "Original exact quote",
        prefix: null,
        suffix: null,
      }),
    ).toBeNull();
    expect(
      highlightCitation(document, view, {
        quote: "Missing exact quote",
        prefix: null,
        suffix: null,
      }),
    ).toBe(false);
    expect(
      document.head.querySelector("[data-newsread-citation-highlight]"),
    ).toBeNull();
  });
});

describe("temporary citation highlighting", () => {
  it("uses the Custom Highlight API and removes temporary styling", () => {
    vi.useFakeTimers();
    const { document, view } = documentView("<p>Exact source quote.</p>");
    const set = vi.fn();
    const remove = vi.fn();
    Object.defineProperty(view, "CSS", {
      configurable: true,
      value: { highlights: { set, delete: remove } },
    });
    Object.defineProperty(view, "Highlight", {
      configurable: true,
      value: class {
        constructor(public range: Range) {}
      },
    });

    expect(
      highlightCitation(document, view, {
        quote: "Exact source quote.",
        prefix: null,
        suffix: null,
      }),
    ).toBe(true);
    expect(set).toHaveBeenCalledOnce();
    expect(
      document.head.querySelector("[data-newsread-citation-highlight]"),
    ).not.toBeNull();

    vi.advanceTimersByTime(8_000);
    expect(remove).toHaveBeenCalledWith("newsread-citation");
    expect(
      document.head.querySelector("[data-newsread-citation-highlight]"),
    ).toBeNull();
  });

  it("retries for a late-rendering SPA and stops after a match", async () => {
    const { document, view } = documentView("<main>Loading…</main>");
    const set = vi.fn();
    Object.defineProperty(view, "CSS", {
      configurable: true,
      value: { highlights: { set, delete: vi.fn() } },
    });
    Object.defineProperty(view, "Highlight", {
      configurable: true,
      value: class {
        constructor(public range: Range) {}
      },
    });
    highlightCitationWithRetry(document, view, {
      quote: "Late exact quote",
      prefix: null,
      suffix: null,
    });

    document.querySelector("main")!.textContent = "Late exact quote";
    await new Promise((resolve) => view.setTimeout(resolve, 110));
    expect(set).toHaveBeenCalledOnce();
  });
});
