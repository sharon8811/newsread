import { api, ApiError, apiPage, configureApi, sendReadBatch } from "../api";

const realFetch = globalThis.fetch;

function mockResponse(
  overrides: Omit<Partial<Response>, "headers"> & {
    jsonBody?: unknown;
    headers?: Record<string, string>;
  },
) {
  const { jsonBody, headers = {}, ...rest } = overrides;
  const lower = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => jsonBody,
    headers: { get: (name: string) => lower[name.toLowerCase()] ?? null },
    ...rest,
  } as unknown as Response;
}

describe("api", () => {
  afterEach(() => {
    globalThis.fetch = realFetch;
    configureApi({ serverUrl: null, token: null });
  });

  it("throws before a server is configured", async () => {
    await expect(api("/articles")).rejects.toThrow(/no server configured/i);
  });

  it("prefixes the server URL and /api, and sends the bearer token", async () => {
    const fetchMock = jest.fn(async () => mockResponse({ jsonBody: [] }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    configureApi({ serverUrl: "https://news.example.com", token: "tok123" });
    await api("/articles?limit=2");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://news.example.com/api/articles?limit=2");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok123");
  });

  it("omits the Authorization header without a token", async () => {
    const fetchMock = jest.fn(async () => mockResponse({ jsonBody: {} }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    configureApi({ serverUrl: "https://news.example.com" });
    await api("/health");
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it("serializes the body and method", async () => {
    const fetchMock = jest.fn(async () => mockResponse({ jsonBody: {} }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    configureApi({ serverUrl: "https://s" });
    await api("/auth/login", { method: "POST", body: { identifier: "a", password: "b" } });
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ identifier: "a", password: "b" });
  });

  it("returns undefined for 204 responses", async () => {
    globalThis.fetch = jest.fn(async () =>
      mockResponse({ status: 204, jsonBody: undefined }),
    ) as unknown as typeof fetch;
    configureApi({ serverUrl: "https://s" });
    await expect(api("/shares/1/seen", { method: "POST" })).resolves.toBeUndefined();
  });

  it("throws ApiError with the backend's string detail", async () => {
    globalThis.fetch = jest.fn(async () =>
      mockResponse({ ok: false, status: 401, jsonBody: { detail: "Invalid credentials" } }),
    ) as unknown as typeof fetch;
    configureApi({ serverUrl: "https://s" });
    const err = (await api("/auth/login", { method: "POST", body: {} }).catch(
      (e: unknown) => e,
    )) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(401);
    expect(err.message).toBe("Invalid credentials");
  });

  it("joins pydantic validation error details", async () => {
    globalThis.fetch = jest.fn(async () =>
      mockResponse({
        ok: false,
        status: 422,
        jsonBody: { detail: [{ msg: "field required" }, { msg: "too short" }] },
      }),
    ) as unknown as typeof fetch;
    configureApi({ serverUrl: "https://s" });
    await expect(api("/auth/register", { method: "POST", body: {} })).rejects.toThrow(
      "field required; too short",
    );
  });

  it("falls back to statusText for non-JSON errors", async () => {
    globalThis.fetch = jest.fn(async () =>
      mockResponse({
        ok: false,
        status: 502,
        statusText: "Bad Gateway",
        json: async () => {
          throw new Error("not json");
        },
      }),
    ) as unknown as typeof fetch;
    configureApi({ serverUrl: "https://s" });
    await expect(api("/articles")).rejects.toThrow("Bad Gateway");
  });
});

describe("apiPage", () => {
  afterEach(() => {
    globalThis.fetch = realFetch;
    configureApi({ serverUrl: null, token: null });
  });

  it("surfaces the X-Next-Cursor header", async () => {
    globalThis.fetch = jest.fn(async () =>
      mockResponse({ jsonBody: [{ id: 1 }], headers: { "X-Next-Cursor": "abc123" } }),
    ) as unknown as typeof fetch;
    configureApi({ serverUrl: "https://s" });
    await expect(apiPage("/articles")).resolves.toEqual({
      items: [{ id: 1 }],
      nextCursor: "abc123",
      prevCursor: null,
      unreadCount: null,
      newAboveCount: null,
    });
  });

  it("returns a null cursor on the last page", async () => {
    globalThis.fetch = jest.fn(async () =>
      mockResponse({ jsonBody: [] }),
    ) as unknown as typeof fetch;
    configureApi({ serverUrl: "https://s" });
    await expect(apiPage("/articles")).resolves.toEqual({
      items: [],
      nextCursor: null,
      prevCursor: null,
      unreadCount: null,
      newAboveCount: null,
    });
  });
});

describe("apiPage reading headers", () => {
  afterEach(() => {
    globalThis.fetch = realFetch;
    configureApi({ serverUrl: null, token: null });
  });

  it("surfaces prev cursor and unread counters", async () => {
    globalThis.fetch = jest.fn(async () =>
      mockResponse({
        jsonBody: [{ id: 1 }],
        headers: {
          "X-Prev-Cursor": "p1",
          "X-Unread-Count": "12",
          "X-New-Above-Count": "3",
        },
      }),
    ) as unknown as typeof fetch;
    configureApi({ serverUrl: "https://s" });
    const page = await apiPage("/articles?anchor=resume");
    expect(page.prevCursor).toBe("p1");
    expect(page.unreadCount).toBe(12);
    expect(page.newAboveCount).toBe(3);
  });
});

describe("sendReadBatch", () => {
  afterEach(() => {
    globalThis.fetch = realFetch;
    configureApi({ serverUrl: null, token: null });
  });

  it("POSTs the batch to the state endpoint", async () => {
    const fetchMock = jest.fn(async () => mockResponse({ status: 204 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    configureApi({ serverUrl: "https://s", token: "t" });
    await sendReadBatch({
      article_ids: [1, 2],
      read_source: "scrolled",
      frontier_article_id: 2,
    });
    const [url, opts] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://s/api/articles/state/batch");
    expect(JSON.parse(String(opts.body))).toEqual({
      article_ids: [1, 2],
      read_source: "scrolled",
      frontier_article_id: 2,
    });
  });
});
