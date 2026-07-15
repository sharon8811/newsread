import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  discussionRefFor,
  fetchHNItem,
  fetchHNThread,
  hnHtmlToText,
  hnItemUrl,
} from "@/lib/discussions";

describe("discussion adapters", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  it("recognizes only canonical Hacker News item links", () => {
    expect(
      discussionRefFor({
        url: "https://example.com/story",
        comments_url: "http://news.ycombinator.com/item?foo=x&id=42",
      }),
    ).toEqual({
      provider: "hackernews",
      id: 42,
      canonicalUrl: "https://news.ycombinator.com/item?id=42",
    });
    expect(
      discussionRefFor({
        url: "https://example.com/story",
        comments_url: "https://evil.example/item?id=42",
      }),
    ).toBeNull();
  });

  it("recognizes HN self posts through the article URL", () => {
    expect(
      discussionRefFor({
        url: "https://news.ycombinator.com/item?id=99",
        comments_url: null,
      })?.id,
    ).toBe(99);
  });

  it("fetches live items directly with no-store", async () => {
    const fetchMock = vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({ id: 77, score: 12 }),
    } as Response);
    await expect(fetchHNItem(77, { fresh: true })).resolves.toMatchObject({ score: 12 });
    expect(fetchMock).toHaveBeenCalledWith(hnItemUrl(77), {
      cache: "no-store",
      signal: undefined,
    });
  });

  it("rejects unavailable HN items", async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => null } as Response);
    await expect(fetchHNItem(991, { fresh: true })).rejects.toThrow("unavailable");
  });

  it("builds a bounded plain-text discussion snapshot", async () => {
    const items: Record<number, object> = {
      2: { id: 2, type: "comment", by: "a", time: 10, text: "first<p>paragraph", kids: [4] },
      3: { id: 3, type: "comment", deleted: true },
      4: { id: 4, type: "comment", by: "b", text: "reply" },
    };
    vi.mocked(fetch).mockImplementation(async (input) => {
      const id = Number(String(input).match(/(\d+)\.json$/)?.[1]);
      return { ok: true, json: async () => items[id] } as Response;
    });
    const snapshot = await fetchHNThread(
      { id: 1, type: "story", descendants: 3, kids: [2, 3] },
      3,
    );
    expect(snapshot.included_total).toBe(3);
    expect(snapshot.reported_total).toBe(3);
    expect(snapshot.comments.map((comment) => comment.id)).toEqual([2, 4, 3]);
    expect(snapshot.comments[1]).toMatchObject({ parent_id: 2, depth: 1 });
    expect(snapshot.comments[0].text).toContain("first");
    expect(snapshot.comments[2].deleted).toBe(true);
  });

  it("preserves HN sibling order even when fetches resolve out of order", async () => {
    // Fresh ids — earlier tests populate the module-level HN item cache.
    const items: Record<number, object> = {
      102: { id: 102, by: "a", text: "slow first sibling", kids: [105] },
      103: { id: 103, by: "b", text: "fast second sibling" },
      105: { id: 105, by: "c", text: "child of the slow one" },
    };
    vi.mocked(fetch).mockImplementation(async (input) => {
      const id = Number(String(input).match(/(\d+)\.json$/)?.[1]);
      // First sibling resolves last; the pool must not let completion order
      // leak into display order.
      if (id === 102) await new Promise((resolve) => setTimeout(resolve, 15));
      return { ok: true, json: async () => items[id] } as Response;
    });
    const snapshot = await fetchHNThread({ id: 101, descendants: 3, kids: [102, 103] }, 10);
    expect(snapshot.comments.map((comment) => comment.id)).toEqual([102, 105, 103]);
  });

  it("propagates an abort instead of returning a partial thread", async () => {
    const controller = new AbortController();
    vi.mocked(fetch).mockImplementation(async () => {
      controller.abort();
      throw new DOMException("aborted", "AbortError");
    });
    await expect(
      fetchHNThread({ id: 201, descendants: 1, kids: [202] }, 5, controller.signal),
    ).rejects.toThrow("aborted");
  });

  it("converts HN markup to safe plain text", () => {
    expect(hnHtmlToText("hello<p><b>world</b> &amp; friends")).toContain("world & friends");
  });

  it("skips one unavailable comment without losing the thread", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const id = Number(String(input).match(/(\d+)\.json$/)?.[1]);
      return {
        ok: true,
        json: async () => (id === 10 ? null : { id, by: "reader", text: "visible" }),
      } as Response;
    });
    const snapshot = await fetchHNThread({ id: 9, descendants: 2, kids: [10, 11] }, 2);
    expect(snapshot.comments.map((comment) => comment.id)).toEqual([11]);
    expect(snapshot.reported_total).toBe(2);
  });
});
