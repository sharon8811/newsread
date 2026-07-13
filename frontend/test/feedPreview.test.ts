import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchPreview, parseFeed, previewErrorMessage } from "@/lib/feedPreview";
import { ApiError } from "@/lib/api";

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: apiMock,
}));

const BASE = "https://blog.example/feed.xml";

const RSS = `<?xml version="1.0"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <atom:link href="https://blog.example/feed.xml" rel="self"/>
    <title>Example Blog</title>
    <link>https://blog.example</link>
    <description>A blog about examples</description>
    <item>
      <title>First &amp; finest</title>
      <link>https://blog.example/first</link>
      <description><![CDATA[<p>Hello <b>world</b> &amp; a fine read.</p>]]></description>
      <pubDate>Sat, 11 Jul 2026 08:00:00 GMT</pubDate>
      <dc:creator>Ann Author</dc:creator>
    </item>
    <item>
      <title>Relative link</title>
      <link>/posts/relative</link>
    </item>
    <item>
      <title>Permalink guid only</title>
      <guid>https://blog.example/guid-post</guid>
    </item>
    <item>
      <title>No link at all</title>
      <guid isPermaLink="false">tag:blog.example,2026:no-link</guid>
    </item>
    <item>
      <title>Long story</title>
      <description>${"lorem ipsum ".repeat(60)}</description>
    </item>
  </channel>
</rss>`;

const ATOM = `<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Blog</title>
  <subtitle>Atomic writing</subtitle>
  <link rel="self" href="https://atom.example/feed"/>
  <link rel="alternate" href="https://atom.example"/>
  <entry>
    <title>Entry one</title>
    <link rel="alternate" href="https://atom.example/one"/>
    <published>2026-07-11T09:30:00Z</published>
    <summary>Short and sweet.</summary>
    <author><name>Bea Writer</name></author>
  </entry>
</feed>`;

const JSON_FEED = JSON.stringify({
  version: "https://jsonfeed.org/version/1.1",
  title: "JSON Blog",
  description: "Structured stories",
  home_page_url: "https://json.example",
  items: [
    {
      id: "1",
      url: "https://json.example/one",
      title: "JSON one",
      content_html: "<p>Body text</p>",
      date_published: "2026-07-10T10:00:00Z",
      authors: [{ name: "Cy Coder" }],
    },
    { id: "2", content_text: "No link, no title" },
  ],
});

