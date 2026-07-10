// Mirrors backend schemas (subset the mobile app uses); keep field names snake_case.

export type ViewMode = "cards" | "list" | "stories";

export type User = {
  id: number;
  email: string;
  username: string;
  name: string;
  default_view: ViewMode;
};

export type TokenOut = {
  access_token: string;
  token_type: "bearer";
  user: User;
};

export type ServerInfo = {
  status: string;
  app: string;
  version: string;
  min_client_version: string;
};

// One entry in the curated feed directory (GET /catalog).
export type CatalogEntry = {
  id: number;
  url: string;
  title: string;
  description: string | null;
  site_url: string | null;
  category: string;
  feed_id: number | null; // my Feed id when already subscribed to this URL
  subscribed: boolean;
};

export type CatalogCategory = {
  name: string;
  count: number;
};

export type EntityBadge = {
  id: number;
  kind: string;
  key: string;
  url: string;
  source: "primary" | "inline";
  badge: Record<string, string | number | null | undefined>;
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
  enriching: boolean;
  // True while an AI illustration is rendering in the background for this
  // article (~10-60s). The server stops reporting it after ~3min, which
  // halts the fast polls on its own.
  image_pending: boolean;
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
};

export type AiStatus = {
  configured: boolean;
  model: string | null;
  search: boolean;
  search_provider: "searxng" | "tavily" | null;
};

export type ToolEvent = {
  name: string;
  args: Record<string, unknown>;
  summary: string | null;
};

export type ChatMessage = {
  id: number;
  role: "user" | "assistant";
  content: string;
  tool_events?: ToolEvent[] | null;
  created_at: string;
};
