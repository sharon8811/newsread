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
