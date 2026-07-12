import {
  discussionRefFor,
  fetchHNItem,
  fetchHNThread,
  hnHtmlToText,
} from "../discussions";

describe("Hacker News discussions", () => {
  beforeEach(() => {
    globalThis.fetch = jest.fn();
  });

  it("matches structured and self-post HN item URLs", () => {
    expect(
      discussionRefFor({
        url: "https://example.com/story",
        comments_url: "http://news.ycombinator.com/item?x=1&id=42",
      })?.id,
    ).toBe(42);
    expect(
      discussionRefFor({
        url: "https://news.ycombinator.com/item?id=77",
        comments_url: null,
      })?.id,
    ).toBe(77);
    expect(
      discussionRefFor({
        url: "https://example.com",
        comments_url: "https://evil.example/item?id=42",
      }),
    ).toBeNull();
  });

  it("fetches story metadata directly from the device", async () => {
    (globalThis.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({ id: 55, score: 8, descendants: 3 }),
    });
    await expect(fetchHNItem(55, { fresh: true })).resolves.toMatchObject({ score: 8 });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "https://hacker-news.firebaseio.com/v0/item/55.json",
      { cache: "no-store", signal: undefined },
    );
  });

  it("builds a bounded thread snapshot with reply structure", async () => {
    const items: Record<number, object> = {
      2: { id: 2, by: "first", text: "hello<p>world", kids: [4] },
      3: { id: 3, deleted: true },
      4: { id: 4, by: "reply", text: "nested" },
    };
    (globalThis.fetch as jest.Mock).mockImplementation(async (input: string) => {
      const id = Number(input.match(/(\d+)\.json$/)?.[1]);
      return { ok: true, json: async () => items[id] };
    });
    const snapshot = await fetchHNThread({ id: 1, kids: [2, 3], descendants: 3 }, 3);
    expect(snapshot.comments.map((comment) => comment.id)).toEqual([2, 4, 3]);
    expect(snapshot.comments[1]).toMatchObject({ parent_id: 2, depth: 1 });
    expect(snapshot.included_total).toBe(3);
  });

  it("strips HN HTML before rendering or sending it to the agent", () => {
    expect(hnHtmlToText("hello<p><b>world</b> &amp; friends")).toContain("world & friends");
  });

  it("skips one unavailable comment without losing the thread", async () => {
    (globalThis.fetch as jest.Mock).mockImplementation(async (input: string) => {
      const id = Number(input.match(/(\d+)\.json$/)?.[1]);
      return {
        ok: true,
        json: async () => (id === 10 ? null : { id, by: "reader", text: "visible" }),
      };
    });
    const snapshot = await fetchHNThread({ id: 9, descendants: 2, kids: [10, 11] }, 2);
    expect(snapshot.comments.map((comment) => comment.id)).toEqual([11]);
  });
});
