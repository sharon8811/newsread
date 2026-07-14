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
  // Structured detail from the backend, when it sends an object (e.g. external
  // share failures include { message, reconnect }).
  detail?: unknown;
  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
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
          : typeof data?.detail?.message === "string"
            ? data.detail.message
            : res.statusText;
    throw new ApiError(detail, res.status, data?.detail);
  }
  return data as T;
}

export const fetcher = <T,>(path: string) => api<T>(path);

// Like api(), but hands back response headers too — article list pagination
// travels in X-Next-Cursor / X-Prev-Cursor / X-Unread-Count / X-New-Above-Count.
export async function apiWithHeaders<T>(
  path: string,
): Promise<{ data: T; headers: Headers }> {
  const token = getToken();
  const res = await fetch(`${API_URL}/api${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = typeof data?.detail === "string" ? data.detail : res.statusText;
    throw new ApiError(detail, res.status, data?.detail);
  }
  return { data: data as T, headers: res.headers };
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

// keepalive lets the final flush survive navigation/tab close (sendBeacon
// can't carry the Authorization header, fetch-with-keepalive can).
export function sendReadBatch(batch: ReadBatch, opts: { keepalive?: boolean } = {}) {
  const token = getToken();
  return fetch(`${API_URL}/api/articles/state/batch`, {
    method: "POST",
    keepalive: opts.keepalive ?? false,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(batch),
  });
}

// Generated article images are stored as relative /api/... paths so they
// survive any deployment host; scraped og:images stay absolute. Resolve the
// relative ones against the API base this client already talks to.
export function imageSrc(url: string | null): string | undefined {
  if (!url) return undefined;
  return url.startsWith("/") ? `${API_URL}${url}` : url;
}

// ——— Q&A streaming (SSE over fetch; EventSource can't POST or send auth) ———

export type QAStreamEvent =
  | { type: "status"; state: string }
  | { type: "tool_call"; id: string; name: string; args: Record<string, unknown> }
  | { type: "tool_result"; id: string; summary: string }
  | { type: "delta"; text: string }
  | { type: "done"; message: ChatMessage }
  | { type: "error"; detail: string };

export function streamQA(
  articleId: number,
  content: string,
  onEvent: (event: QAStreamEvent) => void,
): Promise<void> {
  return streamSSE(`/articles/${articleId}/qa/stream`, content, onEvent);
}

export function streamDiscussionQA(
  articleId: number,
  content: string,
  snapshot: import("./discussions").DiscussionSnapshot,
  onEvent: (event: QAStreamEvent) => void,
): Promise<void> {
  return streamSSE(
    `/articles/${articleId}/discussion/qa/stream`,
    content,
    onEvent,
    { snapshot },
  );
}

export function streamProjectQA(
  projectId: number,
  content: string,
  onEvent: (event: QAStreamEvent) => void,
): Promise<void> {
  return streamSSE(`/projects/${projectId}/qa/stream`, content, onEvent);
}

async function streamSSE(
  path: string,
  content: string,
  onEvent: (event: QAStreamEvent) => void,
  extra: Record<string, unknown> = {},
): Promise<void> {
  const token = getToken();
  const res = await fetch(`${API_URL}/api${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ content, ...extra }),
  });
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => null);
    throw new ApiError(
      typeof data?.detail === "string" ? data.detail : res.statusText,
      res.status,
    );
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let frameEnd;
    while ((frameEnd = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, frameEnd);
      buffer = buffer.slice(frameEnd + 2);
      for (const line of frame.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        const event = JSON.parse(line.slice(6)) as QAStreamEvent;
        if (event.type === "error") throw new ApiError(event.detail, 502);
        onEvent(event);
      }
    }
  }
}

// ——— types (mirror backend schemas) ———

export type ViewMode = "cards" | "list" | "stories";

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

export type SortOrder = "newest" | "oldest";

export type Feed = {
  id: number;
  url: string;
  title: string; // effective: rename applied server-side
  site_url: string | null;
  description: string | null;
  last_fetched_at: string | null;
  article_count: number;
  unread_count: number;
  pending_count: number;
  view_override: ViewMode | null;
  title_override: string | null;
  sort_order: SortOrder | null;
  retention_days: number | null;
  is_muted: boolean;
  ai_enabled: boolean;
  image_gen_enabled: boolean;
  refresh_interval_minutes: number;
};

// PATCH /feeds/{id}/settings — only send the fields being changed.
export type FeedSettingsPatch = Partial<{
  view_override: ViewMode | null;
  title_override: string | null;
  sort_order: SortOrder | null;
  retention_days: number | null;
  is_muted: boolean;
  ai_enabled: boolean;
  image_gen_enabled: boolean;
  refresh_interval_minutes: number;
}>;

// ——— feed catalog (curated directory) ———

