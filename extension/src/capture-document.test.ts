import { JSDOM } from "jsdom";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  extractCaptureDocument,
  hasHistoryCaptureOptOut,
} from "./capture-document.js";

function documentFor(html: string): Document {
  const dom = new JSDOM(html, {
    url: "https://article.example.com/story",
    contentType: "text/html",
  });
  Object.defineProperty(dom.window.HTMLElement.prototype, "innerText", {
    configurable: true,
    get() {
      return this.textContent ?? "";
    },
  });
  vi.stubGlobal("Node", dom.window.Node);
  vi.stubGlobal(
    "getComputedStyle",
    dom.window.getComputedStyle.bind(dom.window),
  );
  return dom.window.document;
}

afterEach(() => vi.unstubAllGlobals());

describe("structured browser extraction", () => {
  it("prefers article blocks and preserves their document order", () => {
    const paragraph = "Useful article sentence ".repeat(12);
    const source = documentFor(`
      <main><p>${"unrelated main content ".repeat(12)}</p></main>
      <article>
        <h1>Article heading</h1>
        <p>${paragraph}</p>
        <blockquote><p>A cited observation worth retaining.</p></blockquote>
      </article>
    `);

    const captured = extractCaptureDocument(source);

    expect(captured?.content_type).toBe("article");
    expect(captured?.blocks.map((block) => block.kind)).toEqual([
      "heading",
      "paragraph",
      "quote",
    ]);
    expect(captured?.blocks[1]?.text).toBe(paragraph.trim());
    expect(captured?.blocks.some((block) => block.text.includes("unrelated"))).toBe(
      false,
    );
  });

  it("captures dynamically rendered RTL text from main", () => {
    const source = documentFor('<html lang="he" dir="rtl"><main></main></html>');
    const paragraph = source.createElement("p");
    paragraph.textContent = "זהו טקסט שימושי שנוסף באופן דינמי לעמוד. ".repeat(12);
    source.querySelector("main")?.append(paragraph);

    const captured = extractCaptureDocument(source);

    expect(captured?.language).toBe("he");
    expect(captured?.blocks[0]?.text).toContain("טקסט שימושי");
  });

  it("uses a body-text fallback but rejects low-value pages", () => {
    const useful = documentFor(
      `<body><div>${"Body fallback content with useful context. ".repeat(8)}</div></body>`,
    );
    const lowValue = documentFor("<body><button>Sign in</button></body>");

    expect(extractCaptureDocument(useful)?.blocks).toHaveLength(1);
    expect(extractCaptureDocument(lowValue)).toBeNull();
  });

  it("splits the fallback into citable blocks instead of one wall of text", () => {
    const source = documentFor(
      `<body><div>
        Nav
        sharon8811 wants to merge 1 commit into main from perf/summarize.
        The change raises the summarize concurrency setting from two up to eight.
        Reviewers approved the pull request after the benchmark run had finished.
      </div></body>`,
    );

    const captured = extractCaptureDocument(source);

    expect(captured?.blocks).toHaveLength(3);
    expect(captured?.blocks[0]?.text).toBe(
      "sharon8811 wants to merge 1 commit into main from perf/summarize.",
    );
    expect(captured?.blocks.map((block) => block.id)).toEqual([
      "b0001",
      "b0002",
      "b0003",
    ]);
  });

  it("keeps a page whose content is laid out in short lines", () => {
    const stanza = [
      "The captured page",
      "is laid out in",
      "lines far shorter",
      "than a sentence",
      "as a poem or a",
      "compact table is",
    ];
    const source = documentFor(
      `<body><div>
        Sign in
        ${stanza.join("\n        ")}
        ${stanza.join("\n        ")}
      </div></body>`,
    );

    const captured = extractCaptureDocument(source);

    // Nothing is lost: the short lines join instead of being discarded.
    expect(captured).not.toBeNull();
    expect(captured!.blocks).toHaveLength(1);
    expect(captured!.blocks[0]!.text).toContain("Sign in The captured page");
    expect(captured!.blocks[0]!.text).toContain("compact table is");
  });

  it("drops an isolated short line between real paragraphs", () => {
    const source = documentFor(
      `<body><div>
        sharon8811 wants to merge 1 commit into main from perf/summarize.
        Nav
        The change raises the summarize concurrency setting from two up to eight.
        Reviewers approved the pull request after the benchmark run had finished.
      </div></body>`,
    );

    const captured = extractCaptureDocument(source);

    expect(captured?.blocks).toHaveLength(3);
    expect(
      captured?.blocks.some((block) => block.text.includes("Nav")),
    ).toBe(false);
  });

  it("ignores hidden blocks", () => {
    const source = documentFor(`
      <article>
        <p style="display:none">${"Hidden private text ".repeat(20)}</p>
        <p>${"Visible useful article text ".repeat(12)}</p>
      </article>
    `);

    const captured = extractCaptureDocument(source);

    expect(captured?.blocks).toHaveLength(1);
    expect(captured?.blocks[0]?.text).not.toContain("Hidden");
  });

  it("recognizes the explicit site-owner opt-out", () => {
    const source = documentFor(`
      <head><meta name="newsread-history" content=" no-capture "></head>
      <article><p>${"Otherwise useful text ".repeat(20)}</p></article>
    `);

    expect(hasHistoryCaptureOptOut(source)).toBe(true);
  });
});
