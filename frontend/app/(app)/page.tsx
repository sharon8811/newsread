"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import ArticleList, { mutateArticleLists } from "@/components/ArticleList";
import FeedSettingsModal from "@/components/FeedSettingsModal";
import StoriesView from "@/components/StoriesView";
import ViewSwitcher from "@/components/ViewSwitcher";
import { CheckAllIcon, GearIcon, RefreshIcon, SearchIcon } from "@/components/icons";
import { useAuth } from "@/lib/auth";
import { api, fetcher, type Feed, type ViewMode } from "@/lib/api";

const VIEW_MODES = ["list", "stories", "zen"] as const;

function pendingCountOf(feeds: Feed[] | undefined, feedId: string | null): number {
  if (!feeds) return 0;
  if (feedId) return feeds.find((f) => String(f.id) === feedId)?.pending_count ?? 0;
  return feeds.reduce((sum, f) => sum + f.pending_count, 0);
}

function Inbox() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const feedId = searchParams.get("feed");
  const { user } = useAuth();
  // Poll while any visible feed still has articles awaiting enrichment
  // (images/full text backfilled by the worker), so updates flow in smoothly
  // instead of landing in a burst on the next focus revalidation.
  const { data: feeds } = useSWR<Feed[]>("/feeds", fetcher, {
    refreshInterval: (data) => (pendingCountOf(data, feedId) > 0 ? 5000 : 0),
  });
  const feed = feedId ? feeds?.find((f) => String(f.id) === feedId) : null;
  const pendingCount = pendingCountOf(feeds, feedId);

  // In-session switches and stories-exit are plain state (this Next build's
  // router.replace can revert query-only navigations mid-transition, so the
  // URL is only read, never written). ?view= acts as a read-only deep link.
  const [localView, setLocalView] = useState<ViewMode | null>(null);
  const [viewFeedId, setViewFeedId] = useState(feedId);
  if (feedId !== viewFeedId) {
    // Adjust during render: a feed change discards the in-session view switch.
    setViewFeedId(feedId);
    setLocalView(null);
  }

  const rawParamView = searchParams.get("view");
  const paramView = VIEW_MODES.includes(rawParamView as ViewMode)
    ? (rawParamView as ViewMode)
    : null;
  const view: ViewMode =
    localView ??
    paramView ??
    (feed ? (feed.view_override ?? user?.default_view) : user?.default_view) ??
    "list";

  const [tab, setTab] = useState<"unread" | "all">("unread");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 250);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => setSearch(""), [feedId]);

  async function refresh() {
    if (!feed || refreshing) return;
    setRefreshing(true);
    try {
      await api(`/feeds/${feed.id}/refresh`, { method: "POST" });
      mutateArticleLists();
    } finally {
      setRefreshing(false);
    }
  }

  async function markAllRead() {
    await api("/articles/mark-all-read", {
      method: "POST",
      body: { feed_id: feed ? feed.id : null },
    });
    mutateArticleLists();
  }

  return (
    <>
      <header
        className="sticky top-0 z-20 border-b px-4 pb-3.5 pt-4 sm:px-6 sm:pt-5"
        style={{
          background: "var(--bg-header)",
          backdropFilter: "blur(10px)",
          borderColor: "var(--line-soft)",
        }}
      >
        <div className="flex items-center gap-3">
          <h1 className="min-w-0 truncate text-[20px] font-semibold leading-none tracking-tight">
            {feed ? feed.title : "Inbox"}
          </h1>
          {feed && (
            <span
              className="font-mono-nr whitespace-nowrap text-[11px]"
              style={{ color: "var(--ink-faint)" }}
            >
              {feed.unread_count} unread
            </span>
          )}
          {pendingCount > 0 && (
            <span
              className="font-mono-nr flex items-center gap-1.5 whitespace-nowrap text-[11px]"
              style={{ color: "var(--accent)" }}
              title="Images and summaries are being fetched in the background"
            >
              <RefreshIcon size={11} className="spinning" />
              enriching {pendingCount} article{pendingCount === 1 ? "" : "s"}…
            </span>
          )}
          <div className="ml-auto flex shrink-0 items-center gap-1.5">
            {feed && (
              <button className="btn btn-ghost" onClick={refresh} title="Refresh feed">
                <RefreshIcon size={14} className={refreshing ? "spinning" : undefined} />
                <span className="hidden sm:inline">Refresh</span>
              </button>
            )}
            {feed && (
              <button
                className="btn btn-ghost"
                onClick={() => setSettingsOpen(true)}
                title="Feed settings"
              >
                <GearIcon size={14} />
                <span className="hidden sm:inline">Settings</span>
              </button>
            )}
            <button className="btn btn-ghost" onClick={markAllRead} title="Mark all as read">
              <CheckAllIcon size={15} />
              <span className="hidden sm:inline">Mark all read</span>
            </button>
          </div>
        </div>

        <div className="mt-3.5 flex flex-wrap items-center gap-2">
          {view !== "stories" && (
            <div
              className="flex rounded-md border p-0.5"
              style={{ borderColor: "var(--line)", background: "var(--bg-inset)" }}
            >
              {(["unread", "all"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className="rounded px-3.5 py-1 text-[12.5px] font-medium capitalize transition-colors"
                  style={{
                    background: tab === t ? "var(--bg-raised)" : "transparent",
                    color: tab === t ? "var(--ink)" : "var(--ink-faint)",
                    boxShadow: tab === t ? "0 1px 2px rgba(28,30,34,0.08)" : "none",
                  }}
                >
                  {t}
                </button>
              ))}
            </div>
          )}
          <ViewSwitcher view={view} feed={feed ?? null} onSwitch={setLocalView} />
          <div className="relative w-full sm:ml-auto sm:w-[240px]">
            <SearchIcon
              size={13}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2"
            />
            <input
              className="input"
              style={{ paddingLeft: 32, fontSize: 13, paddingTop: 6, paddingBottom: 6 }}
              placeholder="Search articles…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>
      </header>

      {view === "stories" ? (
        <StoriesView feedId={feedId} onExit={() => setLocalView("list")} />
      ) : (
        <ArticleList
          variant={view === "zen" ? "zen" : "list"}
          filter={tab === "unread" ? "unread" : "all"}
          feedId={feedId}
          q={q}
          refreshInterval={pendingCount > 0 ? 4000 : 0}
          emptyTitle={
            q
              ? "Nothing matches your search."
              : tab === "unread"
                ? "All caught up."
                : "No articles yet."
          }
          emptySubtitle={
            q
              ? undefined
              : tab === "unread"
                ? "New articles land here as your feeds refresh."
                : "Subscribe to a feed from the sidebar to start reading."
          }
        />
      )}
      {settingsOpen && feed && (
        <FeedSettingsModal
          feed={feed}
          onClose={() => setSettingsOpen(false)}
          onUnsubscribed={() => router.push("/")}
        />
      )}
    </>
  );
}

export default function InboxPage() {
  return (
    <Suspense fallback={null}>
      <Inbox />
    </Suspense>
  );
}
