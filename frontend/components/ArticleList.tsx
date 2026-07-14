"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import useSWR, { mutate } from "swr";
import { api, fetcher, type Article } from "@/lib/api";
import {
  readingSessionKey,
  setReadingReturnAnchor,
} from "@/lib/readingSession";
import { ARTICLES_REFRESH_EVENT, useReadingWindow } from "@/lib/useReadingWindow";
import ArticleCard from "./ArticleCard";
import ArticleRow from "./ArticleRow";
import NotInterestedModal from "./NotInterestedModal";
import ProjectPickerModal from "./ProjectPickerModal";
import ShareModal from "./ShareModal";

export function articlesKey(opts: {
  filter: "all" | "unread" | "saved";
  feedId?: string | null;
  q?: string;
  anchor?: "resume";
}) {
  const params = new URLSearchParams({ filter: opts.filter, limit: "100" });
  if (opts.feedId) params.set("feed_id", opts.feedId);
  if (opts.q) params.set("q", opts.q);
  if (opts.anchor) params.set("anchor", opts.anchor);
  return `/articles?${params.toString()}`;
}

export function mutateArticleLists() {
  mutate((key) => typeof key === "string" && key.startsWith("/articles?"));
  mutate("/feeds");
  // Reading windows manage their own pages outside SWR; poke them too.
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(ARTICLES_REFRESH_EVENT));
  }
}

type ListProps = {
  filter: "all" | "unread" | "saved";
  feedId?: string | null;
  q?: string;
  emptyTitle: string;
  emptySubtitle?: string;
  variant?: "cards" | "list";
  refreshInterval?: number;
};

export default function ArticleList(props: ListProps) {
  // Search results and the saved shelf are lookup modes: flat fetch, no
  // auto-read. Everything else is the reading experience — resume anchor,
  // endless scroll both ways, scroll-past marks read.
  const readingMode = !props.q && props.filter !== "saved";
  if (readingMode) {
    return (
      <ReadingList
        key={readingSessionKey(props.filter as "all" | "unread", props.feedId)}
        {...props}
        filter={props.filter as "all" | "unread"}
      />
    );
  }
  return <QueryList {...props} />;
}

// ——— shared pieces ———

function LoadingSkeleton({ variant }: { variant: "cards" | "list" }) {
  return variant === "cards" ? (
    <div className="mx-auto flex w-full max-w-[720px] flex-col gap-6 p-4 sm:gap-7 sm:py-8">
      {[...Array(4)].map((_, i) => (
        <div
          key={i}
          className="h-[320px] rounded-lg"
          style={{ background: "var(--bg-hover)", opacity: 1 - i * 0.2 }}
        />
      ))}
    </div>
  ) : (
    <div className="flex flex-col gap-4 px-5 py-6">
      {[...Array(6)].map((_, i) => (
        <div
          key={i}
          className="h-[72px] rounded-md"
          style={{
            background: "var(--bg-hover)",
            opacity: 1 - i * 0.13,
          }}
        />
      ))}
    </div>
  );
}

function EmptyState({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="flex flex-col items-center justify-center px-8 py-28 text-center">
      <p className="text-[17px] font-medium" style={{ color: "var(--ink-dim)" }}>
        {title}
      </p>
      {subtitle && (
        <p className="mt-2 max-w-sm text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
          {subtitle}
        </p>
      )}
    </div>
  );
}

