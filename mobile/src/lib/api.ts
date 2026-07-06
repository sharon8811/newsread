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

export type Page<T> = { items: T; nextCursor: string | null };

/** Like api(), but surfaces the X-Next-Cursor pagination header. */
export async function apiPage<T>(path: string): Promise<Page<T>> {
  const res = await request(path);
  return {
    items: (await res.json()) as T,
    nextCursor: res.headers.get("x-next-cursor"),
  };
}

export const fetcher = <T,>(path: string) => api<T>(path);
