// Thin fetch wrapper, same conventions as frontend/lib/api.ts — except the
// base URL is the user's self-hosted server (set at onboarding) and the token
// lives in secure storage, both injected here by the auth provider.

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

let baseUrl: string | null = null;
let authToken: string | null = null;

export function configureApi(config: { serverUrl?: string | null; token?: string | null }) {
  if ("serverUrl" in config) baseUrl = config.serverUrl ?? null;
  if ("token" in config) authToken = config.token ?? null;
}

/** For callers that need raw fetch access (e.g. Q&A streaming). */
export function getApiConfig(): { baseUrl: string | null; token: string | null } {
  return { baseUrl, token: authToken };
}

async function request(path: string, opts: { method?: string; body?: unknown } = {}) {
  if (!baseUrl) throw new ApiError("No server configured", 0);
  const res = await fetch(`${baseUrl}/api${path}`, {
    method: opts.method ?? "GET",
    headers: {
      "Content-Type": "application/json",
      ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
    },
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    const detail =
      typeof data?.detail === "string"
        ? data.detail
        : Array.isArray(data?.detail)
          ? data.detail.map((d: { msg?: string }) => d.msg).join("; ")
          : res.statusText;
    throw new ApiError(detail || `HTTP ${res.status}`, res.status);
  }
  return res;
}

export async function api<T>(
  path: string,
  opts: { method?: string; body?: unknown } = {},
): Promise<T> {
  const res = await request(path, opts);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export type Page<T> = {
  items: T;
  nextCursor: string | null;
  prevCursor: string | null;
  unreadCount: number | null;
  newAboveCount: number | null;
};

/** Like api(), but surfaces the pagination/counter response headers. */
export async function apiPage<T>(path: string): Promise<Page<T>> {
  const res = await request(path);
  const count = (name: string) => {
    const value = res.headers.get(name);
    return value === null ? null : Number(value);
  };
  return {
    items: (await res.json()) as T,
    nextCursor: res.headers.get("x-next-cursor"),
    prevCursor: res.headers.get("x-prev-cursor"),
    unreadCount: count("x-unread-count"),
    newAboveCount: count("x-new-above-count"),
  };
}

export type ReadSource = "opened" | "scrolled" | "story" | "mark_all";

export type ReadBatch = {
  article_ids: number[];
  is_read?: boolean;
  read_source?: ReadSource;
  // Deepest article scrolled past this session — the resume position.
  frontier_article_id?: number;
  frontier_feed_id?: number;
};

/** Bulk read marks from scroll auto-read / story advances. */
export function sendReadBatch(batch: ReadBatch): Promise<void> {
  return api("/articles/state/batch", { method: "POST", body: batch });
}

export const fetcher = <T,>(path: string) => api<T>(path);

// Generated article images are stored as relative /api/... paths so they
// survive any deployment host; scraped og:images stay absolute. Resolve the
// relative ones against the configured server.
export function imageSrc(url: string | null): string | undefined {
  if (!url) return undefined;
  return url.startsWith("/") && baseUrl ? `${baseUrl}${url}` : url;
}
