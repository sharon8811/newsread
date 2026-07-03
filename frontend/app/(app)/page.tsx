"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import useSWR from "swr";
import ArticleList, { mutateArticleLists } from "@/components/ArticleList";
import { CheckAllIcon, RefreshIcon, SearchIcon } from "@/components/icons";
import { api, fetcher, type Feed } from "@/lib/api";

function Inbox() {
  const searchParams = useSearchParams();
  const feedId = searchParams.get("feed");
  const { data: feeds } = useSWR<Feed[]>("/feeds", fetcher);
  const feed = feedId ? feeds?.find((f) => String(f.id) === feedId) : null;

  const [tab, setTab] = useState<"unread" | "all">("unread");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [refreshing, setRefreshing] = useState(false);

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
        className="sticky top-0 z-20 border-b px-6 pb-3.5 pt-5"
        style={{
          background: "rgba(15, 13, 10, 0.88)",
          backdropFilter: "blur(10px)",
          borderColor: "var(--line-soft)",
        }}
      >
        <div className="flex items-center gap-3">
          <h1 className="font-serif-nr text-[24px] italic leading-none">
            {feed ? feed.title : "Inbox"}
          </h1>
          {feed && (
            <span className="font-mono-nr text-[11px]" style={{ color: "var(--ink-faint)" }}>
              {feed.unread_count} unread
            </span>
          )}
          <div className="ml-auto flex items-center gap-1.5">
            {feed && (
              <button className="btn btn-ghost" onClick={refresh} title="Refresh feed">
                <RefreshIcon size={14} className={refreshing ? "spinning" : undefined} />
                Refresh
              </button>
            )}
            <button className="btn btn-ghost" onClick={markAllRead} title="Mark all as read">
              <CheckAllIcon size={15} />
              Mark all read
            </button>
          </div>
        </div>

        <div className="mt-3.5 flex items-center gap-2">
          <div
            className="flex rounded-lg border p-0.5"
            style={{ borderColor: "var(--line)", background: "var(--bg-inset)" }}
          >
            {(["unread", "all"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className="rounded-md px-3.5 py-1 text-[12.5px] font-medium capitalize transition-colors"
                style={{
                  background: tab === t ? "var(--bg-hover)" : "transparent",
                  color: tab === t ? "var(--ink)" : "var(--ink-faint)",
                }}
              >
                {t}
              </button>
            ))}
          </div>
          <div className="relative ml-auto w-[240px]">
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

      <ArticleList
        filter={tab === "unread" ? "unread" : "all"}
        feedId={feedId}
        q={q}
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
