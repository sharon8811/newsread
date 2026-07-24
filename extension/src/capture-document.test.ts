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
