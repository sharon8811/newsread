import { JSDOM } from "jsdom";
import { afterEach, describe, expect, it, vi } from "vitest";

async function loadContentScript(html: string) {
  const dom = new JSDOM(html, { url: "https://spa.example.com/route" });
  Object.defineProperty(dom.window.HTMLElement.prototype, "innerText", {
    configurable: true,
    get() {
      return this.textContent ?? "";
    },
  });
  const sendMessage = vi.fn().mockImplementation((message: { type: string }) =>
    Promise.resolve(
      message.type === "GET_PENDING_CITATION"
        ? { ok: true, value: null }
        : undefined,
    ),
  );
  vi.stubGlobal("document", dom.window.document);
  vi.stubGlobal("location", dom.window.location);
  vi.stubGlobal("Node", dom.window.Node);
  vi.stubGlobal("NodeFilter", dom.window.NodeFilter);
  vi.stubGlobal("Element", dom.window.Element);
  vi.stubGlobal("MutationObserver", dom.window.MutationObserver);
  vi.stubGlobal(
    "getComputedStyle",
    dom.window.getComputedStyle.bind(dom.window),
  );
  vi.stubGlobal("chrome", {
    runtime: {
      sendMessage,
      onMessage: { addListener: vi.fn() },
    },
  });
  vi.resetModules();
  await import("./content.js");
  await vi.waitFor(() =>
    expect(sendMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: "CAPTURE_PAGE" }),
    ),
  );
  return sendMessage;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("content-script metadata fallback", () => {
  it("sends URL and title when an SPA has not rendered useful text yet", async () => {
    const sendMessage = await loadContentScript(
      "<title>Loading article</title><main><p>Loading…</p></main>",
    );

    expect(sendMessage).toHaveBeenCalledWith({
      type: "CAPTURE_PAGE",
      candidate: expect.objectContaining({
        url: "https://spa.example.com/route",
        title: "Loading article",
        document: null,
        leadImage: null,
        favicon: null,
      }),
    });
  });
});