describe("parseFeed", () => {
  it("parses RSS 2.0 with entities, relative links, guid permalinks, and linkless items", () => {
    const preview = parseFeed(RSS, "application/rss+xml", BASE);
    expect(preview.title).toBe("Example Blog");
    expect(preview.description).toBe("A blog about examples");
    // The site link, not the atom:link rel="self" feed URL.
    expect(preview.site_url).toBe("https://blog.example/");

    const [first, relative, permalink, linkless, long] = preview.items;
    expect(first).toMatchObject({
      title: "First & finest",
      url: "https://blog.example/first",
      author: "Ann Author",
      summary: "Hello world & a fine read.",
    });
    expect(first.published_at).toBe("2026-07-11T08:00:00.000Z");
    expect(relative.url).toBe("https://blog.example/posts/relative");
    expect(permalink.url).toBe("https://blog.example/guid-post");
    expect(linkless.url).toBeNull();
    expect(long.summary!.endsWith("…")).toBe(true);
    expect(long.summary!.length).toBeLessThanOrEqual(241);
  });

  it("parses Atom feeds", () => {
    const preview = parseFeed(ATOM, "application/atom+xml", "https://atom.example/feed");
    expect(preview.title).toBe("Atom Blog");
    expect(preview.description).toBe("Atomic writing");
    expect(preview.site_url).toBe("https://atom.example/");
    expect(preview.items).toHaveLength(1);
    expect(preview.items[0]).toMatchObject({
      title: "Entry one",
      url: "https://atom.example/one",
      author: "Bea Writer",
      published_at: "2026-07-11T09:30:00.000Z",
      summary: "Short and sweet.",
    });
  });

  it("parses JSON Feed (by content type or shape)", () => {
    for (const contentType of ["application/feed+json", ""]) {
      const preview = parseFeed(JSON_FEED, contentType, "https://json.example/feed");
      expect(preview.title).toBe("JSON Blog");
      expect(preview.site_url).toBe("https://json.example/");
      expect(preview.items[0]).toMatchObject({
        title: "JSON one",
        url: "https://json.example/one",
        author: "Cy Coder",
        summary: "Body text",
      });
      // Untitled + linkless items are dropped rather than rendered empty.
      expect(preview.items).toHaveLength(1);
    }
  });

  it("caps items at the preview limit", () => {
    const many = `<rss><channel><title>Many</title>${Array.from(
      { length: 12 },
      (_, i) => `<item><title>S${i}</title><link>https://x.example/${i}</link></item>`,
    ).join("")}</channel></rss>`;
    expect(parseFeed(many, "text/xml", BASE).items).toHaveLength(8);
  });

  it("rejects non-feed bodies", () => {
    expect(() => parseFeed("not xml at all", "text/xml", BASE)).toThrow();
    expect(() => parseFeed("<root><child/></root>", "text/xml", BASE)).toThrow(/recognizable/);
    expect(() => parseFeed('{"not": "a feed"}', "application/json", BASE)).toThrow(/JSON Feed/);
  });
});

describe("fetchPreview", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  function okResponse(body: string, contentType: string, url = BASE) {
    return {
      ok: true,
      status: 200,
      url,
      headers: new Headers({ "content-type": contentType }),
      text: async () => body,
    };
  }

  it("fetches and parses in the browser when the publisher allows it", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(okResponse(RSS, "application/rss+xml")));
    const preview = await fetchPreview(BASE, "/catalog/1/preview");
    expect(preview.source).toBe("browser");
    expect(preview.title).toBe("Example Blog");
    expect(apiMock).not.toHaveBeenCalled();
  });

  it("resolves relative links against the final redirected URL", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(okResponse(RSS, "application/rss+xml", "https://moved.example/feed.xml")),
    );
    const preview = await fetchPreview(BASE, "/catalog/1/preview");
    expect(preview.items[1].url).toBe("https://moved.example/posts/relative");
  });

  it("falls back to the server when the fetch is blocked", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("CORS")));
    apiMock.mockResolvedValue({ title: "From server", description: null, site_url: null, fetched_at: "now", items: [] });
    const preview = await fetchPreview(BASE, "/catalog/1/preview");
    expect(apiMock).toHaveBeenCalledWith("/catalog/1/preview");
    expect(preview.source).toBe("server");
    expect(preview.title).toBe("From server");
  });

  it("falls back to the server on non-2xx and unparsable bodies", async () => {
    apiMock.mockResolvedValue({ title: "From server", description: null, site_url: null, fetched_at: "now", items: [] });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ...okResponse("", "text/xml"), ok: false, status: 403 }));
    expect((await fetchPreview(BASE, "/x")).source).toBe("server");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(okResponse("<html><body>a página</body></html>", "text/html")));
    expect((await fetchPreview(BASE, "/x")).source).toBe("server");
  });
});

describe("previewErrorMessage", () => {
  const fallback = "Could not load stories.";

  it("surfaces the server detail for a 503 (publisher rate limit)", () => {
    const error = new ApiError("reddit.com is rate-limiting our preview requests right now.", 503);
    expect(previewErrorMessage(error, fallback)).toBe(
      "reddit.com is rate-limiting our preview requests right now.",
    );
  });

  it("uses the fallback for other statuses, empty messages, and non-API errors", () => {
    expect(previewErrorMessage(new ApiError("upstream broke", 502), fallback)).toBe(fallback);
    expect(previewErrorMessage(new ApiError("", 503), fallback)).toBe(fallback);
    expect(previewErrorMessage(new TypeError("CORS"), fallback)).toBe(fallback);
    expect(previewErrorMessage(undefined, fallback)).toBe(fallback);
  });
});

describe("parseFeed edge branches", () => {
  it("clips long summaries at a word boundary and trims trailing punctuation", () => {
    const words = Array.from({ length: 60 }, (_, i) => `word${i},`).join(" ");
    const xml = `<?xml version="1.0"?><rss><channel><title>T</title>
      <item><title>Long</title><link>https://x/l</link><description>${words}</description></item>
    </channel></rss>`;
    const preview = parseFeed(xml, "application/rss+xml", "https://x/feed");
    const summary = preview.items[0].summary!;
    expect(summary.endsWith("…")).toBe(true);
    expect(summary.length).toBeLessThanOrEqual(241);
    expect(summary).not.toMatch(/[.,;:]+…$/);
  });

  it("drops invalid dates and unparsable link URLs", () => {
    const xml = `<?xml version="1.0"?><rss><channel><title>T</title>
      <item><title>Weird</title><link>ht!tp://[bad</link><pubDate>not a date</pubDate></item>
    </channel></rss>`;
    const preview = parseFeed(xml, "application/rss+xml", "https://x/feed");
    expect(preview.items[0].published_at).toBeNull();
  });

  it("prefers the atom alternate link and falls back over rel-less links", () => {
    const xml = `<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <title>A</title>
      <link rel="self" href="https://x/self"/>
      <link href="https://x/site"/>
      <entry><title>E</title><link href="https://x/e1"/><updated>2024-01-01T00:00:00Z</updated></entry>
    </feed>`;
    const preview = parseFeed(xml, "application/atom+xml", "https://x/feed");
    expect(preview.site_url).toBe("https://x/site");
    expect(preview.items[0].published_at).toBe("2024-01-01T00:00:00.000Z");
  });

  it("json feed: authors array fallback and content_text summaries", () => {
    const body = JSON.stringify({
      version: "https://jsonfeed.org/version/1.1",
      title: "JF",
      items: [
        {
          id: "1",
          title: "JF Entry",
          content_text: "plain text body",
          authors: [{ name: "Ada" }],
          date_modified: "2024-02-02T00:00:00Z",
        },
      ],
    });
    const preview = parseFeed(body, "application/feed+json", "https://x/feed.json");
    expect(preview.items[0].author).toBe("Ada");
    expect(preview.items[0].summary).toBe("plain text body");
    expect(preview.items[0].published_at).toBe("2024-02-02T00:00:00.000Z");
  });
});