export type CatalogEntry = {
  id: number;
  url: string;
  title: string;
  description: string | null;
  site_url: string | null;
  category: string;
  source_host: string;
  content_type: string | null;
  health_status: string;
  item_count: number | null;
  latest_item_at: string | null;
  preview_items: { title: string; url: string; published_at: string | null }[];
  subscriber_count: number;
  match_reason: string | null;
  feed_id: number | null; // my Feed id when I already subscribe to this URL
  subscribed: boolean;
};

export type CatalogCategory = {
  name: string;
  count: number;
};

export type CatalogPreviewItem = {
  title: string;
  url: string | null; // feeds may publish guid-only items with no link
  author: string | null;
  published_at: string | null;
  summary: string | null;
};

// Live snapshot of a catalog feed, fetched on demand for the detail modal.
export type CatalogPreview = {
  title: string;
  description: string | null;
  site_url: string | null;
  fetched_at: string;
  items: CatalogPreviewItem[];
};

// Optional quick settings sent with POST /feeds; include only values that
// differ from the defaults (ai/image on, unmuted) so subscribing to a feed
// someone else already tuned doesn't silently reset their global switches.
export type SubscribeOptions = {
  ai_enabled?: boolean;
  image_gen_enabled?: boolean;
  is_muted?: boolean;
};

// A topic-parameterized feed source (subscribe to any subreddit, news query…).
export type SmartFeed = {
  key: string;
  name: string;
  description: string;
  site_url: string;
  category: string;
  topic_label: string;
  topic_hint: string;
  example_topics: string[];
};

export type SmartFeedResolve = {
  key: string;
  topic: string;
  url: string;
  title: string;
};

