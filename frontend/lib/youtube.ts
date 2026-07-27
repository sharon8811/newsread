/** Recognising YouTube subscriptions in the UI.
 *
 * A followed channel is an ordinary feed row whose URL is the Atom feed
 * YouTube publishes per channel, and a video is an ordinary article whose URL
 * is a watch page. Neither carries a marker from the server, so — as
 * `discussions.ts` does for Hacker News — the client reads it off the URL.
 */

const YOUTUBE_HOSTS = new Set(["youtube.com", "youtube-nocookie.com", "youtu.be"]);

// Every video id is exactly this shape; anything else on a watch path is a
// mangled link, not a video we can send someone to.
const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
const VIDEO_PATHS = new Set(["shorts", "embed", "live", "v"]);

function parse(value: string | null | undefined): { url: URL; host: string } | null {
  if (!value) return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return null;
  const host = url.hostname.toLowerCase().replace(/^(www|m)\./, "");
  return YOUTUBE_HOSTS.has(host) ? { url, host } : null;
}

function segments(url: URL): string[] {
  return url.pathname.split("/").filter(Boolean);
}

/** True for the per-channel Atom feed a followed YouTube channel subscribes to.
 *
 * We only ever create the `?channel_id=` form, but the same path serves the
 * older `?user=` and `?playlist_id=` variants a reader could have pasted, and
 * all of them are YouTube subscriptions as far as the sidebar cares.
 */
export function isYouTubeChannelFeed(feedUrl: string | null | undefined): boolean {
  const parsed = parse(feedUrl);
  if (parsed === null) return false;
  return parsed.url.pathname.replace(/\/+$/, "") === "/feeds/videos.xml";
}

/** True for a link to a single video: watch, youtu.be, shorts, embed, live. */
export function isYouTubeVideoUrl(articleUrl: string | null | undefined): boolean {
  const parsed = parse(articleUrl);
  if (parsed === null) return false;
  const { url, host } = parsed;
  const path = segments(url);
  if (host === "youtu.be") return path.length === 1 && VIDEO_ID.test(path[0]);
  if (url.pathname.replace(/\/+$/, "") === "/watch") {
    return VIDEO_ID.test(url.searchParams.get("v") ?? "");
  }
  return path.length === 2 && VIDEO_PATHS.has(path[0]) && VIDEO_ID.test(path[1]);
}
