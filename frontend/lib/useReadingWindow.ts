"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { mutate } from "swr";
import { api, apiWithHeaders, sendReadBatch, type Article } from "./api";
import { keys } from "./keys";
import {
  getReadingReturnAnchor,
  getReadingSession,
  markArticleReadInReadingSessions,
  readingSessionKey,
  setReadingSession,
} from "./readingSession";

// Reading-mode window over the article list: pages backward through read
// history and forward through unread, and marks articles read as they scroll
// past. Marks are optimistic and flushed in batches; the deepest article
// passed travels along as the new frontier.
//
// Every cold list load starts at the configured top of the scope (newest for
// the inbox). In-memory snapshots preserve the exact window while navigating
// to an article and back, but a browser reload must never reopen at a stale
// server-side reading frontier or halfway through a card.

const PAGE_SIZE = 50;
const FLUSH_DELAY_MS = 1500;
// Dispatched by mutateArticleLists(); reading windows re-anchor on it.
export const ARTICLES_REFRESH_EVENT = "newsread:articles-refresh";

type WindowOpts = {
  filter: "all" | "unread";
  feedId?: string | null;
  enabled: boolean;
};

function listPath(
  opts: WindowOpts,
  extra: Record<string, string> = {},
): string {
  const params = new URLSearchParams({
    filter: opts.filter,
    limit: String(PAGE_SIZE),
    reading_window: "true",
    ...extra,
  });
  if (opts.feedId) params.set("feed_id", opts.feedId);
  return `/articles?${params.toString()}`;
}

