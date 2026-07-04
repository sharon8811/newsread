export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const TOKEN_KEY = "newsread_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (token === null) localStorage.removeItem(TOKEN_KEY);
  else localStorage.setItem(TOKEN_KEY, token);
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(
  path: string,
  opts: { method?: string; body?: unknown } = {},
): Promise<T> {
  const token = getToken();
  const res = await fetch(`${API_URL}/api${path}`, {
    method: opts.method ?? "GET",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail =
      typeof data?.detail === "string"
        ? data.detail
        : Array.isArray(data?.detail)
          ? data.detail.map((d: { msg?: string }) => d.msg).join("; ")
          : res.statusText;
    throw new ApiError(detail, res.status);
  }
  return data as T;
}

export const fetcher = <T,>(path: string) => api<T>(path);

// ——— types (mirror backend schemas) ———

export type ViewMode = "list" | "stories" | "zen";

export type User = {
  id: number;
  email: string;
  username: string;
  name: string;
  default_view: ViewMode;
};

export type UserPublic = {
  id: number;
  username: string;
  name: string;
};

export type Feed = {
  id: number;
  url: string;
  title: string;
  site_url: string | null;
  description: string | null;
  last_fetched_at: string | null;
  article_count: number;
  unread_count: number;
  view_override: ViewMode | null;
};

export type EntityBadge = {
  id: number;
  kind: string; // github | hf_model | hf_dataset | arxiv | pypi | npm | youtube
  key: string;
  url: string;
  source: "primary" | "inline";
  badge: Record<string, string | number | null | undefined>;
};

export type EntitySnapshot = {
  captured_at: string;
  data: Record<string, unknown>;
};

export type EntityFull = EntityBadge & {
  data: Record<string, unknown>;
  fetched_at: string | null;
  deltas: Record<string, number>;
  snapshots: EntitySnapshot[]; // newest-first
};

export type Article = {
  id: number;
  feed_id: number;
  feed_title: string;
  title: string;
  url: string;
  comments_url: string | null;
  author: string | null;
  published_at: string | null;
  excerpt: string;
  image_url: string | null;
  is_read: boolean;
  is_saved: boolean;
  summary: string;
  summary_short: string;
  summary_medium: string;
  entities: EntityBadge[];
};

export type ArticleDetail = Omit<Article, "entities"> & {
  content_html: string;
  summary_model: string | null;
  entities: EntityFull[];
};

export type AiStatus = { configured: boolean; model: string | null };

export type ChatMessage = {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
};

export type Share = {
  id: number;
  article: Article;
  from_user: UserPublic;
  to_users: UserPublic[];
  note: string | null;
  created_at: string;
  seen_at: string | null;
};
