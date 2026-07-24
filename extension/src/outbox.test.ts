import "fake-indexeddb/auto";
import { beforeEach, describe, expect, it } from "vitest";
import { DEFAULT_SETTINGS } from "./config.js";
import {
  applyServerPolicies,
  clearConnectionData,
  clearQueued,
  deleteQueued,
  deleteVisitAggregates,
  downgradeFailedObject,
  enqueueCapture,
  listQueued,
  contentForBatch,
  purgeDomains,
  readSyncBatch,
  toSyncRecord,
} from "./outbox.js";
import type { CaptureCandidate, ExtensionSettings } from "./types.js";

const settings: ExtensionSettings = {
  ...DEFAULT_SETTINGS,
  serverUrl: "https://newsread.example.com",
  token: "nrh_test.secret",
  connectionStatus: "paired",
};

const candidate: CaptureCandidate = {
  url: "https://article.example.com/story?utm_source=test",
  title: "A useful article",
  document: {
    schema_version: 1,
    extraction_version: "history-dom-v2",
    content_type: "article",
    language: "en",
    blocks: [
      {
        id: "b0001",
        kind: "paragraph",
        text: "Visible article text",
      },
    ],
  },
  leadImage: null,
  favicon: null,
  textExcerpt: "Visible article text",
  capturedAt: "2026-07-24T08:00:00.000Z",
};

