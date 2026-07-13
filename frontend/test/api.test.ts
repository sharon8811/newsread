import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  api,
  fetcher,
  getToken,
  setToken,
  ApiError,
  streamQA,
} from "@/lib/api";

function jsonResponse(body: unknown, status = 200) {
  return {
    status,
    ok: status >= 200 && status < 300,
    statusText: "Status",
    json: async () => body,
  } as Response;
}

describe("token storage", () => {
  it("stores and reads the token", () => {
    expect(getToken()).toBeNull();
    setToken("abc");
    expect(getToken()).toBe("abc");
  });

  it("clears the token on null", () => {
    setToken("abc");
    setToken(null);
    expect(getToken()).toBeNull();
  });
});

describe("api()", () => {
  beforeEach(() => {
    setToken(null);
  });

  it("performs a GET and returns json", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: 1 }));
    vi.stubGlobal("fetch", fetchMock);
    const data = await api<{ ok: number }>("/feeds");
    expect(data).toEqual({ ok: 1 });
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/feeds");
    expect(opts.method).toBe("GET");
  });

  it("attaches the auth header when a token exists", async () => {
    setToken("tok123");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}));
    vi.stubGlobal("fetch", fetchMock);
    await api("/auth/me");
    const opts = fetchMock.mock.calls[0][1];
    expect(opts.headers.Authorization).toBe("Bearer tok123");
  });

  it("sends a JSON body for POST", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}));
    vi.stubGlobal("fetch", fetchMock);
    await api("/feeds", { method: "POST", body: { url: "x" } });
    const opts = fetchMock.mock.calls[0][1];
    expect(opts.method).toBe("POST");
    expect(opts.body).toBe(JSON.stringify({ url: "x" }));
  });

  it("returns undefined for 204 responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(null, 204)));
    const data = await api("/articles/1/state", { method: "POST" });
    expect(data).toBeUndefined();
  });

  it("throws ApiError with string detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: "Nope" }, 400)),
    );
    await expect(api("/x")).rejects.toMatchObject({
      message: "Nope",
      status: 400,
    });
  });

  it("joins array validation details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ detail: [{ msg: "a" }, { msg: "b" }] }, 422),
      ),
    );
    await expect(api("/x")).rejects.toThrow("a; b");
  });

  it("falls back to statusText when detail is absent", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 500,
        ok: false,
        statusText: "Server Error",
        json: async () => {
          throw new Error("no body");
        },
      } as unknown as Response),
    );
    await expect(api("/x")).rejects.toThrow("Server Error");
  });

  it("fetcher delegates to api", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ v: 2 })));
    expect(await fetcher<{ v: number }>("/x")).toEqual({ v: 2 });
  });
});

describe("ApiError", () => {
  it("carries the status", () => {
    const e = new ApiError("boom", 503);
    expect(e.status).toBe(503);
    expect(e.message).toBe("boom");
    expect(e).toBeInstanceOf(Error);
  });
});

// Build a fake streaming Response body from a list of chunk strings.
function streamResponse(chunks: string[], { ok = true } = {}) {
  const encoder = new TextEncoder();
  let i = 0;
  return {
    ok,
    status: ok ? 200 : 502,
    statusText: "x",
    body: {
      getReader() {
        return {
          read: async () => {
            if (i < chunks.length) {
              return { done: false, value: encoder.encode(chunks[i++]) };
            }
            return { done: true, value: undefined };
          },
        };
      },
    },
    json: async () => ({ detail: "stream error" }),
  } as unknown as Response;
}

