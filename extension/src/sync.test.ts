import { beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_SETTINGS, DEFAULT_SYNC_STATE } from "./config.js";
import type { QueuedCapture } from "./types.js";

const mocks = vi.hoisted(() => ({
  getSettings: vi.fn(),
  getSyncState: vi.fn(),
  saveSettings: vi.fn(),
  saveSyncState: vi.fn(),
  readSyncBatch: vi.fn(),
  deleteQueued: vi.fn(),
  deleteVisitAggregates: vi.fn(),
  clearConnectionData: vi.fn(),
}));

vi.mock("./settings.js", () => ({
  getSettings: mocks.getSettings,
  getSyncState: mocks.getSyncState,
  saveSettings: mocks.saveSettings,
  saveSyncState: mocks.saveSyncState,
}));

vi.mock("./outbox.js", () => ({
  readSyncBatch: mocks.readSyncBatch,
  deleteQueued: mocks.deleteQueued,
  deleteVisitAggregates: mocks.deleteVisitAggregates,
  clearConnectionData: mocks.clearConnectionData,
  toSyncRecord: (capture: QueuedCapture) => ({
    record_id: capture.record_id,
    url: capture.url,
  }),
}));

import { syncNow } from "./sync.js";

const capture: QueuedCapture = {
  urlHash: "hash",
  record_id: "record-1",
  url: "https://article.example.com/",
  title: "Article",
  text: "text",
  text_excerpt: "text",
  first_visited_at: "2026-07-24T08:00:00Z",
  last_visited_at: "2026-07-24T08:00:00Z",
  captured_at: "2026-07-24T08:00:00Z",
  visit_count: 1,
  known_revision: 0,
  contentHash: "content",
  queuedAt: 1,
};

describe("sync retry and revocation handling", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getSettings.mockResolvedValue({
      ...DEFAULT_SETTINGS,
      serverUrl: "https://news.example.com",
      token: "secret",
      connectionStatus: "paired",
    });
    mocks.getSyncState.mockResolvedValue(DEFAULT_SYNC_STATE);
    mocks.readSyncBatch.mockResolvedValue([capture]);
    mocks.saveSettings.mockResolvedValue(undefined);
    mocks.saveSyncState.mockResolvedValue(undefined);
    mocks.deleteQueued.mockResolvedValue(undefined);
  });

  it("removes acknowledged records and stores the server revision", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            accepted: [{ record_id: "record-1" }],
            rejected: [],
            sync_revision: 4,
            domain_rules: [],
          }),
          { status: 200 },
        ),
      ),
    );
    await syncNow(true);
    expect(mocks.deleteQueued).toHaveBeenCalledWith(["hash"]);
    expect(mocks.saveSettings).toHaveBeenCalledWith(
      expect.objectContaining({ knownRevision: 4, connectionStatus: "paired" }),
    );
    expect(mocks.saveSyncState).toHaveBeenCalledWith(DEFAULT_SYNC_STATE);
  });

  it("clears visit aggregates only for stale-revision rejections", async () => {
    const excluded: QueuedCapture = {
      ...capture,
      urlHash: "hash-excluded",
      record_id: "record-2",
    };
    mocks.readSyncBatch.mockResolvedValue([capture, excluded]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            accepted: [],
            rejected: [
              { record_id: "record-1", code: "stale_revision" },
              { record_id: "record-2", code: "excluded" },
            ],
            sync_revision: 5,
            domain_rules: [],
          }),
          { status: 200 },
        ),
      ),
    );
    await syncNow(true);
    expect(mocks.deleteQueued).toHaveBeenCalledWith(["hash", "hash-excluded"]);
    expect(mocks.deleteVisitAggregates).toHaveBeenCalledWith(["hash"]);
  });

  it("honors Retry-After without deleting queued work", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(null, {
          status: 429,
          headers: { "Retry-After": "120" },
        }),
      ),
    );
    await syncNow(true);
    expect(mocks.deleteQueued).not.toHaveBeenCalled();
    expect(mocks.saveSyncState).toHaveBeenCalledWith(
      expect.objectContaining({
        attempt: 1,
        lastError: "NewsRead asked the extension to slow down",
      }),
    );
  });

  it("marks a revoked connection and preserves the outbox", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 401 })));
    await syncNow(true);
    expect(mocks.saveSettings).toHaveBeenCalledWith({
      connectionStatus: "revoked",
    });
    expect(mocks.deleteQueued).not.toHaveBeenCalled();
  });
});