describe("IndexedDB outbox", () => {
  beforeEach(async () => {
    await clearConnectionData();
  });

  it("queues normalized captures and deduplicates revisits by URL", async () => {
    expect(await enqueueCapture(candidate, settings)).toBe(true);
    expect(
      await enqueueCapture(
        { ...candidate, capturedAt: "2026-07-24T09:00:00.000Z" },
        settings,
      ),
    ).toBe(true);
    const [capture] = await listQueued();
    expect(capture?.url).toBe("https://article.example.com/story");
    expect(capture?.visit_count).toBe(2);
    expect(capture?.first_visited_at).toBe("2026-07-24T08:00:00.000Z");
    expect(capture?.last_visited_at).toBe("2026-07-24T09:00:00.000Z");

    await deleteQueued([capture!.urlHash]);
    await enqueueCapture(
      { ...candidate, capturedAt: "2026-07-24T10:00:00.000Z" },
      settings,
    );
    const [revisit] = await listQueued();
    expect(revisit?.visit_count).toBe(3);
    expect(revisit?.first_visited_at).toBe("2026-07-24T08:00:00.000Z");
  });

  it("enforces pause, NewsRead-host exclusion, and metadata-only mode", async () => {
    expect(
      await enqueueCapture(candidate, { ...settings, paused: true }),
    ).toBe(false);
    expect(
      await enqueueCapture(
        { ...candidate, url: "https://newsread.example.com/history" },
        settings,
      ),
    ).toBe(false);
    expect(
      await enqueueCapture(candidate, {
        ...settings,
        captureMode: "metadata_only",
      }),
    ).toBe(true);
    const [capture] = await listQueued();
    expect(capture?.legacy_text).toBe("");
    expect(capture?.captured_at).toBeNull();
  });

  it("keeps metadata when a short or not-yet-rendered page has no document", async () => {
    expect(
      await enqueueCapture(
        {
          ...candidate,
          document: null,
          textExcerpt: "",
        },
        settings,
      ),
    ).toBe(true);

    const [capture] = await listQueued();
    expect(capture?.title).toBe("A useful article");
    expect(capture?.legacy_text).toBe("");
    expect(capture?.content_hash).toBeNull();
    expect(capture?.captured_at).toBeNull();
  });

  it("applies extension and synchronized server exclusions before queueing", async () => {
    expect(
      await enqueueCapture(candidate, {
        ...settings,
        excludedDomains: ["example.com"],
      }),
    ).toBe(false);
    expect(
      await enqueueCapture(candidate, {
        ...settings,
        domainRules: [
          {
            id: 1,
            hostname: "article.example.com",
            match_subdomains: false,
            mode: "exclude",
          },
        ],
      }),
    ).toBe(false);
  });

  it("builds a bounded API-shaped batch", async () => {
    await enqueueCapture(candidate, settings);
    const batch = await readSyncBatch();
    expect(batch).toHaveLength(1);
    expect(batch[0]?.known_revision).toBe(0);
    expect(batch[0]?.record_id).toBeTruthy();
    expect(batch[0]?.urlHash).toHaveLength(64);
  });

  it("deduplicates v2 objects and never sends their body inline", async () => {
    const v2Settings = { ...settings, contentCapabilityRevision: 2 };
    await enqueueCapture(candidate, v2Settings);
    await enqueueCapture(
      {
        ...candidate,
        url: "https://mirror.example.net/same-story",
      },
      v2Settings,
    );

    const batch = await readSyncBatch();
    const content = await contentForBatch(batch);
    expect(content.documents).toHaveLength(1);
    expect(new Set(batch.map((item) => item.content_hash)).size).toBe(1);
    for (const capture of batch) {
      const record = toSyncRecord(capture, 2);
      expect(record.text).toBe("");
      expect(record.content_hash).toMatch(/^[0-9a-f]{64}$/);
    }
  });

  it("drops locally malformed or oversized image candidates", async () => {
    await enqueueCapture(
      {
        ...candidate,
        leadImage: {
          bytesBase64: btoa("not-a-real-raster"),
          contentType: "image/webp",
          width: 641,
          height: 10,
        },
      },
      { ...settings, contentCapabilityRevision: 2 },
    );

    expect((await listQueued())[0]?.lead_image_hash).toBeNull();
  });

  it("downgrades every capture that references a permanently rejected object", async () => {
    await enqueueCapture(
      {
        ...candidate,
        leadImage: {
          bytesBase64: btoa("lead-image"),
          contentType: "image/webp",
          width: 100,
          height: 80,
        },
        favicon: {
          bytesBase64: btoa("favicon"),
          contentType: "image/png",
          width: 32,
          height: 32,
        },
      },
      { ...settings, contentCapabilityRevision: 2 },
    );
    let batch = await readSyncBatch();
    const original = batch[0]!;
    expect(original.content_hash).not.toBeNull();
    expect(original.lead_image_hash).not.toBeNull();
    expect(original.favicon_image_hash).not.toBeNull();

    batch = await downgradeFailedObject(
      batch,
      "content",
      original.content_hash!,
    );
    const documentDowngrade = batch[0]!;
    expect(documentDowngrade.content_hash).toBeNull();
    expect(documentDowngrade.lead_image_hash).toBeNull();
    expect(documentDowngrade.favicon_image_hash).toBe(
      original.favicon_image_hash,
    );
    expect(documentDowngrade.legacy_text).toBe("");
    expect(documentDowngrade.captured_at).toBeNull();
    expect(toSyncRecord(documentDowngrade, 2).favicon_image_hash).toBe(
      original.favicon_image_hash,
    );
    expect((await contentForBatch(batch)).documents).toHaveLength(0);
    expect((await contentForBatch(batch)).images).toHaveLength(1);

    batch = await downgradeFailedObject(
      batch,
      "images",
      original.favicon_image_hash!,
    );
    expect(batch[0]?.favicon_image_hash).toBeNull();
    expect((await contentForBatch(batch)).images).toHaveLength(0);
  });

  it("purges built-in exclusions and strips content for metadata-only rules", async () => {
    const v2Settings = { ...settings, contentCapabilityRevision: 2 };
    await enqueueCapture(candidate, v2Settings);
    await enqueueCapture(
      {
        ...candidate,
        url: "https://www.google.com/search?q=private",
      },
      {
        ...v2Settings,
        systemRules: v2Settings.systemRules.map((rule) => ({
          ...rule,
          enabled: false,
        })),
      },
    );
    const batch = await readSyncBatch();

    const remaining = await applyServerPolicies(
      batch,
      [
        {
          id: 1,
          hostname: "article.example.com",
          match_subdomains: false,
          mode: "metadata_only",
        },
      ],
      settings.systemRules,
    );

    expect(remaining).toHaveLength(1);
    expect(remaining[0]?.url).toContain("article.example.com");
    expect(remaining[0]?.content_hash).toBeNull();
    expect(remaining[0]?.captured_at).toBeNull();
  });

  it("purges queued captures and visit counts for an excluded domain", async () => {
    await clearConnectionData();
    const other = { ...candidate, url: "https://other.example.net/story" };
    await enqueueCapture(candidate, settings);
    await enqueueCapture(other, settings);

    await purgeDomains(["example.com"]);
    const remaining = await readSyncBatch();
    expect(remaining.map((capture) => new URL(capture.url).hostname)).toEqual([
      "other.example.net",
    ]);

    // The purged domain restarts from scratch: no inherited visit count.
    await enqueueCapture(candidate, settings);
    const requeued = (await listQueued()).find((capture) =>
      capture.url.includes("article.example.com"),
    );
    expect(requeued?.visit_count).toBe(1);
  });

  it("forgets visit history for server-deleted pages", async () => {
    await clearConnectionData();
    await enqueueCapture(candidate, settings);
    await enqueueCapture(candidate, settings);
    const [queued] = await listQueued();
    expect(queued?.visit_count).toBe(2);

    // Simulate the sync layer reacting to a stale_revision rejection.
    await deleteQueued([queued.urlHash]);
    await deleteVisitAggregates([queued.urlHash]);

    await enqueueCapture(candidate, settings);
    const [fresh] = await listQueued();
    expect(fresh?.visit_count).toBe(1);
    expect(fresh?.record_id).not.toBe(queued.record_id);
  });
});
