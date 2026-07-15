"use client";

import useSWR, { mutate, type SWRConfiguration } from "swr";
import {
  fetcher,
  type AISettings,
  type AiStatus,
  type ActivitySummary,
  type ArticleDetail,
  type ArticleProjectStatus,
  type CatalogCategory,
  type CatalogEntry,
  type DislikeOptions,
  type DislikeRule,
  type EntityPage,
  type Feed,
  type IntegrationStatus,
  type Project,
  type ProjectArticle,
  type ProjectComment,
  type RelatedArticle,
  type Share,
  type ShareTarget,
  type SmartFeed,
  type UsageEvent,
  type UsageSummary,
  type UserPublic,
} from "./api";
import { keys } from "./keys";

// Thin, typed wrappers over useSWR: one hook per resource, all reading from
// the shared key registry so mutators (below) always hit the right cache.

export const useFeeds = (config?: SWRConfiguration<Feed[]>) =>
  useSWR<Feed[]>(keys.feeds, fetcher, config);

export const useProjects = (config?: SWRConfiguration<Project[]>) =>
  useSWR<Project[]>(keys.projects, fetcher, config);

export const useProject = (id: number | string) =>
  useSWR<Project>(keys.project(id), fetcher);

export const useProjectArticles = (id: number | string) =>
  useSWR<ProjectArticle[]>(keys.projectArticles(id), fetcher);

export const useProjectComments = (
  projectId: number | string,
  articleId: number,
  enabled = true,
) =>
  useSWR<ProjectComment[]>(
    enabled ? keys.projectComments(projectId, articleId) : null,
    fetcher,
  );

export const useArticleDetail = (
  id: number | string | null,
  config?: SWRConfiguration<ArticleDetail>,
) => useSWR<ArticleDetail>(id === null ? null : keys.article(id), fetcher, config);

export const useRelatedArticles = (articleId: number) =>
  useSWR<RelatedArticle[]>(keys.articleRelated(articleId), fetcher);

export const useArticleProjects = (articleId: number, enabled = true) =>
  useSWR<ArticleProjectStatus[]>(
    enabled ? keys.articleProjects(articleId) : null,
    fetcher,
  );

export const useEntityPage = (id: string | undefined) =>
  useSWR<EntityPage>(id ? keys.entity(id) : null, fetcher);

export const useAiStatus = () => useSWR<AiStatus>(keys.aiStatus, fetcher);

export const useAiSettings = () => useSWR<AISettings>(keys.aiSettings, fetcher);

export const useSharesSent = () => useSWR<Share[]>(keys.sharesSent, fetcher);

export const useSharesReceived = () =>
  useSWR<Share[]>(keys.sharesReceived, fetcher);

export const useUnseenShareCount = (
  config?: SWRConfiguration<{ count: number }>,
) => useSWR<{ count: number }>(keys.sharesUnseenCount, fetcher, config);

export const useShareTargets = () =>
  useSWR<ShareTarget[]>(keys.shareTargets, fetcher);

export const useIntegrations = () =>
  useSWR<IntegrationStatus[]>(keys.integrations, fetcher);

export const useDislikeRules = () =>
  useSWR<DislikeRule[]>(keys.dislikeRules, fetcher);

export const useDislikeOptions = (articleId: number) =>
  useSWR<DislikeOptions>(keys.dislikeOptions(articleId), fetcher);

export const useCatalogEntries = (q: string, category: string | null, sort = "name") =>
  useSWR<CatalogEntry[]>(keys.catalog(q, category, sort), fetcher);

export const useCatalogCategories = () =>
  useSWR<CatalogCategory[]>(keys.catalogCategories, fetcher);

export const useSmartFeeds = () => useSWR<SmartFeed[]>(keys.smartFeeds, fetcher);

// Debounce upstream (useDebouncedValue) and pass the settled query; SWR keying
// makes stale responses drop out naturally — no cancelled-flag effects.
export const useUserSearch = (q: string) =>
  useSWR<UserPublic[]>(q ? keys.userSearch(q) : null, fetcher);

export const useUsageSummary = (range: string) =>
  useSWR<UsageSummary>(keys.usageSummary(range), fetcher);

export const useUsageEvents = (limit: number) =>
  useSWR<UsageEvent[]>(keys.usageEvents(limit), fetcher);

export const useActivitySummary = (range: string, today: string) =>
  useSWR<ActivitySummary>(keys.activitySummary(range, today), fetcher);

// ——— related-cache mutators ———

/** A project changed: refresh it and the list (counts, membership). */
export function mutateProject(id: number | string) {
  mutate(keys.project(id));
  mutate(keys.projects);
}

/** A pin/status/comment changed inside a project: refresh everything project-scoped. */
export function mutateProjectContent(id: number | string) {
  mutate(keys.projectArticles(id));
  mutateProject(id);
}

export function mutateFeeds() {
  mutate(keys.feeds);
}

export function mutateShareTargets() {
  mutate(keys.shareTargets);
}

export function mutateIntegrations() {
  mutate(keys.integrations);
  mutate(keys.shareTargets);
}

export function mutateAiConfig() {
  mutate(keys.aiSettings);
  mutate(keys.aiStatus);
}
