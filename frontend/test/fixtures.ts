import type {
  Article,
  ArticleDetail,
  ArticleProjectStatus,
  CatalogEntry,
  CatalogPreview,
  CoverageSynthesis,
  DislikeOptions,
  DislikeRule,
  EntityBadge,
  EntityFull,
  Feed,
  Project,
  ProjectArticle,
  ProjectComment,
  RelatedArticle,
  Share,
  SmartFeed,
  User,
  UserPublic,
} from "@/lib/api";

export function makeUser(over: Partial<User> = {}): User {
  return { id: 1, email: "a@b.c", username: "alice", name: "Alice", default_view: "list", ...over };
}

export function makePublic(over: Partial<UserPublic> = {}): UserPublic {
  return { id: 2, username: "bob", name: "Bob", ...over };
}

export function makeFeed(over: Partial<Feed> = {}): Feed {
  return {
    id: 1,
    url: "https://feed.example/rss",
    title: "Tech Feed",
    site_url: "https://feed.example",
    description: "desc",
    last_fetched_at: "2024-01-01T00:00:00Z",
    article_count: 10,
    unread_count: 3,
    pending_count: 0,
    view_override: null,
    title_override: null,
    sort_order: null,
    retention_days: null,
    is_muted: false,
    ai_enabled: true,
    image_gen_enabled: true,
    refresh_interval_minutes: 15,
    ...over,
  };
}

export function makeCatalogEntry(over: Partial<CatalogEntry> = {}): CatalogEntry {
  return {
    id: 1,
    url: "https://blog.example/rss",
    title: "Example Blog",
    description: "A blog about examples",
    site_url: "https://blog.example",
    category: "Tech",
    feed_id: null,
    subscribed: false,
    source_host: "example.com",
    content_type: "application/rss+xml",
    health_status: "healthy",
    item_count: 12,
    latest_item_at: "2026-07-10T12:00:00Z",
    preview_items: [],
    subscriber_count: 0,
    match_reason: null,
    ...over,
  };
}

export function makeCatalogPreview(over: Partial<CatalogPreview> = {}): CatalogPreview {
  return {
    title: "Example Blog",
    description: "A blog about examples",
    site_url: "https://blog.example",
    fetched_at: "2026-07-12T09:00:00Z",
    items: [
      {
        title: "Fresh story",
        url: "https://blog.example/fresh",
        author: "Ann Author",
        published_at: "2026-07-12T08:00:00Z",
        summary: "A short plain-text summary of the story.",
      },
      {
        title: "Undated story",
        url: "https://blog.example/undated",
        author: null,
        published_at: null,
        summary: null,
      },
    ],
    ...over,
  };
}

export function makeSmartFeed(over: Partial<SmartFeed> = {}): SmartFeed {
  return {
    key: "reddit",
    name: "Reddit",
    description: "Follow any subreddit as a feed of its newest posts.",
    site_url: "https://www.reddit.com",
    category: "Communities",
    topic_label: "Subreddit",
    topic_hint: "programming, or paste reddit.com/r/programming",
    example_topics: ["programming", "science"],
    ...over,
  };
}

export function makeEntity(over: Partial<EntityBadge> = {}): EntityBadge {
  return {
    id: 1,
    kind: "github",
    key: "a/b",
    url: "https://github.com/a/b",
    source: "primary",
    badge: { label: "a/b", stars: 1200, language: "Python", license: "MIT" },
    ...over,
  };
}

export function makeArticle(over: Partial<Article> = {}): Article {
  return {
    id: 1,
    feed_id: 1,
    feed_title: "Tech Feed",
    title: "A Great Article",
    url: "https://site.example/story",
    comments_url: null,
    author: "Reporter",
    published_at: "2024-01-01T00:00:00Z",
    excerpt: "an excerpt",
    image_url: null,
    enriching: false,
    image_pending: false,
    is_read: false,
    is_saved: false,
    summary: "",
    summary_short: "",
    summary_medium: "",
    entities: [],
    ...over,
  };
}

export function makeArticleDetail(over: Partial<ArticleDetail> = {}): ArticleDetail {
  return {
    ...makeArticle(),
    content_html: "<p>full body</p>",
    summary_model: null,
    entities: [],
    ...over,
  } as ArticleDetail;
}

