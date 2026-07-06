import { normalizeServerUrl, probeServer } from "../server";

describe("normalizeServerUrl", () => {
  it("adds https:// when the scheme is missing", () => {
    expect(normalizeServerUrl("news.example.com")).toBe("https://news.example.com");
  });

  it("keeps an explicit http scheme (LAN installs)", () => {
    expect(normalizeServerUrl("http://192.168.1.10:8000")).toBe("http://192.168.1.10:8000");
  });

  it("strips trailing slashes and a trailing /api", () => {
    expect(normalizeServerUrl("https://news.example.com/")).toBe("https://news.example.com");
    expect(normalizeServerUrl("https://news.example.com/api/")).toBe("https://news.example.com");
    expect(normalizeServerUrl("news.example.com/api")).toBe("https://news.example.com");
  });

  it("preserves a base path", () => {
    expect(normalizeServerUrl("https://example.com/newsread")).toBe("https://example.com/newsread");
  });

  it("trims whitespace", () => {
    expect(normalizeServerUrl("  news.example.com  ")).toBe("https://news.example.com");
  });

  it("rejects empty input", () => {
    expect(() => normalizeServerUrl("   ")).toThrow(/enter/i);
  });

  it("rejects non-http schemes", () => {
    expect(() => normalizeServerUrl("ftp://example.com")).toThrow(/http/);
  });

  it("rejects unparseable input", () => {
    expect(() => normalizeServerUrl("https://")).toThrow(/valid URL/);
  });
});

describe("probeServer", () => {
  const realFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  const mockFetch = (impl: () => Promise<Partial<Response>>) => {
    globalThis.fetch = jest.fn(impl) as unknown as typeof fetch;
  };

  it("returns server info for a NewsRead server", async () => {
    const info = { status: "ok", app: "newsread", version: "0.1.0", min_client_version: "0.1.0" };
    mockFetch(async () => ({ ok: true, json: async () => info }));
    await expect(probeServer("https://news.example.com")).resolves.toEqual(info);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "https://news.example.com/api/health",
      expect.anything(),
    );
  });

  it("rejects a reachable URL that is not NewsRead", async () => {
    mockFetch(async () => ({ ok: true, json: async () => ({ hello: "world" }) }));
    await expect(probeServer("https://example.com")).rejects.toThrow(/NewsRead server/);
  });

  it("rejects non-JSON responses", async () => {
    mockFetch(async () => ({
      ok: true,
      json: async () => {
        throw new Error("not json");
      },
    }));
    await expect(probeServer("https://example.com")).rejects.toThrow(/NewsRead server/);
  });

  it("reports HTTP errors", async () => {
    mockFetch(async () => ({ ok: false, status: 502, json: async () => null }));
    await expect(probeServer("https://example.com")).rejects.toThrow(/HTTP 502/);
  });

  it("reports unreachable servers", async () => {
    mockFetch(async () => {
      throw new Error("network down");
    });
    await expect(probeServer("https://example.com")).rejects.toThrow(/could not reach/i);
  });
});
