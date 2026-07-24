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
  downgradeFailedObject: vi.fn(),
  clearConnectionData: vi.fn(),
  applyServerPolicies: vi.fn(),
  contentForBatch: vi.fn(),
  deleteUnreferencedObjects: vi.fn(),
  markContentUploaded: vi.fn(),
  imageBytes: vi.fn(),
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
  downgradeFailedObject: mocks.downgradeFailedObject,
  clearConnectionData: mocks.clearConnectionData,
  applyServerPolicies: mocks.applyServerPolicies,
  contentForBatch: mocks.contentForBatch,
  deleteUnreferencedObjects: mocks.deleteUnreferencedObjects,
  markContentUploaded: mocks.markContentUploaded,
  imageBytes: mocks.imageBytes,
  toSyncRecord: (capture: QueuedCapture) => ({
    record_id: capture.record_id,
    url: capture.url,
    text: capture.content_hash ? "" : capture.legacy_text,
    content_hash: capture.content_hash ?? undefined,
  }),
}));

import { checkConnection, syncNow } from "./sync.js";

const capture: QueuedCapture = {
  urlHash: "hash",
  record_id: "record-1",
  url: "https://article.example.com/",
  title: "Article",
  legacy_text: "text",
  legacy_text_excerpt: "text",
  content_hash: null,
  lead_image_hash: null,
  favicon_image_hash: null,
  first_visited_at: "2026-07-24T08:00:00Z",
  last_visited_at: "2026-07-24T08:00:00Z",
  captured_at: "2026-07-24T08:00:00Z",
  visit_count: 1,
  known_revision: 0,
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
    mocks.saveSettings.mockImplementation(async (patch) => ({
      ...DEFAULT_SETTINGS,
      serverUrl: "https://news.example.com",
      token: "secret",
      ...patch,
    }));
    mocks.saveSyncState.mockResolvedValue(undefined);
    mocks.deleteQueued.mockResolvedValue(undefined);
    mocks.downgradeFailedObject.mockImplementation(
      async (batch: QueuedCapture[], storeName: string, hash: string) =>
        batch.map((item) =>
          storeName === "content" && item.content_hash === hash
            ? {
                ...item,
                legacy_text: "",
                legacy_text_excerpt: "",
                content_hash: null,
                lead_image_hash: null,
                captured_at: null,
              }
            : item,
        ),
    );
    mocks.applyServerPolicies.mockImplementation(async (batch) => batch);
    mocks.contentForBatch.mockResolvedValue({ documents: [], images: [] });
    mocks.deleteUnreferencedObjects.mockResolvedValue(undefined);
    mocks.markContentUploaded.mockResolvedValue(undefined);
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
            system_policy_revision: 1,
            system_rules: DEFAULT_SETTINGS.systemRules,
            content_capability_revision: 0,
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

  it("clears queued private data when pairing switches tokens", async () => {
    mocks.getSettings.mockResolvedValue({
      ...DEFAULT_SETTINGS,
      serverUrl: "https://news.example.com",
      token: "old-secret",
      connectionId: null,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            connection: { id: 9, name: "New Chrome" },
            user_name: "New owner",
            settings: { sync_revision: 0 },
            domain_rules: [],
            system_policy_revision: 1,
            system_rules: DEFAULT_SETTINGS.systemRules,
            content_capability_revision: 0,
          }),
          { status: 200 },
        ),
      ),
    );

    await checkConnection("https://news.example.com", "new-secret");

    expect(mocks.clearConnectionData).toHaveBeenCalledOnce();
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
            system_policy_revision: 1,
            system_rules: DEFAULT_SETTINGS.systemRules,
            content_capability_revision: 0,
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

  it("checks policy, uploads missing v2 content, then syncs metadata", async () => {
    const v2Capture: QueuedCapture = {
      ...capture,
      content_hash: "a".repeat(64),
      legacy_text: "",
      legacy_text_excerpt: "",
    };
    mocks.getSettings.mockResolvedValue({
      ...DEFAULT_SETTINGS,
      serverUrl: "https://news.example.com",
      token: "secret",
      connectionStatus: "paired",
      contentCapabilityRevision: 2,
    });
    mocks.readSyncBatch.mockResolvedValue([v2Capture]);
    mocks.contentForBatch.mockResolvedValue({
      documents: [
        {
          contentHash: "a".repeat(64),
          canonicalJson: '{"schema_version":1}',
          uploadState: "pending",
          lastError: "",
          updatedAt: 1,
        },
      ],
      images: [],
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            documents: { ["a".repeat(64)]: false },
            images: {},
            sync_revision: 2,
            domain_rules: [],
            system_policy_revision: 1,
            system_rules: DEFAULT_SETTINGS.systemRules,
            content_capability_revision: 2,
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            accepted: [{ record_id: "record-1" }],
            rejected: [],
            sync_revision: 2,
            domain_rules: [],
            system_policy_revision: 1,
            system_rules: DEFAULT_SETTINGS.systemRules,
            content_capability_revision: 2,
          }),
          { status: 200 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    await syncNow(true);

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      "https://news.example.com/api/history/sync/content-status",
      `https://news.example.com/api/history/sync/content/${"a".repeat(64)}`,
      "https://news.example.com/api/history/sync",
    ]);
    expect(mocks.markContentUploaded).toHaveBeenCalledWith(
      "content",
      "a".repeat(64),
    );
  });

  it("downgrades a permanently rejected object and keeps draining the batch", async () => {
    const poisoned: QueuedCapture = {
      ...capture,
      content_hash: "a".repeat(64),
      legacy_text: "",
      legacy_text_excerpt: "",
    };
    const following: QueuedCapture = {
      ...capture,
      urlHash: "following-hash",
      record_id: "record-2",
      url: "https://following.example.com/",
    };
    mocks.getSettings.mockResolvedValue({
      ...DEFAULT_SETTINGS,
      serverUrl: "https://news.example.com",
      token: "secret",
      connectionStatus: "paired",
      contentCapabilityRevision: 2,
    });
    mocks.readSyncBatch.mockResolvedValue([poisoned, following]);
    const queuedDocument = {
      contentHash: "a".repeat(64),
      canonicalJson: '{"schema_version":1}',
      uploadState: "pending",
      lastError: "",
      updatedAt: 1,
    };
    mocks.contentForBatch
      .mockResolvedValueOnce({ documents: [queuedDocument], images: [] })
      .mockResolvedValueOnce({ documents: [queuedDocument], images: [] })
      .mockResolvedValueOnce({ documents: [], images: [] });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            documents: { ["a".repeat(64)]: false },
            images: {},
            sync_revision: 2,
            domain_rules: [],
            system_policy_revision: 1,
            system_rules: DEFAULT_SETTINGS.systemRules,
            content_capability_revision: 2,
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 422 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            accepted: [
              { record_id: "record-1" },
              { record_id: "record-2" },
            ],
            rejected: [],
            sync_revision: 2,
            domain_rules: [],
            system_policy_revision: 1,
            system_rules: DEFAULT_SETTINGS.systemRules,
            content_capability_revision: 2,
          }),
          { status: 200 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    await syncNow(true);

    expect(mocks.downgradeFailedObject).toHaveBeenCalledWith(
      [poisoned, following],
      "content",
      "a".repeat(64),
    );
    const metadataRequest = JSON.parse(
      String((fetchMock.mock.calls[2]?.[1] as RequestInit).body),
    );
    expect(metadataRequest.records).toEqual([
      expect.objectContaining({
        record_id: "record-1",
        text: "",
      }),
      expect.objectContaining({ record_id: "record-2" }),
    ]);
    expect(metadataRequest.records[0].content_hash).toBeUndefined();
    expect(mocks.deleteQueued).toHaveBeenCalledWith([
      "hash",
      "following-hash",
    ]);
    expect(mocks.saveSettings).not.toHaveBeenCalledWith({
      connectionStatus: "error",
    });
  });

  it("removes a permanently rejected image without dropping page metadata", async () => {
    const imageHash = "b".repeat(64);
    const withImage: QueuedCapture = {
      ...capture,
      favicon_image_hash: imageHash,
      legacy_text: "",
      legacy_text_excerpt: "",
    };
    const queuedImage = {
      imageHash,
      bytesBase64: btoa("invalid-image"),
      contentType: "image/png",
      uploadState: "pending",
      lastError: "",
      updatedAt: 1,
    };
    mocks.getSettings.mockResolvedValue({
      ...DEFAULT_SETTINGS,
      serverUrl: "https://news.example.com",
      token: "secret",
      connectionStatus: "paired",
      contentCapabilityRevision: 2,
    });
    mocks.readSyncBatch.mockResolvedValue([withImage]);
    mocks.contentForBatch.mockResolvedValue({
      documents: [],
      images: [queuedImage],
    });
    mocks.imageBytes.mockReturnValue(new TextEncoder().encode("invalid-image"));
    mocks.downgradeFailedObject.mockResolvedValue([
      { ...withImage, favicon_image_hash: null },
    ]);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            documents: {},
            images: { [imageHash]: false },
            sync_revision: 2,
            domain_rules: [],
            system_policy_revision: 1,
            system_rules: DEFAULT_SETTINGS.systemRules,
            content_capability_revision: 2,
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 415 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            accepted: [{ record_id: "record-1" }],
            rejected: [],
            sync_revision: 2,
            domain_rules: [],
            system_policy_revision: 1,
            system_rules: DEFAULT_SETTINGS.systemRules,
            content_capability_revision: 2,
          }),
          { status: 200 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    await syncNow(true);

    expect(mocks.downgradeFailedObject).toHaveBeenCalledWith(
      [withImage],
      "images",
      imageHash,
    );
    expect(mocks.deleteQueued).toHaveBeenCalledWith(["hash"]);
  });
});
