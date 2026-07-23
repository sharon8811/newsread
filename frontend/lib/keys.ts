// Central SWR key registry. Every key that is both fetched and mutated lives
// here so readers and mutators can never drift apart. Article-list keys are
// the exception: their cursor/window machinery stays in components/ArticleList
// (articlesKey / mutateArticleLists).

export const keys = {
  config: "/config",

  feeds: "/feeds",
  importFeed: "/imports/feed",

  projects: "/projects",
  project: (id: number | string) => `/projects/${id}`,
  projectArticles: (id: number | string) => `/projects/${id}/articles`,
  projectComments: (projectId: number | string, articleId: number) =>
    `/projects/${projectId}/articles/by-article/${articleId}/comments`,
  projectQa: (id: number | string) => `/projects/${id}/qa`,

  article: (id: number | string) => `/articles/${id}`,
  articleRelated: (id: number) => `/articles/${id}/related`,
  articleProjects: (id: number) => `/articles/${id}/projects`,
  articleQa: (id: number) => `/articles/${id}/qa`,

  entity: (id: number | string) => `/entities/${id}`,

  aiStatus: "/ai/status",
  aiSettings: "/ai/settings",

  sharesSent: "/shares/sent",
  sharesReceived: "/shares/received",
  sharesUnseenCount: "/shares/unseen-count",
  shareTargets: "/share-targets",
  integrations: "/integrations",

  dislikeRules: "/interests/dislikes",
  dislikeOptions: (articleId: number) => `/interests/dislike-options/${articleId}`,

  catalog: (q: string, category: string | null, sort = "name") => {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (category) params.set("category", category);
    if (sort !== "name") params.set("sort", sort);
    const qs = params.toString();
    return qs ? `/catalog?${qs}` : "/catalog";
  },
  catalogCategories: "/catalog/categories",
  smartFeeds: "/catalog/smart",

  historySummary: "/history/summary",
  historyConnections: "/history/connections",
  historySettings: "/history/settings",
  historyRules: "/history/domain-rules",
  history: ({
    q,
    hostname,
    dateFrom,
    dateTo,
    sort,
  }: {
    q?: string;
    hostname?: string;
    dateFrom?: string;
    dateTo?: string;
    sort?: "recent" | "relevance";
  }) => {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (hostname) params.set("hostname", hostname);
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    if (sort && sort !== "recent") params.set("sort", sort);
    const qs = params.toString();
    return qs ? `/history?${qs}` : "/history";
  },

  userSearch: (q: string) => `/users/search?q=${encodeURIComponent(q)}`,

  usageSummary: (range: string) => `/usage/summary?range=${range}`,
  usageEvents: (limit: number) => `/usage/events?limit=${limit}`,
  activitySummary: (range: string, today: string) =>
    `/activity/summary?range=${range}&today=${today}`,
} as const;
