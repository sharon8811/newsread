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