export function makeEntityFull(over: Partial<EntityFull> = {}): EntityFull {
  return {
    ...makeEntity(),
    data: { full_name: "a/b", stargazers_count: 1200 },
    fetched_at: "2024-01-01T00:00:00Z",
    deltas: {},
    snapshots: [],
    ...over,
  } as EntityFull;
}

export function makeProject(over: Partial<Project> = {}): Project {
  const owner = makePublic({ id: 1, username: "alice", name: "Alice" });
  return {
    id: 1,
    name: "AI Research",
    description: "",
    owner,
    my_role: "owner",
    members: [{ user: owner, role: "owner" }],
    article_count: 0,
    unseen_count: 0,
    is_muted: false,
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

export function makeProjectArticle(over: Partial<ProjectArticle> = {}): ProjectArticle {
  return {
    id: 1,
    project_id: 1,
    article: makeArticle(),
    added_by: makePublic({ id: 1, username: "alice", name: "Alice" }),
    is_shared: true,
    shared_at: "2026-01-02T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
    status: "open",
    status_updated_by: null,
    comment_count: 0,
    ...over,
  };
}

export function makeProjectComment(over: Partial<ProjectComment> = {}): ProjectComment {
  return {
    id: 1,
    author: makePublic({ id: 1, username: "alice", name: "Alice" }),
    body: "a thought",
    link_url: null,
    created_at: "2026-01-03T00:00:00Z",
    ...over,
  };
}

export function makeProjectStatus(
  over: Partial<ArticleProjectStatus> = {},
): ArticleProjectStatus {
  return {
    project_id: 1,
    project_name: "AI Research",
    project_article_id: null,
    is_shared: null,
    shared_by_others: false,
    suggested: false,
    ...over,
  };
}

export function makeShare(over: Partial<Share> = {}): Share {
  return {
    id: 1,
    article: makeArticle(),
    from_user: makePublic({ id: 3, username: "carol", name: "Carol" }),
    to_users: [makePublic({ id: 2, username: "bob", name: "Bob" })],
    note: null,
    created_at: "2024-01-01T00:00:00Z",
    seen_at: null,
    ...over,
  };
}

export function makeIntegration(
  over: Partial<import("@/lib/api").IntegrationStatus> = {},
): import("@/lib/api").IntegrationStatus {
  return {
    platform: "slack",
    configured: true,
    connected: false,
    status: null,
    workspace_name: null,
    account_name: null,
    ...over,
  };
}

export function makeShareTarget(
  over: Partial<import("@/lib/api").ShareTarget> = {},
): import("@/lib/api").ShareTarget {
  return {
    id: 1,
    platform: "slack",
    external_id: "C1",
    display_name: "#general",
    target_type: "channel",
    meta: {},
    last_used_at: null,
    ...over,
  };
}

export function makeDislikeRule(over: Partial<DislikeRule> = {}): DislikeRule {
  return {
    id: 1,
    kind: "topic",
    label: "crypto prices",
    phrase: "crypto prices",
    entity_id: null,
    article_id: null,
    expires_at: null,
    hidden_count: 3,
    created_at: "2024-01-01T00:00:00Z",
    ...over,
  };
}

export function makeDislikeOptions(over: Partial<DislikeOptions> = {}): DislikeOptions {
  return {
    entities: [{ entity_id: 5, kind: "github", key: "acme/widget", label: "acme/widget" }],
    topics: ["crypto prices", "celebrity gossip"],
    story_available: true,
    ...over,
  };
}

export function makeRelatedArticle(over: Partial<RelatedArticle> = {}): RelatedArticle {
  return {
    id: 7,
    title: "Related headline",
    feed_title: "Other Feed",
    published_at: "2024-01-02T00:00:00Z",
    is_read: false,
    tier: "related",
    ...over,
  };
}

export function makeSynthesis(over: Partial<CoverageSynthesis> = {}): CoverageSynthesis {
  return {
    overview: "The overall picture [1][2].",
    timeline: [
      { when: "May 1", what: "it started [1]" },
      { when: "May 3", what: "it escalated [2]" },
    ],
    timeline_raw: null,
    perspectives: "- [2] frames it differently",
    sources: [
      { n: 1, id: 1, title: "A Great Article" },
      { n: 2, id: 7, title: "Related headline" },
    ],
    ...over,
  };
}
