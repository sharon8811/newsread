export function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = seconds / 60;
  if (minutes < 60) return `${Math.floor(minutes)}m ago`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.floor(hours)}h ago`;
  const days = hours / 24;
  if (days < 7) return `${Math.floor(days)}d ago`;
  if (days < 365) return `${Math.floor(days / 7)}w ago`;
  return `${Math.floor(days / 365)}y ago`;
}

export function humanCount(value: number | null | undefined): string {
  if (value == null) return "";
  if (value < 1000) return String(value);
  if (value < 1_000_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}k`;
  if (value < 1_000_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  return `${(value / 1_000_000_000).toFixed(1)}B`;
}

export function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Provider-aware label for a comment's attached link, detected purely from
 * the URL — no fetching. Anything unrecognized falls back to its hostname. */
export function describeLink(url: string): { kind: "github" | "youtube" | "link"; label: string } {
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.replace(/^www\./, "");
    if (host === "github.com") {
      const ref = parsed.pathname.match(/^\/[^/]+\/([^/]+)\/(?:pull|issues)\/(\d+)/);
      if (ref) return { kind: "github", label: `${ref[1]}#${ref[2]}` };
      return { kind: "github", label: "GitHub" };
    }
    if (host === "youtube.com" || host === "youtu.be") {
      return { kind: "youtube", label: "YouTube" };
    }
    return { kind: "link", label: host };
  } catch {
    return { kind: "link", label: url };
  }
}
