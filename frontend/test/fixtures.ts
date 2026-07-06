import type {
  Article,
  ArticleDetail,
  ArticleProjectStatus,
  EntityBadge,
  EntityFull,
  Feed,
  Project,
  ProjectArticle,
  Share,
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
    refresh_interval_minutes: 15,
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
    note: null,
    created_at: "2026-01-01T00:00:00Z",
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
