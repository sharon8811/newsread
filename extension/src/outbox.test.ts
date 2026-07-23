import "fake-indexeddb/auto";
import { beforeEach, describe, expect, it } from "vitest";
import { DEFAULT_SETTINGS } from "./config.js";
import {
  clearQueued,
  deleteQueued,
  enqueueCapture,
  listQueued,
  readSyncBatch,
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
  text: "Visible article text",
  textExcerpt: "Visible article text",
  capturedAt: "2026-07-24T08:00:00.000Z",
};

describe("IndexedDB outbox", () => {
  beforeEach(async () => {
    await clearQueued();
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
    expect(capture?.text).toBe("");
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
});
