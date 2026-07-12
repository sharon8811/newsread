// Browser-side feed preview: the catalog modals fetch the publisher's feed
// directly from the reader's browser so preview traffic never funnels through
// our server. Feeds that block cross-origin reads (no CORS header, mixed
// content, network errors) fall back to the server preview endpoint, which
// fetches with SSRF protection and a shared cache.

import { api, ApiError, type CatalogPreview, type CatalogPreviewItem } from "./api";

const PREVIEW_ITEM_LIMIT = 8;
const SUMMARY_CHARS = 240;
const BROWSER_TIMEOUT_MS = 8000;

/** Where a preview came from — surfaced in the modal so it's honest about
 * hitting the publisher directly vs. going through the server. */
export type PreviewSource = "browser" | "server";

export type LoadedPreview = CatalogPreview & { source: PreviewSource };

function collapse(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

/** Plain text from an HTML fragment; DOMParser never executes scripts. */
function stripHtml(html: string): string {
  const doc = new DOMParser().parseFromString(html, "text/html");
  return collapse(doc.body.textContent ?? "");
}

function clip(text: string): string | null {
  if (!text) return null;
  if (text.length <= SUMMARY_CHARS) return text;
  const cut = text.slice(0, SUMMARY_CHARS);
  const atWord = cut.includes(" ") ? cut.slice(0, cut.lastIndexOf(" ")) : cut;
  return `${atWord.replace(/[.,;:]+$/, "")}…`;
}

function absolute(href: string | null, base: string): string | null {
  if (!href) return null;
  try {
    return new URL(href.trim(), base).toString();
  } catch {
    return null;
  }
}

function toIso(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value.trim());
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

/** First direct child matching a localName (namespace prefixes ignored, so
 * `dc:date` matches "date"); avoids picking up nested descendants. */
function child(el: Element, ...names: string[]): Element | null {
  for (const name of names) {
    for (const node of Array.from(el.children)) {
      if (node.localName === name) return node;
    }
  }
  return null;
}

function childText(el: Element, ...names: string[]): string | null {
  const found = child(el, ...names);
  const text = found?.textContent ? collapse(found.textContent) : "";
  return text || null;
}

function atomLink(el: Element): string | null {
  const links = Array.from(el.children).filter((node) => node.localName === "link");
  const alternate = links.find((node) => (node.getAttribute("rel") ?? "alternate") === "alternate");
  return (alternate ?? links[0])?.getAttribute("href") ?? null;
}

function itemLink(item: Element): string | null {
  // RSS: <link>url</link>; Atom (and some hybrids): <link href="url"/>.
  const link = child(item, "link");
  if (link?.getAttribute("href")) return atomLink(item);
  const text = link?.textContent?.trim();
  if (text) return text;
  // Last resort: a permalink guid (isPermaLink defaults to true).
  const guid = child(item, "guid");
  const guidText = guid?.textContent?.trim();
  if (guidText && guid?.getAttribute("isPermaLink") !== "false" && /^https?:\/\//i.test(guidText)) {
    return guidText;
  }
  return null;
}

function parseXmlItem(item: Element, base: string): CatalogPreviewItem | null {
  const rawTitle = childText(item, "title");
  const url = absolute(itemLink(item), base);
  const title = (rawTitle && stripHtml(rawTitle)) || url;
  if (!title) return null;
  const summaryHtml = childText(item, "description", "summary", "content", "encoded");
  return {
    title,
    url,
    author: childText(item, "author", "creator", "name"),
    published_at: toIso(childText(item, "pubDate", "published", "updated", "date")),
    summary: summaryHtml ? clip(stripHtml(summaryHtml)) : null,
  };
}

function parseXmlFeed(text: string, base: string): CatalogPreview {
  const doc = new DOMParser().parseFromString(text, "text/xml");
  if (doc.getElementsByTagName("parsererror").length > 0) {
    throw new Error("The response is not valid feed XML");
  }
  // <item> covers RSS 2.0 and RDF/RSS 1.0; <entry> covers Atom.
  let items = Array.from(doc.getElementsByTagName("item"));
  let channel: Element | null = doc.getElementsByTagName("channel")[0] ?? null;
  if (items.length === 0) {
    items = Array.from(doc.getElementsByTagName("entry"));
    channel = doc.getElementsByTagName("feed")[0] ?? null;
  }
  if (items.length === 0 && channel === null) {
    throw new Error("The response is not a recognizable RSS or Atom feed");
  }
  // RSS: plain-text <link> (skip any atom:link rel="self"); Atom: href link.
  const links = channel ? Array.from(channel.children).filter((node) => node.localName === "link") : [];
  const siteLink = links.find((node) => !node.getAttribute("href"))?.textContent?.trim()
    || (channel ? atomLink(channel) : null);
  return {
    title: (channel && childText(channel, "title")) ?? "",
    description: channel ? childText(channel, "description", "subtitle") : null,
    site_url: absolute(siteLink, base),
    fetched_at: new Date().toISOString(),
    items: items
      .slice(0, PREVIEW_ITEM_LIMIT)
      .map((item) => parseXmlItem(item, base))
      .filter((item): item is CatalogPreviewItem => item !== null),
  };
}

type JsonFeedItem = {
  url?: string;
  external_url?: string;
  title?: string;
  content_html?: string;
  content_text?: string;
  date_published?: string;
  date_modified?: string;
  author?: { name?: string };
  authors?: { name?: string }[];
};

function parseJsonFeed(data: unknown, base: string): CatalogPreview {
  if (typeof data !== "object" || data === null || !Array.isArray((data as { items?: unknown }).items)) {
    throw new Error("The response is not a JSON Feed object");
  }
  const feed = data as { title?: string; description?: string; home_page_url?: string; items: JsonFeedItem[] };
  return {
    title: feed.title ?? "",
    description: feed.description ? stripHtml(feed.description) || null : null,
    site_url: absolute(feed.home_page_url ?? null, base),
    fetched_at: new Date().toISOString(),
    items: feed.items.slice(0, PREVIEW_ITEM_LIMIT).flatMap((item) => {
      const url = absolute(item.url ?? item.external_url ?? null, base);
      const content = item.content_html ?? item.content_text ?? "";
      const title = item.title?.trim() || url;
      if (!title) return [];
      return [{
        title,
        url,
        author: item.author?.name ?? item.authors?.[0]?.name ?? null,
        published_at: toIso(item.date_published ?? item.date_modified ?? null),
        summary: content ? clip(stripHtml(content)) : null,
      }];
    }),
  };
}

/** Parse a raw feed body (RSS 2.0, RDF, Atom, or JSON Feed). Exported for tests. */
export function parseFeed(body: string, contentType: string, baseUrl: string): CatalogPreview {
  if (contentType.includes("json") || body.trimStart().startsWith("{")) {
    return parseJsonFeed(JSON.parse(body), baseUrl);
  }
  return parseXmlFeed(body, baseUrl);
}

/** The message to show for a failed preview. A 503 from the server fallback
 * carries an honest, actionable detail ("reddit.com is rate-limiting our
 * preview requests…"); anything else gets the caller's generic fallback. */
export function previewErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.status === 503 && error.message) return error.message;
  return fallback;
}

/** Fetch a feed preview straight from the browser, falling back to the given
 * server preview endpoint when the publisher blocks cross-origin reads. */
export async function fetchPreview(feedUrl: string, serverPath: string): Promise<LoadedPreview> {
  try {
    const res = await fetch(feedUrl, { signal: AbortSignal.timeout(BROWSER_TIMEOUT_MS) });
    if (!res.ok) throw new Error(`Feed responded with ${res.status}`);
    const body = await res.text();
    const preview = parseFeed(body, res.headers.get("content-type") ?? "", res.url || feedUrl);
    return { ...preview, source: "browser" };
  } catch {
    const preview = await api<CatalogPreview>(serverPath);
    return { ...preview, source: "server" };
  }
}