export type EntityBadge = {
  id: number;
  kind: string; // github | hf_model | hf_dataset | arxiv | pypi | npm | youtube
  key: string;
  url: string;
  source: "primary" | "inline" | "ner";
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

// The /entity/[id] page: who/what this is plus coverage from the user's feeds.
export type EntityPage = {
  id: number;
  kind: string;
  key: string;
  url: string;
  name: string;
  badge: Record<string, string | number | null | undefined>;
  articles: Article[];
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
  image_pending: boolean; // an AI illustration is rendering — refetch soon
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

export type RelatedTier = "same_story" | "related";

export type RelatedArticle = {
  id: number;
  title: string;
  feed_title: string;
  published_at: string | null;
  is_read: boolean;
  tier: RelatedTier; // same_story = near-duplicate coverage of this story
};

export type SynthesisTimelineItem = { when: string; what: string };

export type SynthesisSource = { n: number; id: number; title: string };

export type CoverageSynthesis = {
  overview: string; // GFM with inline [n] citations
  timeline: SynthesisTimelineItem[] | null;
  timeline_raw: string | null; // only when the timeline lines didn't parse
  perspectives: string | null; // GFM bullets
  sources: SynthesisSource[]; // [1] is the current article
};

export type DislikeRuleKind = "article" | "entity" | "topic" | "story";

export type DislikeOptionEntity = {
  entity_id: number;
  kind: string;
  key: string;
  label: string;
};

export type DislikeOptions = {
  entities: DislikeOptionEntity[];
  topics: string[]; // [] when the LLM or embeddings are unavailable
  story_available: boolean;
};

export type DislikeRule = {
  id: number;
  kind: DislikeRuleKind;
  label: string;
  phrase: string | null;
  entity_id: number | null;
  article_id: number | null;
  expires_at: string | null;
  hidden_count: number;
  created_at: string;
};

export type DislikeRuleCreate = {
  kind: DislikeRuleKind;
  article_id?: number;
  entity_id?: number;
  phrase?: string;
};

export type DislikeRuleCreated = {
  rule: DislikeRule; // rule.hidden_count doubles as the "also hid N recent" figure
  preview: { id: number; title: string }[];
};

export type AiStatus = {
  configured: boolean;
  model: string | null;
  search: boolean;
  search_provider: "searxng" | "tavily" | null;
  source?: "user" | "system" | null; // whose key interactive AI calls run on
};

// ——— Bring-your-own LLM key ———

export type AIProvider = "openai" | "anthropic" | "custom";

export const AI_PROVIDER_LABELS: Record<AIProvider, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  custom: "Custom (OpenAI-compatible)",
};

export type AIImageSettings = {
  provider: AIProvider;
  model: string;
  base_url: string;
  key_hint: string;
  extra_params: string; // JSON object merged into every generation request; "" = none
};

export type AISettings = {
  configured: boolean; // the user saved their own key
  system_available: boolean; // a server-wide default exists to fall back to
  provider: AIProvider | null;
  model: string | null;
  base_url: string | null;
  key_hint: string | null; // keys are write-only; this is all that comes back
  supports_vision: boolean; // the model accepts image input (screenshot summaries)
  image: AIImageSettings | null;
  image_generation_available: boolean;
  image_prompt: string | null; // null = the default prompt applies
  default_image_prompt: string;
  image_gen_monthly_limit: number | null; // null = unlimited
  image_generations_this_month: number;
};

export type AIImageSettingsSave = {
  provider: AIProvider;
  model: string;
  api_key?: string; // omitted keeps the stored key / falls back to the main one
  base_url?: string;
  extra_params?: string; // sent in full each save; omitted/"" clears
};

export type AISettingsSave = {
  provider: AIProvider;
  model: string;
  api_key?: string; // omitted keeps the stored key
  base_url?: string;
  supports_vision?: boolean;
  image?: AIImageSettingsSave | null;
};

export type AITestResult = {
  ok: boolean;
  detail: string | null;
  model: string | null;
};

export type UsageFeatureKey = "summary" | "qa" | "share" | "image" | "topics" | "synthesis";

export const USAGE_FEATURE_LABELS: Record<UsageFeatureKey, string> = {
  summary: "Summaries",
  qa: "Q&A",
  share: "Share messages",
  image: "Images",
  topics: "Topic suggestions",
  synthesis: "Coverage synthesis",
};

export type UsageDay = { day: string; calls: number; tokens: number };

export type UsageFeature = { feature: string; calls: number; tokens: number };

export type UsageModel = {
  provider: string;
  model: string;
  calls: number;
  tokens: number;
};

export type UsageSummary = {
  range: ActivityRange;
  configured: boolean;
  total_calls: number;
  total_tokens: number;
  prev_total_tokens: number;
  error_count: number;
  days: UsageDay[];
  by_feature: UsageFeature[];
  by_model: UsageModel[];
};

export type UsageEvent = {
  id: number;
  feature: string;
  provider: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  duration_ms: number;
  status: string;
  error: string | null;
  created_at: string;
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

export type ProjectRole = "owner" | "member";

export type ProjectMember = {
  user: UserPublic;
  role: ProjectRole;
};

export type Project = {
  id: number;
  name: string;
  description: string;
  owner: UserPublic;
  my_role: ProjectRole;
  members: ProjectMember[];
  article_count: number; // only what the viewer can see
  unseen_count: number; // others' publishes since my last visit
  is_muted: boolean; // my per-project push mute
  created_at: string;
};

// The ticket workflow an article moves through inside a project. Keep in sync
// with the ProjectTicketStatus literal in backend/app/schemas.py — the status
// dropdown and filters render from this list, nothing else changes.
export const PROJECT_STATUSES = [
  { value: "open", label: "Open" },
  { value: "done", label: "Done" },
] as const;

export type ProjectTicketStatus = (typeof PROJECT_STATUSES)[number]["value"];

export type ProjectArticle = {
  id: number;
  project_id: number;
  article: Article;
  added_by: UserPublic;
  is_shared: boolean;
  shared_at: string | null;
  created_at: string;
  // Ticket state, shared per (project, article) across every pin of it.
  status: ProjectTicketStatus;
  status_updated_by: UserPublic | null;
  comment_count: number;
};

// One comment on an article's thread within a project.
export type ProjectComment = {
  id: number;
  author: UserPublic;
  body: string;
  link_url: string | null;
  created_at: string;
};

// Picker state for one of my projects against one article.
export type ArticleProjectStatus = {
  project_id: number;
  project_name: string;
  project_article_id: number | null; // my own pin, if any
  is_shared: boolean | null; // my pin's flag
  shared_by_others: boolean;
  suggested: boolean; // embedding similarity says this article belongs here
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

// ——— reading activity ———

export type ActivityRange = "week" | "month" | "year";

export type ActivityDay = { day: string; seconds: number };

export type ActivityFeed = { feed_id: number; title: string; seconds: number };

export type ActivityArticle = {
  article_id: number;
  title: string;
  feed_title: string;
  seconds: number;
};

export type ActivitySummary = {
  range: ActivityRange;
  total_seconds: number;
  prev_total_seconds: number; // same-length window just before; powers the delta
  days: ActivityDay[]; // dense, oldest → newest
  streak_days: number;
  top_feeds: ActivityFeed[];
  top_articles: ActivityArticle[];
};

// ——— messaging integrations (share to Slack / Teams as the user) ———

export type MessagingPlatform = "slack" | "teams";

export const PLATFORM_LABELS: Record<MessagingPlatform, string> = {
  slack: "Slack",
  teams: "Microsoft Teams",
};

export type IntegrationStatus = {
  platform: MessagingPlatform;
  configured: boolean; // server has credentials for this platform
  connected: boolean;
  status: "active" | "error" | null; // 'error' = needs reconnect
  workspace_name: string | null;
  account_name: string | null;
};

export type TargetType = "channel" | "group" | "dm" | "chat";

// One row in the live channel/chat picker (proxied from the platform).
export type TargetOption = {
  external_id: string;
  display_name: string;
  target_type: TargetType;
  meta: Record<string, unknown>;
  saved_id: number | null; // ShareTarget id when already saved
};

// A saved quick-share destination.
export type ShareTarget = {
  id: number;
  platform: MessagingPlatform;
  external_id: string;
  display_name: string;
  target_type: TargetType;
  meta: Record<string, unknown>;
  last_used_at: string | null;
};

export type ExternalShareResult = {
  id: number;
  platform: MessagingPlatform;
  target_display: string;
  status: string;
  created_at: string;
};