function useListKeyboard(opts: {
  articles: Article[] | null | undefined;
  selected: number;
  setSelected: (updater: (s: number) => number) => void;
  modalOpen: boolean;
  toggleSaved: (a: Article) => void;
  toggleRead: (a: Article) => void;
  openArticle: (a: Article) => void;
}) {
  const {
    articles,
    selected,
    setSelected,
    modalOpen,
    toggleSaved,
    toggleRead,
    openArticle,
  } = opts;

  // Install the handler before the painted rows become interactive. Reading
  // mode receives articles asynchronously; a passive effect leaves a brief
  // window where Enter still sees the previous empty list.
  useLayoutEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (modalOpen) return;
      const target = e.target as HTMLElement;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target.isContentEditable
      )
        return;
      if (!articles || articles.length === 0) return;

      if (e.key === "j") {
        setSelected((s) => Math.min(s + 1, articles.length - 1));
      } else if (e.key === "k") {
        setSelected((s) => Math.max(s - 1, 0));
      } else if (e.key === "Enter") {
        const article = articles[selected];
        if (article) openArticle(article);
      } else if (e.key === "s") {
        const article = articles[selected];
        if (article) toggleSaved(article);
      } else if (e.key === "m") {
        const article = articles[selected];
        if (article) toggleRead(article);
      } else {
        return;
      }
      e.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [articles, selected, modalOpen, setSelected, toggleSaved, toggleRead, openArticle]);

  useEffect(() => {
    document
      .querySelector(`[data-row-index="${selected}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [selected]);
}

function ItemModals({
  sharing,
  setSharing,
  pickingProject,
  setPickingProject,
  dismissing,
  setDismissing,
}: {
  sharing: Article | null;
  setSharing: (a: Article | null) => void;
  pickingProject: Article | null;
  setPickingProject: (a: Article | null) => void;
  dismissing: Article | null;
  setDismissing: (a: Article | null) => void;
}) {
  return (
    <>
      {sharing && <ShareModal article={sharing} onClose={() => setSharing(null)} />}
      {dismissing && (
        <NotInterestedModal article={dismissing} onClose={() => setDismissing(null)} />
      )}
      {pickingProject && (
        <ProjectPickerModal
          article={pickingProject}
          onClose={() => setPickingProject(null)}
        />
      )}
    </>
  );
}

const KEYS_HINT = "j / k to navigate · enter to open · s to save · m to toggle read";

// ——— reading mode: resume anchor + endless both ways + scroll-past reads ———

function ReadingList({
  filter,
  feedId,
  emptyTitle,
  emptySubtitle,
  variant = "list",
}: Omit<ListProps, "filter"> & { filter: "all" | "unread" }) {
  const {
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
    toggleRead,
    markOpened,
    toggleSaved,
  } = useReadingWindow({ filter, feedId, enabled: true });
  const router = useRouter();

  const [selected, setSelected] = useState(0);
  const [sharing, setSharing] = useState<Article | null>(null);
  const [pickingProject, setPickingProject] = useState<Article | null>(null);
  const [dismissing, setDismissing] = useState<Article | null>(null);

  const listRef = useRef<HTMLDivElement | null>(null);
  const scrollerRef = useRef<HTMLElement | null>(null);
  const itemEls = useRef(new Map<number, HTMLElement>());
  const passObserver = useRef<IntersectionObserver | null>(null);
  const compensation = useRef<{ height: number; top: number } | null>(null);
  const articlesLive = useRef<Article[] | null>(null);
  useEffect(() => {
    articlesLive.current = articles;
  }, [articles]);

  const key = readingSessionKey(filter, feedId);
  useEffect(() => setSelected(0), [key]);

  useLayoutEffect(() => {
    scrollerRef.current = listRef.current?.closest("main") ?? null;
  }, []);

  // Scroll-past auto-read: an item whose box fully left through the top edge
  // of the scroller has been passed. Scrolling back up never re-marks — the
  // exit event only fires on the way out.
  useEffect(() => {
    const root = scrollerRef.current;
    if (!root) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const rect = entry.boundingClientRect;
          // Unmounting elements report a zero rect; ignore them.
          if (rect.width === 0 && rect.height === 0) continue;
          const rootTop = entry.rootBounds?.top ?? 0;
          if (!entry.isIntersecting && rect.bottom <= rootTop) {
            const id = Number((entry.target as HTMLElement).dataset.articleId);
            if (id) markPassed(id);
          }
        }
      },
      { root, threshold: 0 },
    );
    passObserver.current = observer;
    itemEls.current.forEach((el) => observer.observe(el));
    return () => {
      observer.disconnect();
      passObserver.current = null;
    };
  }, [markPassed]);

  const itemRef = useCallback(
    (id: number) => (el: HTMLElement | null) => {
      const map = itemEls.current;
      const existing = map.get(id);
      if (existing && existing !== el) passObserver.current?.unobserve(existing);
      if (el) {
        map.set(id, el);
        passObserver.current?.observe(el);
      } else {
        map.delete(id);
      }
    },
    [],
  );

  // Prepending history shifts content; keep the viewport pinned to what the
  // user was looking at. overflow-anchor is disabled on the list so the
  // browser's own anchoring (absent in Safari) can't double-correct.
  useLayoutEffect(() => {
    const scroller = scrollerRef.current;
    if (compensation.current && scroller) {
      scroller.scrollTop =
        compensation.current.top +
        (scroller.scrollHeight - compensation.current.height);
    }
    compensation.current = null;
  }, [articles]);

  const openArticle = useCallback(
    (article: Article) => {
      const scroller = scrollerRef.current;
      const element = itemEls.current.get(article.id);
      const offset =
        scroller && element
          ? element.getBoundingClientRect().top - scroller.getBoundingClientRect().top
          : 0;
      setReadingReturnAnchor(key, { articleId: article.id, offset });
      markOpened(article.id);
      router.push(`/article/${article.id}`);
    },
    [key, markOpened, router],
  );

  const loadOlderCompensated = useCallback(async () => {
    const scroller = scrollerRef.current;
    const snapshot = scroller
      ? { height: scroller.scrollHeight, top: scroller.scrollTop }
      : null;
    const fetched = await loadOlder();
    if (fetched) compensation.current = snapshot;
  }, [loadOlder]);

  // Sentinels: the top one is visible right after mount, which auto-loads one
  // page of read history above the anchor (context), then again each time the
  // user scrolls back up to it. The bottom one drives ordinary infinite scroll.
  const topSentinel = useRef<HTMLDivElement | null>(null);
  const bottomSentinel = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const root = scrollerRef.current;
    const el = topSentinel.current;
    if (!root || !el || !prevCursor) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) loadOlderCompensated();
      },
      { root },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [prevCursor, loadOlderCompensated]);

  useEffect(() => {
    const root = scrollerRef.current;
    const el = bottomSentinel.current;
    if (!root || !el || !nextCursor) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) loadNewer();
      },
      { root, rootMargin: "400px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [nextCursor, loadNewer]);

  const jumpToNextUnread = useCallback(async () => {
    const scroller = scrollerRef.current;
    for (let attempt = 0; attempt < 2; attempt++) {
      const list = articlesLive.current;
      if (!list || !scroller) return;
      const rootTop = scroller.getBoundingClientRect().top;
      for (const article of list) {
        if (article.is_read) continue;
        const el = itemEls.current.get(article.id);
        if (el && el.getBoundingClientRect().top > rootTop + 60) {
          el.scrollIntoView({ behavior: "smooth", block: "start" });
          return;
        }
      }
      // Nothing unread below in the window: pull one more page and retry.
      if (attempt === 0 && nextCursor) {
        const fetched = await loadNewer();
        if (!fetched) break;
        await new Promise((resolve) => setTimeout(resolve, 50));
        continue;
      }
      break;
    }
    if (newAbove > 0) {
      await resetToTop();
      scrollerRef.current?.scrollTo({ top: 0 });
    }
  }, [nextCursor, newAbove, loadNewer, resetToTop]);

  const jumpToNew = useCallback(async () => {
    await resetToTop();
    scrollerRef.current?.scrollTo({ top: 0 });
  }, [resetToTop]);

  useListKeyboard({
    articles,
    selected,
    setSelected,
    modalOpen: Boolean(sharing || pickingProject || dismissing),
    toggleSaved,
    toggleRead,
    openArticle,
  });

  // The wrapper (and its ref) must exist from the first render — the scroll
  // container lookup and observer roots resolve in mount-time effects, before
  // the first page arrives.
  if (loading && articles === null) {
    return (
      <div ref={listRef}>
        <LoadingSkeleton variant={variant} />
      </div>
    );
  }

  if (!articles || articles.length === 0) {
    return (
      <div ref={listRef}>
        <EmptyState title={emptyTitle} subtitle={emptySubtitle} />
      </div>
    );
  }

  const items = articles.map((article, i) => {
    const shared = {
      article,
      index: i,
      selected: i === selected,
      onToggleSaved: toggleSaved,
      onShare: setSharing,
      onAddToProject: setPickingProject,
      onNotInterested: setDismissing,
      onOpen: openArticle,
    };
    return (
      <div key={article.id} ref={itemRef(article.id)} data-article-id={article.id}>
        {variant === "cards" ? <ArticleCard {...shared} /> : <ArticleRow {...shared} />}
      </div>
    );
  });

  return (
    <>
      <div className="fade-up" ref={listRef} style={{ overflowAnchor: "none" }}>
        {prevCursor && (
          <div ref={topSentinel} className="px-5 py-4 text-center">
            <span
              className="font-mono-nr text-[11px]"
              style={{ color: "var(--ink-faint)" }}
            >
              loading earlier articles…
            </span>
          </div>
        )}
        {variant === "cards" ? (
          <div className="mx-auto flex w-full max-w-[720px] flex-col gap-6 p-4 sm:gap-7 sm:py-8">
            {items}
          </div>
        ) : (
          items
        )}
        {nextCursor ? (
          <div ref={bottomSentinel} className="px-5 py-6 text-center">
            <span
              className="font-mono-nr text-[11px]"
              style={{ color: "var(--ink-faint)" }}
            >
              loading more…
            </span>
          </div>
        ) : (
          <p
            className="font-mono-nr px-5 py-6 text-center text-[11px]"
            style={{ color: "var(--ink-faint)" }}
          >
            {KEYS_HINT}
          </p>
        )}
      </div>

      {newAbove > 0 && (
        <button
          onClick={jumpToNew}
          className="fixed left-1/2 top-[120px] z-30 -translate-x-1/2 rounded-full border px-3.5 py-1.5 text-[12px] font-medium shadow-md transition-colors"
          style={{
            background: "var(--accent)",
            borderColor: "var(--accent)",
            color: "#fff",
          }}
        >
          {newAbove} new ↑
        </button>
      )}
      {unreadCount !== null && (
        <button
          onClick={jumpToNextUnread}
          title={unreadCount > 0 ? "Jump to the next unread article" : undefined}
          className="font-mono-nr fixed bottom-5 left-1/2 z-30 -translate-x-1/2 rounded-full border px-3.5 py-1.5 text-[11.5px] shadow-md transition-colors"
          style={{
            background: "var(--bg-raised)",
            borderColor: "var(--line)",
            color: unreadCount > 0 ? "var(--ink)" : "var(--ink-faint)",
          }}
        >
          {unreadCount > 0 ? `${unreadCount} unread ↓` : "All caught up ✓"}
        </button>
      )}

      <ItemModals
        sharing={sharing}
        setSharing={setSharing}
        pickingProject={pickingProject}
        setPickingProject={setPickingProject}
        dismissing={dismissing}
        setDismissing={setDismissing}
      />
    </>
  );
}

// ——— query mode: search results and the saved shelf (flat fetch, no auto-read) ———

function QueryList({
  filter,
  feedId,
  q,
  emptyTitle,
  emptySubtitle,
  variant = "list",
  refreshInterval = 0,
}: ListProps) {
  const router = useRouter();
  const key = articlesKey({ filter, feedId, q });
  // While any listed article has an AI illustration rendering, poll fast so
  // the "generating" cards resolve into images (and each poll lets the server
  // start the next few generations). Server-side pending stops reporting
  // after ~3 min, which halts the fast poll on its own.
  const { data: articles, isLoading } = useSWR<Article[]>(key, fetcher, {
    refreshInterval: (data) =>
      data?.some((a) => a.image_pending && !a.image_url) ? 3000 : refreshInterval,
  });
  const [selected, setSelected] = useState(0);
  const [sharing, setSharing] = useState<Article | null>(null);
  const [pickingProject, setPickingProject] = useState<Article | null>(null);
  const [dismissing, setDismissing] = useState<Article | null>(null);

  useEffect(() => {
    setSelected(0);
  }, [key]);

  const toggleSaved = useCallback(async (article: Article) => {
    await api(`/articles/${article.id}/state`, {
      method: "POST",
      body: { is_saved: !article.is_saved },
    });
    mutateArticleLists();
  }, []);

  const toggleRead = useCallback(async (article: Article) => {
    await api(`/articles/${article.id}/state`, {
      method: "POST",
      body: { is_read: !article.is_read },
    });
    mutateArticleLists();
  }, []);

  const openArticle = useCallback(
    (article: Article) => router.push(`/article/${article.id}`),
    [router],
  );

  useListKeyboard({
    articles,
    selected,
    setSelected,
    modalOpen: Boolean(sharing || pickingProject || dismissing),
    toggleSaved,
    toggleRead,
    openArticle,
  });

  if (isLoading) return <LoadingSkeleton variant={variant} />;

  if (!articles || articles.length === 0) {
    return <EmptyState title={emptyTitle} subtitle={emptySubtitle} />;
  }

  return (
    <>
      <div className="fade-up">
        {variant === "cards" ? (
          <div className="mx-auto flex w-full max-w-[720px] flex-col gap-6 p-4 sm:gap-7 sm:py-8">
            {articles.map((article, i) => (
              <ArticleCard
                key={article.id}
                article={article}
                index={i}
                selected={i === selected}
                onToggleSaved={toggleSaved}
                onShare={setSharing}
                onAddToProject={setPickingProject}
                onNotInterested={setDismissing}
              />
            ))}
          </div>
        ) : (
          articles.map((article, i) => (
            <ArticleRow
              key={article.id}
              article={article}
              index={i}
              selected={i === selected}
              onToggleSaved={toggleSaved}
              onShare={setSharing}
              onAddToProject={setPickingProject}
              onNotInterested={setDismissing}
            />
          ))
        )}
        <p
          className="font-mono-nr px-5 py-6 text-center text-[11px]"
          style={{ color: "var(--ink-faint)" }}
        >
          {KEYS_HINT}
        </p>
      </div>
      <ItemModals
        sharing={sharing}
        setSharing={setSharing}
        pickingProject={pickingProject}
        setPickingProject={setPickingProject}
        dismissing={dismissing}
        setDismissing={setDismissing}
      />
    </>
  );
}
