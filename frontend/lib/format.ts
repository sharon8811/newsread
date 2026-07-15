export function timeAgo(iso: string | null | undefined): string {
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

/** Coarse catalog freshness: "Updated today" … "Updated 2 years ago". */
export function freshness(value: string | null | undefined): string | null {
  if (!value) return null;
  const days = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 86_400_000));
  if (days === 0) return "Updated today";
  if (days === 1) return "Updated yesterday";
  if (days < 30) return `Updated ${days} days ago`;
  if (days < 365) {
    const months = Math.floor(days / 30);
    return `Updated ${months} ${months === 1 ? "month" : "months"} ago`;
  }
  const years = Math.floor(days / 365);
  return `Updated ${years} ${years === 1 ? "year" : "years"} ago`;
}

export function formatFeedType(value: string | null | undefined): string {
  if (!value) return "RSS";
  if (value.includes("atom")) return "Atom";
  if (value.includes("json")) return "JSON Feed";
  return "RSS";
}

export function humanCount(value: number | null | undefined): string {
  if (value == null) return "";
  if (value < 1000) return String(value);
  if (value < 1_000_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}k`;
  if (value < 1_000_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  return `${(value / 1_000_000_000).toFixed(1)}B`;
}

/** Reading-time totals: "1h 24m", "12m", "45s"; zero reads as "0m". */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return seconds > 0 ? `${Math.round(seconds)}s` : "0m";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest > 0 ? `${hours}h ${rest}m` : `${hours}h`;
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
