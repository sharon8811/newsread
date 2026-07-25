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
  const scrollIntoView = vi.fn();
  Object.defineProperty(dom.window.Element.prototype, "scrollIntoView", {
    configurable: true,
    value: scrollIntoView,
  });
  return { document: dom.window.document, view: dom.window, scrollIntoView };
}

/** Give the highlighter's settle debounce room to run. */
function afterSettle(view: Window): Promise<void> {
  return new Promise((resolve) => view.setTimeout(resolve, 400));
}

function stubHighlightApi(view: Window) {
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
  return { set, remove };
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

  it("retries for a late-rendering SPA once its mutations settle", async () => {
    const { document, view } = documentView("<main>Loading…</main>");
    const { set } = stubHighlightApi(view);
    highlightCitationWithRetry(document, view, {
      quote: "Late exact quote",
      prefix: null,
      suffix: null,
    });

    document.querySelector("main")!.textContent = "Late exact quote";
    await afterSettle(view);
    expect(set).toHaveBeenCalledOnce();
  });

  it("re-applies the highlight when hydration replaces the cited nodes", async () => {
    const { document, view, scrollIntoView } = documentView(
      "<main><p>Hydrating exact quote</p></main>",
    );
    const { set } = stubHighlightApi(view);
    const anchor = {
      quote: "Hydrating exact quote",
      prefix: null,
      suffix: null,
    };
    highlightCitationWithRetry(document, view, anchor);
    expect(set).toHaveBeenCalledOnce();
    const original = document.querySelector("p")!.firstChild;

    // What GitHub does after readyState "complete": swap the rendered subtree
    // for a freshly built one carrying the same text.
    const replacement = document.createElement("p");
    replacement.textContent = "Hydrating exact quote";
    document.querySelector("main")!.replaceChildren(replacement);
    await afterSettle(view);

    expect(original!.isConnected).toBe(false);
    expect(set).toHaveBeenCalledTimes(2);
    expect(scrollIntoView).toHaveBeenCalledTimes(2);
  });

  it("stops following the citation once the reader scrolls themselves", async () => {
    const { document, view, scrollIntoView } = documentView(
      "<main><p>Reader owned quote</p></main>",
    );
    stubHighlightApi(view);
    highlightCitationWithRetry(document, view, {
      quote: "Reader owned quote",
      prefix: null,
      suffix: null,
    });
    expect(scrollIntoView).toHaveBeenCalledOnce();

    view.dispatchEvent(new view.Event("wheel"));
    const replacement = document.createElement("p");
    replacement.textContent = "Reader owned quote";
    document.querySelector("main")!.replaceChildren(replacement);
    await afterSettle(view);

    expect(scrollIntoView).toHaveBeenCalledOnce();
  });

  it("gives up watching when the retry window closes", async () => {
    const { document, view } = documentView("<main>Loading…</main>");
    const { set } = stubHighlightApi(view);
    highlightCitationWithRetry(
      document,
      view,
      { quote: "Never rendered quote", prefix: null, suffix: null },
      50,
    );

    await new Promise((resolve) => view.setTimeout(resolve, 60));
    document.querySelector("main")!.textContent = "Never rendered quote";
    await afterSettle(view);
    expect(set).not.toHaveBeenCalled();
  });
});
