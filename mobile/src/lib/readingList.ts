// Reading-mode article list: opens anchored at the resume point (the
// server-side reading frontier), pages forward through unread, and marks
// articles read as they scroll past the top of the viewport. Marks are
// optimistic and flushed in batches, carrying the deepest passed article as
// the new frontier. Mobile is downward-only for now — no backward paging
// through read history (the web has it; FlatList prepends are fragile).

import { useCallback, useEffect, useRef, useState } from "react";
import { AppState } from "react-native";

import { apiPage, sendReadBatch } from "./api";
import type { Article } from "./types";

const PAGE_SIZE = 30;
const FLUSH_DELAY_MS = 1500;

export type ReadingFilter = "unread" | "all";

function listPath(filter: ReadingFilter, extra: Record<string, string> = {}) {
  const params = new URLSearchParams({
    filter,
    limit: String(PAGE_SIZE),
    ...extra,
  });
  return `/articles?${params.toString()}`;
}

/** Ids of unread articles that sit above the first visible index — i.e.
 * everything the user has scrolled past. Pure so it's unit-testable. */
export function passedArticleIds(
  articles: Article[],
  minVisibleIndex: number,
): number[] {
  const passed: number[] = [];
  for (let i = 0; i < Math.min(minVisibleIndex, articles.length); i++) {
    if (!articles[i].is_read) passed.push(articles[i].id);
  }
  return passed;
}

export function useReadingList(filter: ReadingFilter, enabled: boolean) {
  const [articles, setArticles] = useState<Article[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [unreadCount, setUnreadCount] = useState<number | null>(null);
  const [newAbove, setNewAbove] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const articlesRef = useRef<Article[] | null>(null);
  const pendingRef = useRef<Set<number>>(new Set());
  // Deepest list index passed this session; its article is the frontier.
  const frontierIndexRef = useRef(-1);
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadingMoreRef = useRef(false);
  const generationRef = useRef(0);

  useEffect(() => {
    articlesRef.current = articles;
  }, [articles]);

  const flush = useCallback(() => {
    if (flushTimerRef.current) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    const ids = [...pendingRef.current];
    if (ids.length === 0) return;
    pendingRef.current.clear();
    const frontier = articlesRef.current?.[frontierIndexRef.current];
    sendReadBatch({
      article_ids: ids,
      read_source: "scrolled",
      ...(frontier ? { frontier_article_id: frontier.id } : {}),
    }).catch(() => {
      // Re-queue; reads are idempotent upserts, the next flush retries.
      ids.forEach((id) => pendingRef.current.add(id));
    });
  }, []);

  const load = useCallback(
    async (opts: { anchor: boolean }) => {
      const generation = ++generationRef.current;
      setIsLoading(true);
      setError(null);
      try {
        const page = await apiPage<Article[]>(
          listPath(filter, opts.anchor ? { anchor: "resume" } : {}),
        );
        if (generation !== generationRef.current) return;
        pendingRef.current.clear();
        frontierIndexRef.current = -1;
        setArticles(page.items);
        setNextCursor(page.nextCursor);
        if (page.unreadCount !== null) setUnreadCount(page.unreadCount);
        setNewAbove(opts.anchor ? (page.newAboveCount ?? 0) : 0);
      } catch (err) {
        if (generation === generationRef.current) {
          setError(err);
          setArticles((current) => current ?? []);
        }
      } finally {
        if (generation === generationRef.current) setIsLoading(false);
      }
    },
    [filter],
  );

  useEffect(() => {
    if (!enabled) return;
    setArticles(null);
    setNextCursor(null);
    setUnreadCount(null);
    setNewAbove(0);
    load({ anchor: true });
  }, [enabled, load]);

  // Flush pending marks when the app backgrounds or the screen unmounts.
  useEffect(() => {
    if (!enabled) return;
    const sub = AppState.addEventListener("change", (state) => {
      if (state !== "active") flush();
    });
    return () => {
      sub.remove();
      flush();
    };
  }, [enabled, flush]);

  const loadMore = useCallback(async () => {
    const cursor = nextCursor;
    if (!cursor || loadingMoreRef.current) return;
    loadingMoreRef.current = true;
    const generation = generationRef.current;
    try {
      const page = await apiPage<Article[]>(listPath(filter, { cursor }));
      if (generation !== generationRef.current) return;
      setArticles((current) => {
        const seen = new Set((current ?? []).map((a) => a.id));
        return [...(current ?? []), ...page.items.filter((a) => !seen.has(a.id))];
      });
      setNextCursor(page.nextCursor);
    } catch {
      // Transient; the next end-reached retries.
    } finally {
      loadingMoreRef.current = false;
    }
  }, [nextCursor, filter]);

  /** Everything above the first visible index has been scrolled past:
   * mark it read, move the frontier, schedule a flush. */
  const markPassedUpTo = useCallback(
    (minVisibleIndex: number) => {
      const current = articlesRef.current;
      if (!enabled || !current) return;
      const passed = passedArticleIds(current, minVisibleIndex);
      if (minVisibleIndex - 1 > frontierIndexRef.current) {
        frontierIndexRef.current = minVisibleIndex - 1;
      }
      if (passed.length === 0) return;
      passed.forEach((id) => pendingRef.current.add(id));
      setArticles((list) =>
        list
          ? list.map((a) => (passed.includes(a.id) ? { ...a, is_read: true } : a))
          : list,
      );
      setUnreadCount((count) =>
        count === null ? count : Math.max(0, count - passed.length),
      );
      if (!flushTimerRef.current) {
        flushTimerRef.current = setTimeout(() => {
          flushTimerRef.current = null;
          flush();
        }, FLUSH_DELAY_MS);
      }
    },
    [enabled, flush],
  );

  // Pull-to-refresh / stories exit: flush what we know, then re-anchor.
  const refresh = useCallback(async () => {
    flush();
    await load({ anchor: true });
  }, [flush, load]);

  // The "N new" pill: jump to the very top of the list (no anchor).
  const resetToTop = useCallback(async () => {
    flush();
    await load({ anchor: false });
  }, [flush, load]);

  // While AI illustrations render for listed articles, poll and merge fields
  // in place (never reordering or adding rows mid-read).
  const hasPendingImages =
    articles?.some((a) => a.image_pending && !a.image_url) ?? false;
  useEffect(() => {
    if (!enabled || !hasPendingImages) return;
    const timer = setInterval(async () => {
      try {
        const page = await apiPage<Article[]>(
          listPath(filter, { anchor: "resume" }),
        );
        const byId = new Map(page.items.map((a) => [a.id, a]));
        setArticles((list) =>
          list
            ? list.map((a) => {
                const fresh = byId.get(a.id);
                return fresh
                  ? { ...fresh, is_read: a.is_read, is_saved: a.is_saved }
                  : a;
              })
            : list,
        );
      } catch {
        // Transient; next tick retries.
      }
    }, 4000);
    return () => clearInterval(timer);
  }, [enabled, hasPendingImages, filter]);

  return {
    articles,
    isLoading,
    error,
    nextCursor,
    unreadCount,
    newAbove,
    loadMore,
    markPassedUpTo,
    refresh,
    resetToTop,
    flush,
  };
}