export function useReadingWindow(opts: WindowOpts) {
  const { filter, feedId, enabled } = opts;
  const key = listPath({ filter, feedId, enabled });
  const sessionKey = readingSessionKey(filter, feedId);
  // The snapshot exists to make article detail → Back seamless. Ordinary app
  // navigation (Inbox → Sent → Inbox) must fetch the current unread page
  // instead of reviving an old window whose leading rows are already read.
  const initialSession = getReadingReturnAnchor(sessionKey)
    ? getReadingSession(sessionKey)
    : null;

  const [articles, setArticles] = useState<Article[] | null>(initialSession?.articles ?? null);
  const [prevCursor, setPrevCursor] = useState<string | null>(
    initialSession?.prevCursor ?? null,
  );
  const [nextCursor, setNextCursor] = useState<string | null>(
    initialSession?.nextCursor ?? null,
  );
  const [unreadCount, setUnreadCount] = useState<number | null>(
    initialSession?.unreadCount ?? null,
  );
  const [newAbove, setNewAbove] = useState(initialSession?.newAbove ?? 0);
  const [loading, setLoading] = useState(initialSession === null);

  const articlesRef = useRef<Article[] | null>(articles);
  const stateSessionKeyRef = useRef(sessionKey);
  useEffect(() => {
    articlesRef.current = articles;
  }, [articles]);

  useEffect(() => {
    if (
      !enabled ||
      articles === null ||
      stateSessionKeyRef.current !== sessionKey
    )
      return;
    setReadingSession(sessionKey, {
      articles,
      prevCursor,
      nextCursor,
      unreadCount,
      newAbove,
    });
  }, [enabled, sessionKey, articles, prevCursor, nextCursor, unreadCount, newAbove]);
  const pendingRef = useRef<Set<number>>(new Set());
  const undoneRef = useRef<Set<number>>(new Set());
  const batchWriteByArticleRef = useRef<Map<number, Promise<Response>>>(new Map());
  const frontierRef = useRef<number | null>(null);
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadingOlderRef = useRef(false);
  const loadingNewerRef = useRef(false);
  // Guards stale async responses after a key change or manual reload.
  const generationRef = useRef(0);

  const feedIdRef = useRef(feedId);
  useEffect(() => {
    feedIdRef.current = feedId;
  }, [feedId]);

  const flush = useCallback((keepalive = false) => {
    if (flushTimerRef.current) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    const ids = [...pendingRef.current];
    if (ids.length === 0) return;
    pendingRef.current.clear();
    const feed = feedIdRef.current;
    const request = sendReadBatch(
      {
        article_ids: ids,
        read_source: "scrolled",
        ...(frontierRef.current !== null
          ? {
              frontier_article_id: frontierRef.current,
              ...(feed ? { frontier_feed_id: Number(feed) } : {}),
            }
          : {}),
      },
      { keepalive },
    ).then((response) => {
      if (!response.ok) throw new Error("Could not save read state");
      return response;
    });
    ids.forEach((id) => batchWriteByArticleRef.current.set(id, request));
    request
      .then(() => mutate(keys.feeds))
      .catch(() => {
        // Re-queue so the next flush retries; reads are idempotent upserts.
        ids.forEach((id) => {
          if (!undoneRef.current.has(id)) pendingRef.current.add(id);
        });
      })
      .finally(() => {
        ids.forEach((id) => {
          if (batchWriteByArticleRef.current.get(id) === request) {
            batchWriteByArticleRef.current.delete(id);
          }
        });
      });
  }, []);

  const applyLocalReadState = useCallback((ids: number[], isRead: boolean) => {
    const wanted = new Set(ids);
    const current = articlesRef.current;
    if (!current) return [];
    const changed = current
      .filter((article) => wanted.has(article.id) && article.is_read !== isRead)
      .map((article) => article.id);
    if (changed.length === 0) return changed;
    const changedSet = new Set(changed);
    const apply = (list: Article[]) =>
      list.map((article) =>
        changedSet.has(article.id) ? { ...article, is_read: isRead } : article,
      );
    // Functional update: a page prepend/append queued in the same tick must
    // not be clobbered by a snapshot of the pre-update list.
    articlesRef.current = apply(current);
    setArticles((list) => (list ? apply(list) : list));
    setUnreadCount((count) =>
      count === null
        ? count
        : Math.max(0, count + (isRead ? -changed.length : changed.length)),
    );
    return changed;
  }, []);

  const waitForBatchWrites = useCallback(async (ids: number[]) => {
    const writes = [
      ...new Set(
        ids
          .map((id) => batchWriteByArticleRef.current.get(id))
          .filter((request): request is Promise<Response> => Boolean(request)),
      ),
    ];
    await Promise.allSettled(writes);
  }, []);

  const applyPage = useCallback(
    (
      page: { data: Article[]; headers: Headers },
      mode: "replace" | "prepend" | "append",
    ) => {
      const next = page.headers.get("X-Next-Cursor");
      const prev = page.headers.get("X-Prev-Cursor");
      const unread = page.headers.get("X-Unread-Count");
      const above = page.headers.get("X-New-Above-Count");
      setArticles((current) => {
        if (mode === "replace" || current === null) return page.data;
        const seen = new Set(current.map((a) => a.id));
        const fresh = page.data.filter((a) => !seen.has(a.id));
        return mode === "prepend" ? [...fresh, ...current] : [...current, ...fresh];
      });
      if (mode !== "prepend") setNextCursor(next);
      if (mode !== "append") setPrevCursor(prev);
      if (unread !== null) setUnreadCount(Number(unread));
      if (above !== null) setNewAbove(Number(above));
    },
    [],
  );

  const reload = useCallback(async () => {
    const generation = ++generationRef.current;
    setLoading(true);
    try {
      const page = await apiWithHeaders<Article[]>(
        listPath({ filter, feedId, enabled }),
      );
      if (generation !== generationRef.current) return;
      pendingRef.current.clear();
      frontierRef.current = null;
      applyPage(page, "replace");
    } catch {
      if (generation === generationRef.current) setArticles((a) => a ?? []);
    } finally {
      if (generation === generationRef.current) setLoading(false);
    }
  }, [filter, feedId, enabled, applyPage]);

  // Initial load + reload on scope change.
  useEffect(() => {
    if (!enabled) return;
    const session = getReadingReturnAnchor(sessionKey)
      ? getReadingSession(sessionKey)
      : null;
    stateSessionKeyRef.current = sessionKey;
    if (session) {
      setArticles(session.articles);
      setPrevCursor(session.prevCursor);
      setNextCursor(session.nextCursor);
      setUnreadCount(session.unreadCount);
      setNewAbove(session.newAbove);
      setLoading(false);
      return;
    }
    setArticles(null);
    setPrevCursor(null);
    setNextCursor(null);
    setUnreadCount(null);
    setNewAbove(0);
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, sessionKey, enabled]);

  // Re-anchor when something else invalidates article lists (mark all read,
  // feed refresh, subscribe…). Flush first so our own marks aren't lost.
  useEffect(() => {
    if (!enabled) return;
    const onRefresh = () => {
      flush();
      reload();
    };
    window.addEventListener(ARTICLES_REFRESH_EVENT, onRefresh);
    return () => window.removeEventListener(ARTICLES_REFRESH_EVENT, onRefresh);
  }, [enabled, flush, reload]);

  // Flush pending marks when the tab hides or the component unmounts —
  // keepalive lets the request survive navigation to the article page.
  useEffect(() => {
    if (!enabled) return;
    const onHide = () => {
      if (document.visibilityState === "hidden") flush(true);
    };
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", onHide);
    return () => {
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("pagehide", onHide);
      flush(true);
    };
  }, [enabled, flush]);

  // Both loaders resolve true only when a page was actually applied, so the
  // caller knows whether to expect a DOM change (scroll compensation).
  const loadOlder = useCallback(async (): Promise<boolean> => {
    const cursor = prevCursor;
    if (!cursor || loadingOlderRef.current) return false;
    loadingOlderRef.current = true;
    const generation = generationRef.current;
    try {
      const page = await apiWithHeaders<Article[]>(
        listPath({ filter, feedId, enabled }, { cursor, direction: "before" }),
      );
      if (generation !== generationRef.current) return false;
      applyPage(page, "prepend");
      return true;
    } catch {
      return false;
    } finally {
      loadingOlderRef.current = false;
    }
  }, [prevCursor, filter, feedId, enabled, applyPage]);

  const loadNewer = useCallback(async (): Promise<boolean> => {
    const cursor = nextCursor;
    if (!cursor || loadingNewerRef.current) return false;
    loadingNewerRef.current = true;
    const generation = generationRef.current;
    try {
      const page = await apiWithHeaders<Article[]>(
        listPath({ filter, feedId, enabled }, { cursor }),
      );
      if (generation !== generationRef.current) return false;
      applyPage(page, "append");
      return true;
    } catch {
      return false;
    } finally {
      loadingNewerRef.current = false;
    }
  }, [nextCursor, filter, feedId, enabled, applyPage]);

  // Jump back to the very top of the list (the "N new" pill): plain first
  // page, no anchor. Passing the new arrivals afterwards moves the frontier
  // naturally.
  const resetToTop = useCallback(async () => {
    const generation = ++generationRef.current;
    setLoading(true);
    try {
      const page = await apiWithHeaders<Article[]>(listPath({ filter, feedId, enabled }));
      if (generation !== generationRef.current) return;
      applyPage(page, "replace");
      setPrevCursor(null);
      setNewAbove(0);
    } finally {
      if (generation === generationRef.current) setLoading(false);
    }
  }, [filter, feedId, enabled, applyPage]);

  // An article scrolled past the top edge: optimistic read + queued flush.
  const markPassed = useCallback(
    (id: number): boolean => {
      const current = articlesRef.current;
      const article = current?.find((a) => a.id === id);
      if (!article || article.is_read) return false;
      applyLocalReadState([id], true);
      undoneRef.current.delete(id);
      pendingRef.current.add(id);
      // The frontier is the deepest article passed, in list order.
      if (current) {
        const frontierIndex =
          frontierRef.current === null
            ? -1
            : current.findIndex((a) => a.id === frontierRef.current);
        if (current.findIndex((a) => a.id === id) > frontierIndex) {
          frontierRef.current = id;
        }
      }
      if (!flushTimerRef.current) {
        flushTimerRef.current = setTimeout(() => flush(), FLUSH_DELAY_MS);
      }
      return true;
    },
    [applyLocalReadState, flush],
  );

  // Undo can race the delayed auto-read flush. Remove marks that have not
  // left yet; if a batch is already in flight, wait and then write unread so
  // the user's correction is always the final server state.
  const undoPassed = useCallback(
    async (ids: number[]) => {
      const uniqueIds = [...new Set(ids)];
      uniqueIds.forEach((id) => {
        pendingRef.current.delete(id);
        undoneRef.current.add(id);
      });
      const changed = applyLocalReadState(uniqueIds, false);
      if (changed.length === 0) return;
      await waitForBatchWrites(changed);
      try {
        await api("/articles/state/batch", {
          method: "POST",
          body: { article_ids: changed, is_read: false, read_source: "scrolled" },
        });
        mutate(keys.feeds);
      } catch (error) {
        changed.forEach((id) => {
          undoneRef.current.delete(id);
          pendingRef.current.add(id);
        });
        applyLocalReadState(changed, true);
        if (!flushTimerRef.current) {
          flushTimerRef.current = setTimeout(() => flush(), FLUSH_DELAY_MS);
        }
        throw error;
      }
    },
    [applyLocalReadState, flush, waitForBatchWrites],
  );

  // Manual toggles (m key, row actions) keep their immediate single-article
  // semantics; the window state updates in place instead of a full refetch.
  const toggleRead = useCallback(
    async (article: Article) => {
      const current = articlesRef.current?.find((item) => item.id === article.id);
      if (!current) return;
      const next = !current.is_read;
      pendingRef.current.delete(article.id);
      if (next) undoneRef.current.delete(article.id);
      else undoneRef.current.add(article.id);
      const changed = applyLocalReadState([article.id], next);
      if (changed.length === 0) return;
      if (!next) await waitForBatchWrites(changed);
      try {
        await api(`/articles/${article.id}/state`, {
          method: "POST",
          body: { is_read: next },
        });
        mutate(keys.feeds);
      } catch (error) {
        applyLocalReadState(changed, !next);
        if (!next) {
          changed.forEach((id) => {
            undoneRef.current.delete(id);
            pendingRef.current.add(id);
          });
          if (!flushTimerRef.current) {
            flushTimerRef.current = setTimeout(() => flush(), FLUSH_DELAY_MS);
          }
        }
        throw error;
      }
    },
    [applyLocalReadState, flush, waitForBatchWrites],
  );

  const markOpened = useCallback((articleId: number) => {
    const article = articlesRef.current?.find((item) => item.id === articleId);
    if (!article || article.is_read) return;
    markArticleReadInReadingSessions(articleId);
    setArticles((list) =>
      list
        ? list.map((item) =>
            item.id === articleId ? { ...item, is_read: true } : item,
          )
        : list,
    );
    setUnreadCount((count) => (count === null ? count : Math.max(0, count - 1)));
  }, []);

  const toggleSaved = useCallback(async (article: Article) => {
    const next = !article.is_saved;
    setArticles((list) =>
      list ? list.map((a) => (a.id === article.id ? { ...a, is_saved: next } : a)) : list,
    );
    await api(`/articles/${article.id}/state`, {
      method: "POST",
      body: { is_saved: next },
    });
  }, []);

  // While AI illustrations render for windowed articles, poll and merge the
  // fresh fields in place (never reordering or adding rows mid-read).
  const hasPendingImages =
    articles?.some((a) => a.image_pending && !a.image_url) ?? false;
  useEffect(() => {
    if (!enabled || !hasPendingImages) return;
    const timer = setInterval(async () => {
      try {
        const page = await apiWithHeaders<Article[]>(
          listPath({ filter, feedId, enabled }),
        );
        const byId = new Map(page.data.map((a) => [a.id, a]));
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
  }, [enabled, hasPendingImages, filter, feedId]);

  return {
    articles,
    loading,
    prevCursor,
    nextCursor,
    unreadCount,
    newAbove,
    loadOlder,
    loadNewer,
    resetToTop,
    markPassed,
    undoPassed,
    toggleRead,
    markOpened,
    toggleSaved,
    reload,
  };
}
