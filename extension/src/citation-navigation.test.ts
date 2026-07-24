import { beforeEach, describe, expect, it, vi } from "vitest";
import { SETTINGS_KEY } from "./config.js";
import {
  claimPendingCitation,
  openCitation,
  parseCitationData,
  parseCitationNavigation,
} from "./citation-navigation.js";

const extensionId = "newsread-extension-id";
const pairedSettings = {
  serverUrl: "https://newsread.example.com/api",
  token: "paired-token",
};
const citation = {
  version: 1,
  url: "https://article.example.com/story#author-section",
  highlightUrl:
    "https://article.example.com/story#author-section:~:text=Before-,Exact%20quote,-After",
  anchor: {
    quote: "Exact quote",
    prefix: "Before",
    suffix: "After",
  },
};

function installChromeMock() {
  const session = new Map<string, unknown>();
  const tabsCreate = vi.fn().mockResolvedValue({ id: 42 });
  vi.stubGlobal("chrome", {
    runtime: { id: extensionId },
    tabs: { create: tabsCreate },
    storage: {
      local: {
        get: vi.fn().mockResolvedValue({ [SETTINGS_KEY]: pairedSettings }),
      },
      session: {
        get: vi.fn(async (key: string) => ({ [key]: session.get(key) })),
        set: vi.fn(async (values: Record<string, unknown>) => {
          Object.entries(values).forEach(([key, value]) =>
            session.set(key, value),
          );
        }),
        remove: vi.fn(async (key: string) => {
          session.delete(key);
        }),
      },
    },
  });
  return { session, tabsCreate };
}

beforeEach(() => {
  installChromeMock();
});

describe("citation navigation validation", () => {
  it("accepts a bounded anchor and same-document native fragment", () => {
    expect(parseCitationNavigation(citation)).toEqual(citation);
    expect(
      parseCitationData(
        JSON.stringify({
          version: 1,
          url: citation.url,
          anchor: citation.anchor,
        }),
        citation.highlightUrl,
      ),
    ).toEqual(citation);
  });

  it("rejects unsafe schemes, credentials, and cross-document fragments", () => {
    expect(
      parseCitationNavigation({
        ...citation,
        url: "javascript:alert(1)",
      }),
    ).toBeNull();
    expect(
      parseCitationNavigation({
        ...citation,
        url: "https://user:secret@article.example.com/story",
      }),
    ).toBeNull();
    expect(
      parseCitationNavigation({
        ...citation,
        highlightUrl: "https://evil.example.com/#:~:text=Exact",
      }),
    ).toBeNull();
  });
});

describe("pending citation handoff", () => {
  it("opens one tab and lets only that target tab claim the anchor once", async () => {
    const { tabsCreate } = installChromeMock();
    const sourceSender = {
      id: extensionId,
      tab: { id: 7 },
      url: "https://newsread.example.com/history/documents/12",
    };

    await expect(openCitation(citation, sourceSender, 1_000)).resolves.toBe(
      true,
    );
    expect(tabsCreate).toHaveBeenCalledWith({
      active: true,
      url: citation.highlightUrl,
    });

    await expect(
      claimPendingCitation(
        {
          id: extensionId,
          tab: { id: 41 },
          url: "https://article.example.com/story",
        },
        1_001,
      ),
    ).resolves.toBeNull();
    const targetSender = {
      id: extensionId,
      tab: { id: 42 },
      url: "https://article.example.com/story",
    };
    await expect(
      claimPendingCitation(targetSender, 1_001),
    ).resolves.toEqual(citation.anchor);
    await expect(
      claimPendingCitation(targetSender, 1_002),
    ).resolves.toBeNull();
  });

  it("rejects opens from lookalike origins and unpaired settings", async () => {
    await expect(
      openCitation(citation, {
        id: extensionId,
        tab: { id: 7 },
        url: "https://newsread.example.com.evil.test/history",
      }),
    ).rejects.toThrow("Citation navigation rejected");

    vi.mocked(chrome.storage.local.get).mockResolvedValue({
      [SETTINGS_KEY]: { ...pairedSettings, token: "" },
    });
    await expect(
      openCitation(citation, {
        id: extensionId,
        tab: { id: 7 },
        url: "https://newsread.example.com/history",
      }),
    ).rejects.toThrow("Citation navigation rejected");
  });

  it("keeps the already-open native tab when session storage is unavailable", async () => {
    vi.mocked(chrome.storage.session.set).mockRejectedValue(
      new Error("session storage unavailable"),
    );
    await expect(
      openCitation(citation, {
        id: extensionId,
        tab: { id: 7 },
        url: "https://newsread.example.com/history",
      }),
    ).resolves.toBe(true);
    expect(chrome.tabs.create).toHaveBeenCalledOnce();
  });

  it("expires or discards an anchor on a changed target URL", async () => {
    const sourceSender = {
      id: extensionId,
      tab: { id: 7 },
      url: "https://newsread.example.com/history",
    };
    await openCitation(citation, sourceSender, 1_000);

    await expect(
      claimPendingCitation(
        {
          id: extensionId,
          tab: { id: 42 },
          url: "https://article.example.com/another-story",
        },
        1_001,
      ),
    ).resolves.toBeNull();

    await openCitation(citation, sourceSender, 1_000);
    await expect(
      claimPendingCitation(
        {
          id: extensionId,
          tab: { id: 42 },
          url: "https://article.example.com/story",
        },
        20_000,
      ),
    ).resolves.toBeNull();
  });
});
