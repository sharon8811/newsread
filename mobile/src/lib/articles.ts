import { useCallback } from "react";
import useSWRInfinite from "swr/infinite";

import { apiPage, type Page } from "./api";
import type { Article } from "./types";

export type ArticleFilter = "all" | "unread" | "saved";

const PAGE_SIZE = 30;

type ArticlePage = Page<Article[]>;

/** Infinite article list over the API's keyset pagination: each page's
 * X-Next-Cursor feeds the next request, so the list stays stable while new
 * articles arrive (no duplicates or skips, unlike offsets). */
export function useArticles(filter: ArticleFilter) {
  const getKey = (_index: number, previous: ArticlePage | null) => {
    if (previous && !previous.nextCursor) return null; // reached the end
    const cursor = previous?.nextCursor
      ? `&cursor=${encodeURIComponent(previous.nextCursor)}`
      : "";
    return `/articles?filter=${filter}&limit=${PAGE_SIZE}${cursor}`;
  };
  const { data, error, size, setSize, isValidating, isLoading, mutate } = useSWRInfinite(
    getKey,
    (path: string) => apiPage<Article[]>(path),
  );

  const articles = data ? data.flatMap((page) => page.items) : [];
  const hasMore = data ? data[data.length - 1].nextCursor !== null : false;
  const loadMore = useCallback(() => {
    if (hasMore && !isValidating) setSize((current) => current + 1);
  }, [hasMore, isValidating, setSize]);

  return { articles, error, isLoading, isValidating, hasMore, loadMore, refresh: mutate };
}
