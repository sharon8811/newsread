import type { components } from "./api-schema.gen";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const TOKEN_KEY = "newsread_token";

// localStorage can throw (Safari/Firefox private browsing, storage disabled);
// degrade to logged-out instead of breaking every API call.
export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token === null) localStorage.removeItem(TOKEN_KEY);
    else localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Session just won't persist across reloads.
  }
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

// Bearer header for the current session, spreadable into any fetch call.
function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// FastAPI error payloads: a plain string, a validation-error list, or a
// structured object with a message. Normalize to something displayable.
function errorDetail(data: { detail?: unknown } | null, fallback: string): string {
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d: { msg?: string }) => d.msg).join("; ");
  }
  if (
    detail !== null &&
    typeof detail === "object" &&
    typeof (detail as { message?: unknown }).message === "string"
  ) {
    return (detail as { message: string }).message;
  }
  return fallback;
}

function throwApiError(res: Response, data: { detail?: unknown } | null): never {
  throw new ApiError(errorDetail(data, res.statusText), res.status, data?.detail);
}

export async function api<T>(
  path: string,
  opts: { method?: string; body?: unknown } = {},
): Promise<T> {
  const res = await fetch(`${API_URL}/api${path}`, {
    method: opts.method ?? "GET",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) throwApiError(res, data);
  return data as T;
}

export const fetcher = <T,>(path: string) => api<T>(path);

// Like api(), but hands back response headers too — article list pagination
// travels in X-Next-Cursor / X-Prev-Cursor / X-Unread-Count / X-New-Above-Count.
export async function apiWithHeaders<T>(
  path: string,
): Promise<{ data: T; headers: Headers }> {
  const res = await fetch(`${API_URL}/api${path}`, { headers: authHeaders() });
  const data = await res.json().catch(() => null);
  if (!res.ok) throwApiError(res, data);
  return { data: data as T, headers: res.headers };
}

export type ReadSource = Schemas["ArticleStateBatchIn"]["read_source"];

// is_read/read_source are backend defaults; frontier_* is the deepest article
// scrolled past this session — the resume position.
export type ReadBatch = WithOptional<
  Schemas["ArticleStateBatchIn"],
  "is_read" | "read_source"
>;

// keepalive lets the final flush survive navigation/tab close (sendBeacon
// can't carry the Authorization header, fetch-with-keepalive can).
export function sendReadBatch(batch: ReadBatch, opts: { keepalive?: boolean } = {}) {
  return fetch(`${API_URL}/api/articles/state/batch`, {
    method: "POST",
    keepalive: opts.keepalive ?? false,
    headers: { "Content-Type": "application/json", ...authHeaders() },
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
  const res = await fetch(`${API_URL}/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ content, ...extra }),
  });
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => null);
    throwApiError(res, data);
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

// ——— types (generated from the backend's OpenAPI schema) ———
//
// `npm run types:gen` regenerates lib/api-schema.gen.ts from
// backend/openapi.json (itself exported by backend/scripts/export_openapi.py).
// The aliases below keep the frontend's established names; a backend schema
// change now surfaces as a compile error instead of a runtime undefined.

type Schemas = components["schemas"];

// Pydantic fills these request fields from defaults when omitted, but the
// generated types mark defaulted fields as required — relax them.
type WithOptional<T, K extends keyof T> = Omit<T, K> & Partial<Pick<T, K>>;

// Backend types entity badge payloads as plain dicts; the UI renders their
// values, so keep the narrower value type it was written against.
type BadgeData = Record<string, string | number | null | undefined>;

export type User = Schemas["UserOut"];
export type UserPublic = Schemas["UserPublic"];
export type ViewMode = User["default_view"];
export type SortOrder = NonNullable<Schemas["FeedOut"]["sort_order"]>;

export type Feed = Schemas["FeedOut"];
// PATCH /feeds/{id}/settings — only send the fields being changed.
export type FeedSettingsPatch = Schemas["FeedSettingsIn"];

// ——— feed catalog (curated directory) ———

export type CatalogEntry = Omit<Schemas["CatalogEntryOut"], "preview_items"> & {
  // Backend types cached preview rows as plain dicts; keep the render shape.
  preview_items: { title: string; url: string; published_at: string | null }[];
};
export type CatalogCategory = Schemas["CatalogCategoryOut"];
export type CatalogPreviewItem = Schemas["CatalogPreviewItemOut"];
// Live snapshot of a catalog feed, fetched on demand for the detail modal.
export type CatalogPreview = Schemas["CatalogPreviewOut"];

// Optional quick settings sent with POST /feeds; include only values that
// differ from the defaults (ai/image on, unmuted) so subscribing to a feed
// someone else already tuned doesn't silently reset their global switches.
export type SubscribeOptions = Pick<
  Schemas["AddFeedIn"],
  "ai_enabled" | "image_gen_enabled" | "is_muted"
>;

// A topic-parameterized feed source (subscribe to any subreddit, news query…).
export type SmartFeed = Schemas["SmartFeedOut"];
export type SmartFeedResolve = Schemas["SmartFeedResolveOut"];

export type EntityBadge = Omit<Schemas["EntityBadge"], "badge"> & { badge: BadgeData };
export type EntitySnapshot = Schemas["EntitySnapshotOut"];
export type EntityFull = Omit<Schemas["EntityFull"], "badge" | "deltas"> & {
  badge: BadgeData;
  deltas: Record<string, number>;
};
// The /entity/[id] page: who/what this is plus coverage from the user's feeds.
export type EntityPage = Omit<Schemas["EntityPageOut"], "badge"> & { badge: BadgeData };

export type Article = Omit<Schemas["ArticleListItem"], "entities"> & {
  entities: EntityBadge[];
};
export type ArticleDetail = Omit<Schemas["ArticleDetail"], "entities"> & {
  entities: EntityFull[];
};

export type RelatedArticle = Schemas["RelatedArticleItem"];
export type RelatedTier = RelatedArticle["tier"]; // same_story = near-duplicate coverage

export type SynthesisTimelineItem = Schemas["SynthesisTimelineItem"];
export type SynthesisSource = Schemas["SynthesisSourceOut"];
export type CoverageSynthesis = Schemas["SynthesisOut"];

export type DislikeRule = Schemas["DislikeRuleOut"];
export type DislikeRuleKind = DislikeRule["kind"];
export type DislikeOptionEntity = Schemas["DislikeOptionEntity"];
export type DislikeOptions = Schemas["DislikeOptionsOut"];
export type DislikeRuleCreate = Schemas["DislikeRuleIn"];
export type DislikeRuleCreated = Schemas["DislikeRuleCreateOut"];

export type AiStatus = Schemas["AiStatusOut"];

// ——— Bring-your-own LLM key ———

export type AIProvider = NonNullable<Schemas["AISettingsOut"]["provider"]>;

export const AI_PROVIDER_LABELS: Record<AIProvider, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  custom: "Custom (OpenAI-compatible)",
};

export type AIImageSettings = Schemas["AIImageSettingsOut"];
export type AISettings = Schemas["AISettingsOut"];
export type AIImageSettingsSave = WithOptional<
  Schemas["AIImageSettingsIn"],
  "base_url" | "extra_params"
>;
export type AISettingsSave = Omit<
  WithOptional<Schemas["AISettingsIn"], "base_url" | "supports_vision">,
  "image"
> & { image?: AIImageSettingsSave | null };
export type AITestResult = Schemas["AITestOut"];

// FE-only: which usage features get a friendly label (backend stores plain strings).
export type UsageFeatureKey = "summary" | "qa" | "share" | "image" | "topics" | "synthesis";

export const USAGE_FEATURE_LABELS: Record<UsageFeatureKey, string> = {
  summary: "Summaries",
  qa: "Q&A",
  share: "Share messages",
  image: "Images",
  topics: "Topic suggestions",
  synthesis: "Coverage synthesis",
};

export type UsageDay = Schemas["UsageDayOut"];
export type UsageFeature = Schemas["UsageFeatureOut"];
export type UsageModel = Schemas["UsageModelOut"];
export type UsageSummary = Schemas["UsageSummaryOut"];
export type UsageEvent = Schemas["UsageEventOut"];

// Backend types chat messages loosely (role: str, tool_events: list[dict]);
// keep the narrowings the UI renders from. Tighten server-side eventually.
export type ToolEvent = {
  name: string;
  args: Record<string, unknown>;
  summary: string | null;
};

export type ChatMessage = Omit<Schemas["MessageOut"], "role" | "tool_events"> & {
  role: "user" | "assistant";
  tool_events?: ToolEvent[] | null;
};

export type ProjectMember = Schemas["ProjectMemberOut"];
export type ProjectRole = ProjectMember["role"];
export type Project = Schemas["ProjectOut"];

export type ProjectArticle = Schemas["ProjectArticleOut"];
// The ticket workflow an article moves through inside a project — derived
// from the backend literal, so a new status is a compile error here.
export type ProjectTicketStatus = ProjectArticle["status"];

export const PROJECT_STATUSES = [
  { value: "open", label: "Open" },
  { value: "done", label: "Done" },
] as const satisfies readonly { value: ProjectTicketStatus; label: string }[];

// One comment on an article's thread within a project.
export type ProjectComment = Schemas["ProjectCommentOut"];

// Picker state for one of my projects against one article.
export type ArticleProjectStatus = Schemas["ArticleProjectStatus"];

export type Share = Schemas["ShareOut"];

// ——— reading activity ———

export type ActivitySummary = Schemas["ActivitySummaryOut"];
export type ActivityRange = ActivitySummary["range"];
export type ActivityDay = Schemas["ActivityDayOut"];
export type ActivityFeed = Schemas["ActivityFeedOut"];
export type ActivityArticle = Schemas["ActivityArticleOut"];

// ——— messaging integrations (share to Slack / Teams as the user) ———

export type IntegrationStatus = Schemas["IntegrationStatusOut"];
export type MessagingPlatform = IntegrationStatus["platform"];

export const PLATFORM_LABELS: Record<MessagingPlatform, string> = {
  slack: "Slack",
  teams: "Microsoft Teams",
};

// One row in the live channel/chat picker (proxied from the platform).
export type TargetOption = Schemas["TargetOptionOut"];
export type TargetType = TargetOption["target_type"];

// A saved quick-share destination.
export type ShareTarget = Schemas["ShareTargetOut"];

export type ExternalShareResult = Schemas["ExternalShareOut"];
