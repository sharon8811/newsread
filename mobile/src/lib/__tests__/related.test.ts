import { configureApi } from "../api";
import { relatedKey, synthesizeCoverage, timelineRows } from "../related";
import type { CoverageSynthesis } from "../types";

const realFetch = globalThis.fetch;

function mockResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => body,
  } as unknown as Response;
}

function makeSynthesis(over: Partial<CoverageSynthesis> = {}): CoverageSynthesis {
  return {
    overview: "o",
    timeline: [{ when: "May 1", what: "started" }],
    timeline_raw: null,
    perspectives: null,
    sources: [{ n: 1, id: 1, title: "T" }],
    ...over,
  };
}

afterEach(() => {
  globalThis.fetch = realFetch;
  configureApi({ serverUrl: null, token: null });
});

describe("relatedKey", () => {
  it("builds the SWR key from an id", () => {
    expect(relatedKey(5)).toBe("/articles/5/related");
    expect(relatedKey("7")).toBe("/articles/7/related");
  });

  it("returns null (no fetch) without an id", () => {
    expect(relatedKey(undefined)).toBeNull();
    expect(relatedKey(null)).toBeNull();
  });
});

describe("synthesizeCoverage", () => {
  it("POSTs to the synthesis endpoint with auth", async () => {
    const fetchMock = jest.fn().mockResolvedValue(mockResponse(makeSynthesis()));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    configureApi({ serverUrl: "https://news.example", token: "tok" });

    const result = await synthesizeCoverage(5);
    expect(result.overview).toBe("o");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://news.example/api/articles/5/related-synthesis");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer tok");
  });
});

describe("timelineRows", () => {
  it("returns structured rows when present", () => {
    expect(timelineRows(makeSynthesis())).toEqual([{ when: "May 1", what: "started" }]);
  });

  it("returns null for empty or missing timelines (raw fallback)", () => {
    expect(timelineRows(makeSynthesis({ timeline: null }))).toBeNull();
    expect(timelineRows(makeSynthesis({ timeline: [] }))).toBeNull();
  });
});