describe("streamQA", () => {
  it("parses SSE frames and calls onEvent", async () => {
    const frames = [
      'data: {"type":"delta","text":"Hel"}\n\n',
      'data: {"type":"delta","text":"lo"}\n\ndata: {"type":"done","message":{"id":1}}\n\n',
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse(frames)));
    const events: string[] = [];
    await streamQA(1, "q", (e) => events.push(e.type));
    expect(events).toEqual(["delta", "delta", "done"]);
  });

  it("splits frames that arrive across chunk boundaries", async () => {
    const frames = ['data: {"type":"stat', 'us","state":"thinking"}\n\n'];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse(frames)));
    const events: unknown[] = [];
    await streamQA(1, "q", (e) => events.push(e));
    expect(events).toHaveLength(1);
  });

  it("throws on an error event", async () => {
    const frames = ['data: {"type":"error","detail":"boom"}\n\n'];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse(frames)));
    await expect(streamQA(1, "q", () => {})).rejects.toThrow("boom");
  });

  it("throws ApiError when the response is not ok", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(streamResponse([], { ok: false })),
    );
    await expect(streamQA(1, "q", () => {})).rejects.toBeInstanceOf(ApiError);
  });

  it("includes the auth token in the request", async () => {
    setToken("tok");
    const fetchMock = vi.fn().mockResolvedValue(streamResponse([]));
    vi.stubGlobal("fetch", fetchMock);
    await streamQA(5, "hi", () => {});
    const opts = fetchMock.mock.calls[0][1];
    expect(opts.headers.Authorization).toBe("Bearer tok");
    expect(fetchMock.mock.calls[0][0]).toContain("/articles/5/qa/stream");
  });
});

describe("imageSrc", () => {
  it("prefixes relative generated-image paths with the API base", async () => {
    const { imageSrc, API_URL } = await import("@/lib/api");
    expect(imageSrc("/api/articles/32/generated-image")).toBe(
      `${API_URL}/api/articles/32/generated-image`,
    );
  });

  it("leaves absolute og-image URLs and empty values alone", async () => {
    const { imageSrc } = await import("@/lib/api");
    expect(imageSrc("https://site.example/og.png")).toBe("https://site.example/og.png");
    expect(imageSrc(null)).toBeUndefined();
    expect(imageSrc("")).toBeUndefined();
  });
});

describe("apiWithHeaders()", () => {
  beforeEach(() => setToken(null));

  it("returns data plus headers, sending the auth header when a token exists", async () => {
    setToken("tok-1");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [{ id: 1 }],
      headers: new Headers({ "X-Unread-Count": "4" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const { apiWithHeaders } = await import("@/lib/api");
    const page = await apiWithHeaders("/articles?anchor=resume");
    expect(page.data).toEqual([{ id: 1 }]);
    expect(page.headers.get("X-Unread-Count")).toBe("4");
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer tok-1");
    setToken(null);
  });

  it("omits the auth header without a token", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [],
      headers: new Headers(),
    });
    vi.stubGlobal("fetch", fetchMock);
    const { apiWithHeaders } = await import("@/lib/api");
    await apiWithHeaders("/articles");
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBeUndefined();
  });

  it("throws ApiError with the backend detail string", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        statusText: "Unprocessable",
        json: async () => ({ detail: "anchor cannot be combined with cursor or q" }),
        headers: new Headers(),
      }),
    );
    const { apiWithHeaders } = await import("@/lib/api");
    await expect(apiWithHeaders("/articles")).rejects.toMatchObject({
      status: 422,
      message: "anchor cannot be combined with cursor or q",
    });
  });

  it("falls back to statusText when the error body is not a detail string", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        statusText: "Server Error",
        json: async () => {
          throw new Error("not json");
        },
        headers: new Headers(),
      }),
    );
    const { apiWithHeaders } = await import("@/lib/api");
    await expect(apiWithHeaders("/articles")).rejects.toMatchObject({
      status: 500,
      message: "Server Error",
    });
  });
});

describe("sendReadBatch()", () => {
  beforeEach(() => setToken(null));

  it("POSTs the batch without keepalive by default, with auth when present", async () => {
    setToken("tok-2");
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    vi.stubGlobal("fetch", fetchMock);
    const { sendReadBatch } = await import("@/lib/api");
    await sendReadBatch({ article_ids: [1, 2], read_source: "scrolled" });
    const [url, opts] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/articles/state/batch");
    expect(opts.keepalive).toBe(false);
    expect(opts.headers.Authorization).toBe("Bearer tok-2");
    expect(JSON.parse(opts.body)).toEqual({ article_ids: [1, 2], read_source: "scrolled" });
    setToken(null);
  });

  it("sets keepalive for final flushes and works without a token", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    vi.stubGlobal("fetch", fetchMock);
    const { sendReadBatch } = await import("@/lib/api");
    await sendReadBatch({ article_ids: [3] }, { keepalive: true });
    const [, opts] = fetchMock.mock.calls[0];
    expect(opts.keepalive).toBe(true);
    expect(opts.headers.Authorization).toBeUndefined();
  });
});
