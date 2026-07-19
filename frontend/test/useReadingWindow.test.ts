import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import {
  ARTICLES_REFRESH_EVENT,
  useReadingWindow,
} from "@/lib/useReadingWindow";
import {
  clearReadingSessions,
  readingSessionKey,
  setReadingReturnAnchor,
} from "@/lib/readingSession";
import { makeArticle } from "./fixtures";
import type { Article } from "@/lib/api";

const { mutateMock } = vi.hoisted(() => ({ mutateMock: vi.fn() }));
vi.mock("swr", () => ({ mutate: mutateMock }));

type Route = {
  match: (url: string, opts?: RequestInit) => boolean;
  articles?: Article[];
  headers?: Record<string, string>;
  status?: number;
  fail?: boolean;
};

function installFetch(routes: Route[]) {
  const fetchMock = vi.fn((url: string, opts?: RequestInit) => {
    const route = routes.find((r) => r.match(String(url), opts));
    if (!route) return Promise.reject(new Error(`no route for ${url}`));
    if (route.fail) return Promise.reject(new Error("network down"));
    return Promise.resolve({
      ok: true,
      status: route.status ?? 200,
      json: async () => route.articles ?? [],
      headers: new Headers(route.headers ?? {}),
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const topRoute = (articles: Article[], headers: Record<string, string> = {}): Route => ({
  match: (u) =>
    u.includes("/articles?") &&
    !u.includes("cursor=") &&
    !u.includes("direction=") &&
    !u.includes("/state/"),
  articles,
  headers,
});
const batchRoute = (overrides: Partial<Route> = {}): Route => ({
  match: (u) => u.includes("/state/batch"),
  status: 204,
  ...overrides,
});

beforeEach(() => clearReadingSessions());

describe("useReadingWindow", () => {
  beforeEach(() => {
    mutateMock.mockClear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("opens at the newest page without a resume anchor and exposes counters", async () => {
    const fetchMock = installFetch([
      topRoute([makeArticle({ id: 2 }), makeArticle({ id: 1 })], {
        "X-Unread-Count": "9",
        "X-New-Above-Count": "0",
        "X-Next-Cursor": "next",
      }),
    ]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await waitFor(() => expect(result.current.articles).toHaveLength(2));
    expect(result.current.articles?.map((article) => article.id)).toEqual([2, 1]);
    expect(result.current.unreadCount).toBe(9);
    expect(result.current.newAbove).toBe(0);
    expect(result.current.prevCursor).toBeNull();
    expect(result.current.nextCursor).toBe("next");
    expect(result.current.loading).toBe(false);
    expect(String(fetchMock.mock.calls[0][0])).not.toContain("anchor=resume");
  });

  it("keeps an opened article in the cached window across navigation", async () => {
    const fetchMock = installFetch([
      topRoute(
        [makeArticle({ id: 1, title: "Opened" }), makeArticle({ id: 2, title: "Next" })],
        { "X-Unread-Count": "2" },
      ),
    ]);
    const first = renderHook(() =>
      useReadingWindow({ filter: "unread", enabled: true }),
    );
    await waitFor(() => expect(first.result.current.articles).toHaveLength(2));

    act(() => first.result.current.markOpened(1));
    expect(first.result.current.articles?.map((article) => article.id)).toEqual([1, 2]);
    expect(first.result.current.articles?.[0].is_read).toBe(true);
    expect(first.result.current.unreadCount).toBe(1);
    setReadingReturnAnchor(readingSessionKey("unread"), { articleId: 1, offset: 0 });
    first.unmount();

    const second = renderHook(() =>
      useReadingWindow({ filter: "unread", enabled: true }),
    );
    await waitFor(() => expect(second.result.current.articles).toHaveLength(2));
    expect(second.result.current.articles?.map((article) => article.id)).toEqual([1, 2]);
    expect(second.result.current.articles?.[0].is_read).toBe(true);
    expect(second.result.current.unreadCount).toBe(1);
    expect(
      fetchMock.mock.calls.filter((call) => String(call[0]).includes("/articles?")),
    ).toHaveLength(1);
  });

  it("refetches the newest page after a non-article navigation", async () => {
    let requestCount = 0;
    const fetchMock = installFetch([
      {
        ...topRoute([]),
        get articles() {
          requestCount += 1;
          return requestCount === 1
            ? [makeArticle({ id: 1, title: "Old cached row", is_read: true })]
            : [makeArticle({ id: 2, title: "Fresh unread row" })];
        },
        headers: { "X-Unread-Count": "1" },
      } as Route,
    ]);

    const first = renderHook(() =>
      useReadingWindow({ filter: "unread", enabled: true }),
    );
    await waitFor(() => expect(first.result.current.articles?.[0].id).toBe(1));
    first.unmount();

    const second = renderHook(() =>
      useReadingWindow({ filter: "unread", enabled: true }),
    );
    await waitFor(() => expect(second.result.current.articles?.[0].id).toBe(2));
    expect(second.result.current.articles?.[0].title).toBe("Fresh unread row");
    expect(
      fetchMock.mock.calls.filter((call) => String(call[0]).includes("/articles?")),
    ).toHaveLength(2);
  });

  it("does nothing when disabled", () => {
    const fetchMock = installFetch([]);
    renderHook(() => useReadingWindow({ filter: "all", enabled: false }));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("opens feed-scoped windows at the top, without a resume anchor", async () => {
    const fetchMock = installFetch([
      {
        match: (u) => u.includes("feed_id=7") && !u.includes("anchor"),
        articles: [makeArticle({ id: 1 })],
        headers: { "X-Unread-Count": "2" },
      },
    ]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "unread", feedId: "7", enabled: true }),
    );
    await waitFor(() => expect(result.current.articles).toHaveLength(1));
    expect(result.current.unreadCount).toBe(2);
    // A resume anchor must never leak into any cold list.
    expect(
      fetchMock.mock.calls.filter((call) => String(call[0]).includes("anchor")),
    ).toHaveLength(0);
  });

  it("falls back to an empty window when the top-page request fails", async () => {
    installFetch([{ ...topRoute([]), fail: true }]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.articles).toEqual([]);
  });

  it("loadOlder prepends deduped history and advances the prev cursor", async () => {
    installFetch([
      topRoute([makeArticle({ id: 5, title: "Anchor" })], {
        "X-Prev-Cursor": "p1",
      }),
      {
        match: (u) => u.includes("direction=before"),
        articles: [
          makeArticle({ id: 3, title: "History", is_read: true }),
          makeArticle({ id: 5, title: "Anchor" }), // dupe must be dropped
        ],
        headers: { "X-Prev-Cursor": "p2" },
      },
    ]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await waitFor(() => expect(result.current.prevCursor).toBe("p1"));
    let fetched = false;
    await act(async () => {
      fetched = await result.current.loadOlder();
    });
    expect(fetched).toBe(true);
    expect(result.current.articles!.map((a) => a.id)).toEqual([3, 5]);
    expect(result.current.prevCursor).toBe("p2");
  });

  it("loadOlder resolves false without a cursor", async () => {
    installFetch([topRoute([makeArticle({ id: 1 })])]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await waitFor(() => expect(result.current.articles).toHaveLength(1));
    let fetched = true;
    await act(async () => {
      fetched = await result.current.loadOlder();
    });
    expect(fetched).toBe(false);
  });

  it("loadNewer appends the next page and tracks the next cursor", async () => {
    installFetch([
      topRoute([makeArticle({ id: 1 })], { "X-Next-Cursor": "n1" }),
      {
        match: (u) => u.includes("cursor=n1") && !u.includes("direction"),
        articles: [makeArticle({ id: 2 })],
        headers: {},
      },
    ]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await waitFor(() => expect(result.current.nextCursor).toBe("n1"));
    await act(async () => {
      await result.current.loadNewer();
    });
    expect(result.current.articles!.map((a) => a.id)).toEqual([1, 2]);
    expect(result.current.nextCursor).toBeNull();
  });

  it("resetToTop replaces the window with the plain first page", async () => {
    let requestCount = 0;
    installFetch([{
      ...topRoute([]),
      get articles() {
        requestCount += 1;
        return requestCount === 1
          ? [makeArticle({ id: 9, title: "Initial" })]
          : [makeArticle({ id: 1, title: "Top" })];
      },
      headers: { "X-Next-Cursor": "n" },
    } as Route]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await waitFor(() => expect(result.current.articles?.[0].title).toBe("Initial"));
    await act(async () => {
      await result.current.resetToTop();
    });
    expect(result.current.articles!.map((a) => a.id)).toEqual([1]);
    expect(result.current.newAbove).toBe(0);
    expect(result.current.prevCursor).toBeNull();
  });

  it("markPassed is optimistic, tracks the frontier, and flushes a batch", async () => {
    vi.useFakeTimers();
    const fetchMock = installFetch([
      {
        // Feed-scoped windows load without a resume anchor.
        match: (u) => u.includes("feed_id=7") && !u.includes("batch"),
        articles: [makeArticle({ id: 1 }), makeArticle({ id: 2 }), makeArticle({ id: 3 })],
        headers: { "X-Unread-Count": "3" },
      },
      batchRoute(),
    ]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "unread", feedId: "7", enabled: true }),
    );
    await vi.waitFor(() => expect(result.current.articles).toHaveLength(3));
    act(() => {
      result.current.markPassed(2); // deeper first
      result.current.markPassed(1); // shallower — frontier must stay at 2
      result.current.markPassed(99); // unknown id — ignored
    });
    expect(result.current.articles!.find((a) => a.id === 1)!.is_read).toBe(true);
    expect(result.current.unreadCount).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    const batch = fetchMock.mock.calls.find((c) => String(c[0]).includes("batch"))!;
    const body = JSON.parse((batch[1] as RequestInit).body as string);
    expect(new Set(body.article_ids)).toEqual(new Set([1, 2]));
    expect(body.frontier_article_id).toBe(2);
    expect(body.frontier_feed_id).toBe(7);
    expect(mutateMock).toHaveBeenCalledWith("/feeds");
  });

  it("reports only the first pass and undoes before the delayed flush", async () => {
    vi.useFakeTimers();
    const fetchMock = installFetch([
      topRoute([makeArticle({ id: 1 })], { "X-Unread-Count": "1" }),
      batchRoute(),
    ]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await vi.waitFor(() => expect(result.current.articles).toHaveLength(1));

    act(() => {
      expect(result.current.markPassed(1)).toBe(true);
      expect(result.current.markPassed(1)).toBe(false);
      expect(result.current.markPassed(99)).toBe(false);
    });
    expect(result.current.unreadCount).toBe(0);

    await act(async () => {
      await result.current.undoPassed([1, 1]);
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(result.current.articles![0].is_read).toBe(false);
    expect(result.current.unreadCount).toBe(1);
    const batches = fetchMock.mock.calls.filter((call) =>
      String(call[0]).includes("/state/batch"),
    );
    expect(batches).toHaveLength(1);
    expect(JSON.parse((batches[0][1] as RequestInit).body as string)).toMatchObject({
      article_ids: [1],
      is_read: false,
    });
  });

  it("makes undo the final write when the auto-read batch is already in flight", async () => {
    vi.useFakeTimers();
    let resolveAutoRead!: (response: {
      ok: boolean;
      status: number;
      json: () => Promise<object>;
    }) => void;
    const bodies: Record<string, unknown>[] = [];
    const fetchMock = vi.fn((url: string, opts?: RequestInit) => {
      if (String(url).includes("/state/batch")) {
        const body = JSON.parse(opts?.body as string) as Record<string, unknown>;
        bodies.push(body);
        if (body.is_read === false) {
          return Promise.resolve({ ok: true, status: 204, json: async () => ({}) });
        }
        return new Promise((resolve) => {
          resolveAutoRead = resolve;
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => [makeArticle({ id: 1 })],
        headers: new Headers({ "X-Unread-Count": "1" }),
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await vi.waitFor(() => expect(result.current.articles).toHaveLength(1));
    act(() => result.current.markPassed(1));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(bodies).toHaveLength(1);

    let undo!: Promise<void>;
    act(() => {
      undo = result.current.undoPassed([1]);
    });
    expect(result.current.articles![0].is_read).toBe(false);
    expect(bodies).toHaveLength(1);
    resolveAutoRead({ ok: true, status: 204, json: async () => ({}) });
    await act(async () => undo);
    expect(bodies).toHaveLength(2);
    expect(bodies[0].is_read).toBeUndefined();
    expect(bodies[1]).toMatchObject({ article_ids: [1], is_read: false });
  });

  it("rolls an undo back when the unread request fails", async () => {
    vi.useFakeTimers();
    const fetchMock = installFetch([
      topRoute([makeArticle({ id: 1 })], { "X-Unread-Count": "1" }),
      batchRoute({ fail: true }),
    ]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await vi.waitFor(() => expect(result.current.articles).toHaveLength(1));
    act(() => result.current.markPassed(1));
    await act(async () => {
      await expect(result.current.undoPassed([1])).rejects.toThrow("network down");
    });
    expect(result.current.articles![0].is_read).toBe(true);
    expect(result.current.unreadCount).toBe(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    const batches = fetchMock.mock.calls
      .filter((call) => String(call[0]).includes("/state/batch"))
      .map((call) => JSON.parse((call[1] as RequestInit).body as string));
    expect(batches).toHaveLength(2);
    expect(batches[1].is_read).toBeUndefined();
  });

  it("re-queues the batch when the flush fails, then retries on the next flush", async () => {
    vi.useFakeTimers();
    let failBatch = true;
    const fetchMock = installFetch([
      topRoute([makeArticle({ id: 1 }), makeArticle({ id: 2 })], {
        "X-Unread-Count": "2",
      }),
      {
        match: (u) => u.includes("batch"),
        get fail() {
          return failBatch;
        },
        status: 204,
      } as Route,
    ]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await vi.waitFor(() => expect(result.current.articles).toHaveLength(2));
    act(() => result.current.markPassed(1));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000); // flush fails, id re-queued
    });
    failBatch = false;
    act(() => result.current.markPassed(2));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    const calls = fetchMock.mock.calls.filter((c) => String(c[0]).includes("batch"));
    const lastBody = JSON.parse((calls.at(-1)![1] as RequestInit).body as string);
    expect(new Set(lastBody.article_ids)).toEqual(new Set([1, 2]));
  });

  it("reloads the newest page on the articles-refresh event", async () => {
    const fetchMock = installFetch([
      topRoute([makeArticle({ id: 1 })], { "X-Unread-Count": "1" }),
    ]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await waitFor(() => expect(result.current.articles).toHaveLength(1));
    const before = fetchMock.mock.calls.length;
    act(() => {
      window.dispatchEvent(new Event(ARTICLES_REFRESH_EVENT));
    });
    await waitFor(() =>
      expect(fetchMock.mock.calls.length).toBeGreaterThan(before),
    );
  });

  it("flushes with keepalive when the tab hides", async () => {
    vi.useFakeTimers();
    const fetchMock = installFetch([
      topRoute([makeArticle({ id: 1 })], { "X-Unread-Count": "1" }),
      batchRoute(),
    ]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await vi.waitFor(() => expect(result.current.articles).toHaveLength(1));
    act(() => result.current.markPassed(1));
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "hidden",
    });
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    const batch = fetchMock.mock.calls.find((c) => String(c[0]).includes("batch"))!;
    expect((batch[1] as RequestInit & { keepalive?: boolean }).keepalive).toBe(true);
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "visible",
    });
  });

  it("flushes on unmount", async () => {
    vi.useFakeTimers();
    const fetchMock = installFetch([
      topRoute([makeArticle({ id: 1 })], { "X-Unread-Count": "1" }),
      batchRoute(),
    ]);
    const { result, unmount } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await vi.waitFor(() => expect(result.current.articles).toHaveLength(1));
    act(() => result.current.markPassed(1));
    unmount();
    expect(
      fetchMock.mock.calls.some((c) => String(c[0]).includes("batch")),
    ).toBe(true);
  });

  it("toggleRead and toggleSaved update in place and post single states", async () => {
    const fetchMock = installFetch([
      topRoute([makeArticle({ id: 4, is_read: false, is_saved: false })], {
        "X-Unread-Count": "1",
      }),
      { match: (u) => u.includes("/articles/4/state"), articles: [] },
    ]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await waitFor(() => expect(result.current.articles).toHaveLength(1));
    await act(async () => {
      await result.current.toggleRead(result.current.articles![0]);
    });
    expect(result.current.articles![0].is_read).toBe(true);
    expect(result.current.unreadCount).toBe(0);
    await act(async () => {
      await result.current.toggleRead(result.current.articles![0]);
    });
    expect(result.current.articles![0].is_read).toBe(false);
    expect(result.current.unreadCount).toBe(1);
    await act(async () => {
      await result.current.toggleSaved(result.current.articles![0]);
    });
    expect(result.current.articles![0].is_saved).toBe(true);
    const stateCalls = fetchMock.mock.calls.filter((c) =>
      String(c[0]).includes("/articles/4/state"),
    );
    expect(stateCalls).toHaveLength(3);
  });

  it("merges fresh fields while images render, keeping local read state", async () => {
    vi.useFakeTimers();
    const pending = makeArticle({
      id: 6,
      image_pending: true,
      image_url: null,
    });
    const resolved = {
      ...pending,
      image_pending: false,
      image_url: "https://img/x.png",
    };
    let served: Article[] = [pending];
    installFetch([
      {
        match: topRoute([]).match,
        get articles() {
          return served;
        },
        headers: { "X-Unread-Count": "1" },
      } as Route,
      batchRoute(),
    ]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await vi.waitFor(() => expect(result.current.articles).toHaveLength(1));
    act(() => result.current.markPassed(6)); // local read mark must survive merges
    served = [resolved];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4500);
    });
    expect(result.current.articles![0].image_url).toBe("https://img/x.png");
    expect(result.current.articles![0].is_read).toBe(true);
  });
});

describe("useReadingWindow edges", () => {
  beforeEach(() => mutateMock.mockClear());
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("loadNewer resolves false when the page request fails", async () => {
    installFetch([
      topRoute([makeArticle({ id: 1 })], { "X-Next-Cursor": "n1" }),
      { match: (u) => u.includes("cursor=n1"), fail: true },
    ]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await waitFor(() => expect(result.current.nextCursor).toBe("n1"));
    let fetched = true;
    await act(async () => {
      fetched = await result.current.loadNewer();
    });
    expect(fetched).toBe(false);
  });

  it("concurrent loadOlder calls collapse into one request", async () => {
    let resolveBefore: (v: unknown) => void = () => {};
    const gate = new Promise((r) => (resolveBefore = r));
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (String(url).includes("direction=before")) {
        await gate;
        return {
          ok: true,
          status: 200,
          json: async () => [makeArticle({ id: 2, is_read: true })],
          headers: new Headers(),
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => [makeArticle({ id: 5 })],
        headers: new Headers({ "X-Prev-Cursor": "p1", "X-Unread-Count": "1" }),
      };
    });
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await waitFor(() => expect(result.current.prevCursor).toBe("p1"));
    let second = true;
    await act(async () => {
      const first = result.current.loadOlder();
      second = await result.current.loadOlder(); // guarded — no second fetch
      resolveBefore(null);
      await first;
    });
    expect(second).toBe(false);
    expect(
      fetchMock.mock.calls.filter((c) => String(c[0]).includes("direction=before")),
    ).toHaveLength(1);
  });

  it("does not flush while the tab stays visible", async () => {
    vi.useFakeTimers();
    const fetchMock = installFetch([
      topRoute([makeArticle({ id: 1 })], { "X-Unread-Count": "1" }),
      batchRoute(),
    ]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await vi.waitFor(() => expect(result.current.articles).toHaveLength(1));
    act(() => result.current.markPassed(1));
    act(() => {
      document.dispatchEvent(new Event("visibilitychange")); // visible
    });
    expect(
      fetchMock.mock.calls.some((c) => String(c[0]).includes("batch")),
    ).toBe(false);
  });

  it("keeps the counter null when the server sends no count header", async () => {
    installFetch([
      topRoute([makeArticle({ id: 1 })]),
      batchRoute(),
    ]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await waitFor(() => expect(result.current.articles).toHaveLength(1));
    act(() => result.current.markPassed(1));
    expect(result.current.unreadCount).toBeNull();
  });
});

describe("useReadingWindow last edges", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("markPassed skips articles that are already read", async () => {
    vi.useFakeTimers();
    const fetchMock = installFetch([
      topRoute([makeArticle({ id: 1, is_read: true })], { "X-Unread-Count": "0" }),
      batchRoute(),
    ]);
    const { result } = renderHook(() =>
      useReadingWindow({ filter: "all", enabled: true }),
    );
    await vi.waitFor(() => expect(result.current.articles).toHaveLength(1));
    act(() => result.current.markPassed(1));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("batch"))).toBe(false);
    expect(result.current.unreadCount).toBe(0);
  });

  it("drops a stale top-page response when the scope changes mid-flight", async () => {
    const resolvers: Array<() => void> = [];
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const isAll = String(url).includes("filter=all");
      return new Promise((resolve) => {
        resolvers.push(() =>
          resolve({
            ok: true,
            status: 200,
            json: async () => [makeArticle({ id: isAll ? 1 : 2, title: isAll ? "AllPage" : "UnreadPage" })],
            headers: new Headers({ "X-Unread-Count": "1" }),
          }),
        );
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const { result, rerender } = renderHook(
      ({ filter }: { filter: "all" | "unread" }) =>
        useReadingWindow({ filter, enabled: true }),
      { initialProps: { filter: "all" as "all" | "unread" } },
    );
    rerender({ filter: "unread" }); // supersedes the in-flight "all" page
    await act(async () => {
      resolvers.forEach((r) => r()); // resolve stale first, then current
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.articles).not.toBeNull());
    expect(result.current.articles![0].title).toBe("UnreadPage");
  });
});
