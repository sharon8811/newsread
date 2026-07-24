import { JSDOM } from "jsdom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { handleCitationClick } from "./citation-content.js";

function clickFixture() {
  const dom = new JSDOM(
    `<a
      href="https://article.example.com/story#:~:text=Exact%20quote"
      data-newsread-citation='{"version":1,"url":"https://article.example.com/story","anchor":{"quote":"Exact quote","prefix":null,"suffix":null}}'
    >Open highlighted source</a>`,
    { url: "https://newsread.example.com/history/documents/12" },
  );
  vi.stubGlobal("Element", dom.window.Element);
  const link = dom.window.document.querySelector("a")!;
  const preventDefault = vi.fn();
  const event = {
    isTrusted: true,
    button: 0,
    defaultPrevented: false,
    target: link,
    preventDefault,
  } as unknown as MouseEvent;
  return { dom, event, preventDefault };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("paired-page citation clicks", () => {
  it("sends a validated navigation only from a trusted user click", async () => {
    const { dom, event, preventDefault } = clickFixture();
    const sendMessage = vi.fn().mockResolvedValue({ ok: true, value: true });
    await handleCitationClick(event, dom.window, { sendMessage });

    expect(preventDefault).toHaveBeenCalledOnce();
    expect(sendMessage).toHaveBeenCalledWith({
      type: "OPEN_CITATION",
      citation: expect.objectContaining({
        version: 1,
        url: "https://article.example.com/story",
        highlightUrl:
          "https://article.example.com/story#:~:text=Exact%20quote",
      }),
    });
  });

  it("ignores synthetic clicks", async () => {
    const { dom, event, preventDefault } = clickFixture();
    Object.defineProperty(event, "isTrusted", { value: false });
    const sendMessage = vi.fn();
    await handleCitationClick(event, dom.window, { sendMessage });
    expect(preventDefault).not.toHaveBeenCalled();
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("requires active user activation when the browser exposes it", async () => {
    const { dom, event, preventDefault } = clickFixture();
    Object.defineProperty(dom.window.navigator, "userActivation", {
      configurable: true,
      value: { isActive: false, hasBeenActive: true },
    });
    const sendMessage = vi.fn();
    await handleCitationClick(event, dom.window, { sendMessage });
    expect(preventDefault).not.toHaveBeenCalled();
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("falls back to the native fragment when extension handling fails", async () => {
    const { dom, event } = clickFixture();
    const open = vi.fn();
    Object.defineProperty(dom.window, "open", {
      configurable: true,
      value: open,
    });
    await handleCitationClick(event, dom.window, {
      sendMessage: vi.fn().mockRejectedValue(new Error("worker unavailable")),
    });
    expect(open).toHaveBeenCalledWith(
      "https://article.example.com/story#:~:text=Exact%20quote",
      "_blank",
      "noopener,noreferrer",
    );
  });
});
