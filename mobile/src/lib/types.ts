// Mirrors backend schemas (subset the mobile app uses); keep field names snake_case.

export type ViewMode = "list" | "stories" | "zen";

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
