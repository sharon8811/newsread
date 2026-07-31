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
import {
  api,
  fetcher,
  imageSrc,
  type Article,
  type DislikeRuleCreated,
} from "@/lib/api";
import {
  SCROLL_JUMP_ATTR,
  SNAP_ITEM_ATTR,
  useAssistedScroll,
} from "@/lib/assistedScroll";
import { useAuth } from "@/lib/auth";
import { keys } from "@/lib/keys";
import {
  markArticleReadInReadingSessions,
  readingSessionKey,
  setReadingReturnAnchor,
} from "@/lib/readingSession";
import { ARTICLES_REFRESH_EVENT, useReadingWindow } from "@/lib/useReadingWindow";
import ArticleCard from "./ArticleCard";
import EmptyState from "./ui/EmptyState";
import ArticleRow from "./ArticleRow";
import NotInterestedModal from "./NotInterestedModal";
import ProjectPickerModal from "./ProjectPickerModal";
import ShareModal from "./ShareModal";
import { CheckIcon } from "./icons";

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
  mutate(keys.feeds);
  // Reading windows manage their own pages outside SWR; poke them too.
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(ARTICLES_REFRESH_EVENT));
  }
}

// Surgical alternative to mutateArticleLists for single-article field flips
// (read/save state): patch every cached list and the detail view in place —
// no re-downloads — and only revalidate /feeds for its server-computed unread
// counts. Reading windows keep their own in-place state and snapshots.
export function patchArticleCaches(articleId: number, patch: Partial<Article>) {
  mutate(
    (key) => typeof key === "string" && key.startsWith("/articles?"),
    (articles?: Article[]) =>
      articles?.map((a) => (a.id === articleId ? { ...a, ...patch } : a)),
    { revalidate: false },
  );
  mutate(
    keys.article(articleId),
    (article?: Article) => (article ? { ...article, ...patch } : article),
    { revalidate: false },
  );
  mutate(keys.feeds);
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
  // auto-read. Everything else is the reading experience — top-anchored cold
  // loads, cached return position, endless forward scroll, scroll-past reads.
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

// "Not interested" hides the article the moment it's clicked — the POST
// belongs to the event, not to the modal's mount (a mount effect re-fires on
// StrictMode double-render and any remount). The modal receives the in-flight
// request for its Undo bookkeeping and error display.
type PendingDismiss = { article: Article; hide: Promise<DislikeRuleCreated> };

function startDismiss(article: Article): PendingDismiss {
  const hide = api<DislikeRuleCreated>("/interests/dislikes", {
    method: "POST",
    body: { kind: "article", article_id: article.id },
  });
  hide.then(
    () => mutateArticleLists(),
    () => {}, // surfaced by the modal
  );
  return { article, hide };
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
  dismissing: PendingDismiss | null;
  setDismissing: (d: PendingDismiss | null) => void;
}) {
  return (
    <>
      {sharing && <ShareModal article={sharing} onClose={() => setSharing(null)} />}
      {dismissing && (
        <NotInterestedModal
          article={dismissing.article}
          hide={dismissing.hide}
          onClose={() => setDismissing(null)}
        />
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

// How far below the viewport upcoming card images are warmed (~8 cards), so a
// connection drop mid-session doesn't turn the next reads into empty frames.
const IMAGE_PREFETCH_MARGIN_PX = 4000;

// Row wrapper for reading mode. Owning the ref callback here keeps its
// identity stable per row (a fresh closure per parent render would make React
// detach/re-attach every row's ref and churn the scroll-past observer), and
// content-visibility skips rendering work for far-offscreen rows — the window
// is unbounded in both directions. The intrinsic size is only the pre-render
// estimate; the browser remembers real heights once painted.
function ReadingListItem({
  articleId,
  variant,
  snap,
  onElement,
  children,
}: {
  articleId: number;
  variant: "cards" | "list";
  snap: boolean;
  onElement: (id: number, el: HTMLElement | null) => void;
  children: React.ReactNode;
}) {
  const refCallback = useCallback(
    (el: HTMLElement | null) => onElement(articleId, el),
    [articleId, onElement],
  );
  return (
    <div
      ref={refCallback}
      data-article-id={articleId}
      {...(snap ? { [SNAP_ITEM_ATTR]: "" } : null)}
      style={
        snap ? { scrollSnapAlign: "start", scrollSnapStop: "always" } : undefined
      }
      className={
        variant === "cards"
          ? "[content-visibility:auto] [contain-intrinsic-size:auto_380px]"
          : "[content-visibility:auto] [contain-intrinsic-size:auto_120px]"
      }
    >
      {children}
    </div>
  );
}

// ——— reading mode: newest-first cold load + cached return + scroll-past reads ———

function ReadingList({
  filter,
  feedId,
  emptyTitle,
  emptySubtitle,
  variant = "list",
  refreshInterval = 0,
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
  } = useReadingWindow({ filter, feedId, enabled: true, refreshInterval });
  const router = useRouter();

  const [selected, setSelected] = useState(0);
  const [sharing, setSharing] = useState<Article | null>(null);
  const [pickingProject, setPickingProject] = useState<Article | null>(null);
  const [dismissing, setDismissing] = useState<PendingDismiss | null>(null);
  const [recentPassed, setRecentPassed] = useState<number[]>([]);
  const [readFeedbackError, setReadFeedbackError] = useState<string | null>(null);
  // Each scroll-past mark pops an ephemeral ✓ at the seam where the card left;
  // the counter keys the element so a rapid fling restarts the animation.
  const [readStamp, setReadStamp] = useState(0);
  const [overlayTop, setOverlayTop] = useState(120);
  const [headerHeight, setHeaderHeight] = useState(0);
  const openDismiss = useCallback((a: Article) => setDismissing(startDismiss(a)), []);

  const listRef = useRef<HTMLDivElement | null>(null);
  const scrollerRef = useRef<HTMLElement | null>(null);
  // A snapshot means this is an in-session return and its position belongs to
  // AppLayout's semantic article anchor. Without one, this is a cold load and
  // native browser scroll restoration must not reopen between article cards.
  const resetColdLoadRef = useRef(articles === null);
  const itemEls = useRef(new Map<number, HTMLElement>());
  const passObserver = useRef<IntersectionObserver | null>(null);
  const compensation = useRef<{ height: number; top: number } | null>(null);
  const articlesLive = useRef<Article[] | null>(null);
  // Read from observer and event callbacks that can fire as soon as the rows
  // paint — before a passive effect would have filled it in.
  useLayoutEffect(() => {
    articlesLive.current = articles;
  }, [articles]);

  const key = readingSessionKey(filter, feedId);
  useEffect(() => setSelected(0), [key]);

  useLayoutEffect(() => {
    scrollerRef.current = listRef.current?.closest("main") ?? null;
  }, []);

  useLayoutEffect(() => {
    if (!resetColdLoadRef.current || articles === null) return;
    resetColdLoadRef.current = false;
    const scroller = scrollerRef.current;
    if (!scroller) return;

    const reset = () => {
      scroller.scrollTop = 0;
    };
    reset();
    let secondFrame = 0;
    const firstFrame = requestAnimationFrame(() => {
      reset();
      secondFrame = requestAnimationFrame(reset);
    });
    return () => {
      cancelAnimationFrame(firstFrame);
      cancelAnimationFrame(secondFrame);
    };
  }, [articles]);

  // Scroll-past auto-read: the observer's top edge is inset by the visible
  // sticky list header, so "passed" means the whole article has left the
  // actual reading viewport rather than merely slipping behind the header.
  useEffect(() => {
    const root = scrollerRef.current;
    if (!root) return;
    let observer: IntersectionObserver | null = null;
    let lastHeaderHeight: number | null = null;
    const header = root.querySelector<HTMLElement>("[data-reading-header]");

    const connect = () => {
      const headerHeight = Math.ceil(header?.getBoundingClientRect().height ?? 0);
      setHeaderHeight(headerHeight);
      setOverlayTop(
        Math.max(
          12,
          Math.ceil(root.getBoundingClientRect().top + headerHeight + 12),
        ),
      );
      // Resize events fire constantly (mobile URL-bar show/hide among them);
      // only an actual header-height change warrants rebuilding the observer.
      if (headerHeight === lastHeaderHeight) return;
      lastHeaderHeight = headerHeight;
      // A fresh observer's first delivery is a snapshot of current positions,
      // not a scroll transition — processing it would re-mark undone articles
      // still sitting above the boundary, so it only arms the observer.
      let snapshotDelivery = true;
      observer?.disconnect();
      observer = new IntersectionObserver(
        (entries) => {
          if (snapshotDelivery) {
            snapshotDelivery = false;
            return;
          }
          for (const entry of entries) {
            const rect = entry.boundingClientRect;
            // Unmounting elements report a zero rect; ignore them.
            if (rect.width === 0 && rect.height === 0) continue;
            const rootTop =
              entry.rootBounds?.top ??
              root.getBoundingClientRect().top + headerHeight;
            if (!entry.isIntersecting && rect.bottom <= rootTop) {
              const id = Number((entry.target as HTMLElement).dataset.articleId);
              if (id && markPassed(id)) {
                setReadFeedbackError(null);
                setReadStamp((count) => count + 1);
                setRecentPassed((current) =>
                  current.includes(id) ? current : [...current, id],
                );
              }
            }
          }
        },
        {
          root,
          threshold: 0,
          rootMargin: `-${headerHeight}px 0px 0px 0px`,
        },
      );
      passObserver.current = observer;
      itemEls.current.forEach((el) => observer?.observe(el));
    };

    connect();
    const resizeObserver =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(connect);
    resizeObserver?.observe(root);
    if (header) resizeObserver?.observe(header);
    window.addEventListener("resize", connect);
    return () => {
      window.removeEventListener("resize", connect);
      resizeObserver?.disconnect();
      observer?.disconnect();
      passObserver.current = null;
    };
  }, [markPassed]);

  // Assisted scrolling is a cards-view affordance: one card is roughly one
  // screen, so stepping between them reads as turning a page. List rows are
  // ~72px tall and snapping each of them would fight ordinary scanning.
  const { user } = useAuth();
  const getScroller = useCallback(() => scrollerRef.current, []);
  const assisted = variant === "cards" && (user?.assisted_scroll ?? true);
  useAssistedScroll({ enabled: assisted, getScroller, headerHeight });

  useEffect(() => {
    if (recentPassed.length === 0 && !readFeedbackError) return;
    const timer = setTimeout(() => {
      setRecentPassed([]);
      setReadFeedbackError(null);
    }, 5000);
    return () => clearTimeout(timer);
  }, [recentPassed, readFeedbackError]);

  // The stamp outlives its animation just long enough to be cleaned up; a
  // fresh mark during that window re-keys the element and restarts it.
  useEffect(() => {
    if (readStamp === 0) return;
    const timer = setTimeout(() => setReadStamp(0), 1000);
    return () => clearTimeout(timer);
  }, [readStamp]);

  // The unread pill doubles as the running tally: when a mark drops the
  // count, re-key its label so the new number rolls in.
  const prevUnreadRef = useRef(unreadCount);
  const [pillTick, setPillTick] = useState(0);
  useEffect(() => {
    const previous = prevUnreadRef.current;
    prevUnreadRef.current = unreadCount;
    if (previous !== null && unreadCount !== null && unreadCount < previous) {
      setPillTick((tick) => tick + 1);
    }
  }, [unreadCount]);

  const handleToggleRead = useCallback(
    async (article: Article) => {
      if (article.is_read) {
        setRecentPassed((current) => current.filter((id) => id !== article.id));
      }
      try {
        await toggleRead(article);
      } catch {
        setReadFeedbackError("Could not update read state. Please try again.");
      }
    },
    [toggleRead],
  );

  // Warm upcoming card images well before they render: lazy loading plus
  // content-visibility means an image is not even requested until its card
  // nears the viewport, so a connection drop would leave the next cards
  // imageless. Generated images are served immutable, so a warmed URL comes
  // back from the HTTP cache even offline.
  const prefetchObserver = useRef<IntersectionObserver | null>(null);
  const prefetchedImages = useRef(new Set<string>());
  useEffect(() => {
    const root = scrollerRef.current;
    if (!root) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          observer.unobserve(entry.target);
          const id = Number((entry.target as HTMLElement).dataset.articleId);
          const url = articlesLive.current?.find((a) => a.id === id)?.image_url;
          const src = imageSrc(url ?? null);
          if (!src || prefetchedImages.current.has(src)) continue;
          prefetchedImages.current.add(src);
          new Image().src = src;
        }
      },
      { root, rootMargin: `0px 0px ${IMAGE_PREFETCH_MARGIN_PX}px 0px` },
    );
    prefetchObserver.current = observer;
    itemEls.current.forEach((el) => observer.observe(el));
    return () => {
      observer.disconnect();
      prefetchObserver.current = null;
    };
  }, []);

  const onItemElement = useCallback((id: number, el: HTMLElement | null) => {
    const map = itemEls.current;
    const existing = map.get(id);
    if (existing && existing !== el) {
      passObserver.current?.unobserve(existing);
      prefetchObserver.current?.unobserve(existing);
    }
    if (el) {
      map.set(id, el);
      passObserver.current?.observe(el);
      prefetchObserver.current?.observe(el);
    } else {
      map.delete(id);
    }
  }, []);

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
      // Generous look-ahead: with a 50-article page already in memory, pulling
      // the next page this early keeps a whole page of upcoming reads loaded
      // before the connection can matter.
      { root, rootMargin: "2000px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [nextCursor, loadNewer]);

  const jumpToNextUnread = useCallback(async () => {
    const scroller = scrollerRef.current;
    for (let attempt = 0; attempt < 2; attempt++) {
      const list = articlesLive.current;
      if (!list || !scroller) return;
      // "Below" means below the sticky header, not below the scroller's top
      // edge: an article aligned right under the header is the one being read,
      // so the pill has to skip it and reach the next one.
      const rootTop =
        scroller.getBoundingClientRect().top + Math.max(headerHeight, 56);
      for (const article of list) {
        if (article.is_read) continue;
        const el = itemEls.current.get(article.id);
        if (el && el.getBoundingClientRect().top > rootTop + 4) {
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
  }, [nextCursor, newAbove, headerHeight, loadNewer, resetToTop]);

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
    toggleRead: handleToggleRead,
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
      onNotInterested: openDismiss,
      onOpen: openArticle,
      onToggleRead: handleToggleRead,
      showReadState: true,
    };
    return (
      <ReadingListItem
        key={article.id}
        articleId={article.id}
        variant={variant}
        snap={assisted}
        onElement={onItemElement}
      >
        {variant === "cards" ? <ArticleCard {...shared} /> : <ArticleRow {...shared} />}
      </ReadingListItem>
    );
  });

  return (
    <>
      <div className="fade-up" ref={listRef} style={{ overflowAnchor: "none" }}>
        {prevCursor && (
          <div ref={topSentinel} className="px-5 py-4 text-center">
            <span
              className="font-mono-nr text-label"
              style={{ color: "var(--ink-faint)" }}
            >
              loading earlier articles…
            </span>
          </div>
        )}
        {variant === "cards" ? (
          <div className="mx-auto flex w-full max-w-[720px] flex-col gap-2 sm:gap-7 sm:p-4 sm:py-8">
            {items}
          </div>
        ) : (
          items
        )}
        {nextCursor ? (
          <div ref={bottomSentinel} className="px-5 py-6 text-center">
            <span
              className="font-mono-nr text-label"
              style={{ color: "var(--ink-faint)" }}
            >
              loading more…
            </span>
          </div>
        ) : (
          <p
            className="font-mono-nr px-5 py-6 text-center text-label"
            style={{ color: "var(--ink-faint)" }}
          >
            {KEYS_HINT}
          </p>
        )}
      </div>

      {newAbove > 0 && (
        <button
          onClick={jumpToNew}
          {...{ [SCROLL_JUMP_ATTR]: "" }}
          className="fixed left-1/2 z-30 -translate-x-1/2 rounded-full border px-3.5 py-1.5 text-body-sm font-medium shadow-md transition-colors"
          style={{
            top: overlayTop,
            background: "var(--accent)",
            borderColor: "var(--accent)",
            color: "#fff",
          }}
        >
          {newAbove} new ↑
        </button>
      )}
      {/* Scroll-past feedback: a transient ✓ pops at the seam where the read
          card just left and rides out after it — no bubble covering the next
          card. Undoing is the card's own read toggle, a scroll-back away. A
          visually hidden live region keeps the announcement for screen
          readers, who get no joy from the stamp. */}
      {readStamp > 0 && (
        <div
          key={readStamp}
          aria-hidden="true"
          className="pointer-events-none fixed left-1/2 z-40 -translate-x-1/2"
          style={{ top: overlayTop }}
        >
          <span
            className="read-stamp flex h-7 w-7 items-center justify-center rounded-full shadow-md"
            style={{ background: "var(--accent)", color: "#fff" }}
          >
            <CheckIcon size={14} />
          </span>
        </div>
      )}
      {recentPassed.length > 0 && (
        <span className="sr-only" role="status" aria-live="polite">
          {recentPassed.length === 1
            ? "Marked read"
            : `${recentPassed.length} marked read`}
        </span>
      )}
      {readFeedbackError && (
        <div
          className="font-mono-nr fixed left-1/2 z-40 flex max-w-[calc(100vw-24px)] -translate-x-1/2 items-center gap-2 rounded-md border px-3 py-2 text-label shadow-lg"
          style={{
            top: overlayTop + (newAbove > 0 ? 44 : 0),
            background: "var(--bg-raised)",
            borderColor: "var(--danger)",
            color: "var(--danger)",
          }}
          role="status"
          aria-live="polite"
        >
          <span className="whitespace-nowrap">{readFeedbackError}</span>
        </div>
      )}
      {unreadCount !== null && (
        <button
          onClick={jumpToNextUnread}
          {...{ [SCROLL_JUMP_ATTR]: "" }}
          title={unreadCount > 0 ? "Jump to the next unread article" : undefined}
          className="font-mono-nr fixed left-1/2 z-30 -translate-x-1/2 rounded-full border px-3.5 py-1.5 text-label shadow-md transition-colors"
          style={{
            // Clears the iOS home indicator on phones without a browser chrome.
            bottom: "calc(1.25rem + env(safe-area-inset-bottom))",
            background: "var(--bg-raised)",
            borderColor: "var(--line)",
            color: unreadCount > 0 ? "var(--ink)" : "var(--ink-faint)",
          }}
        >
          {/* Re-keyed on every decrement so the fresh count rolls in. */}
          <span key={pillTick} className="count-tick">
            {unreadCount > 0 ? `${unreadCount} unread ↓` : "All caught up ✓"}
          </span>
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
  const [dismissing, setDismissing] = useState<PendingDismiss | null>(null);
  const openDismiss = useCallback((a: Article) => setDismissing(startDismiss(a)), []);

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
    const next = !article.is_read;
    await api(`/articles/${article.id}/state`, {
      method: "POST",
      body: { is_read: next },
    });
    if (next) markArticleReadInReadingSessions(article.id);
    patchArticleCaches(article.id, { is_read: next });
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
          <div className="mx-auto flex w-full max-w-[720px] flex-col gap-2 sm:gap-7 sm:p-4 sm:py-8">
            {articles.map((article, i) => (
              <ArticleCard
                key={article.id}
                article={article}
                index={i}
                selected={i === selected}
                onToggleSaved={toggleSaved}
                onShare={setSharing}
                onAddToProject={setPickingProject}
                onNotInterested={openDismiss}
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
              onNotInterested={openDismiss}
            />
          ))
        )}
        <p
          className="font-mono-nr px-5 py-6 text-center text-label"
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
