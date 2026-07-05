"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import useSWR, { mutate } from "swr";
import { api, fetcher, type Article } from "@/lib/api";
import ArticleRow from "./ArticleRow";
import ShareModal from "./ShareModal";
import ZenRow from "./ZenRow";

export function articlesKey(opts: {
  filter: "all" | "unread" | "saved";
  feedId?: string | null;
  q?: string;
}) {
  const params = new URLSearchParams({ filter: opts.filter, limit: "100" });
  if (opts.feedId) params.set("feed_id", opts.feedId);
  if (opts.q) params.set("q", opts.q);
  return `/articles?${params.toString()}`;
}

export function mutateArticleLists() {
  mutate((key) => typeof key === "string" && key.startsWith("/articles?"));
  mutate("/feeds");
}

export default function ArticleList({
  filter,
  feedId,
  q,
  emptyTitle,
  emptySubtitle,
  variant = "list",
  refreshInterval = 0,
}: {
  filter: "all" | "unread" | "saved";
  feedId?: string | null;
  q?: string;
  emptyTitle: string;
  emptySubtitle?: string;
  variant?: "list" | "zen";
  refreshInterval?: number;
}) {
  const router = useRouter();
  const key = articlesKey({ filter, feedId, q });
  const { data: articles, isLoading } = useSWR<Article[]>(key, fetcher, {
    refreshInterval,
  });
  const [selected, setSelected] = useState(0);
  const [revealed, setRevealed] = useState<number | null>(null);
  const [sharing, setSharing] = useState<Article | null>(null);

  useEffect(() => {
    setSelected(0);
    setRevealed(null);
  }, [key]);

  const toggleSaved = useCallback(
    async (article: Article) => {
      await api(`/articles/${article.id}/state`, {
        method: "POST",
        body: { is_saved: !article.is_saved },
      });
      mutateArticleLists();
    },
    [],
  );

  const toggleRead = useCallback(async (article: Article) => {
    await api(`/articles/${article.id}/state`, {
      method: "POST",
      body: { is_read: !article.is_read },
    });
    mutateArticleLists();
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (sharing) return;
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
        setRevealed(null);
      } else if (e.key === "k") {
        setSelected((s) => Math.max(s - 1, 0));
        setRevealed(null);
      } else if (e.key === "a" && variant === "zen") {
        setRevealed((r) => (r === selected ? null : selected));
      } else if (e.key === "Enter") {
        const article = articles[selected];
        if (article) router.push(`/article/${article.id}`);
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
  }, [articles, selected, sharing, router, toggleSaved, toggleRead, variant]);

  useEffect(() => {
    document
      .querySelector(`[data-row-index="${selected}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  if (isLoading) {
    return (
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

  if (!articles || articles.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center px-8 py-28 text-center">
        <p className="text-[17px] font-medium" style={{ color: "var(--ink-dim)" }}>
          {emptyTitle}
        </p>
        {emptySubtitle && (
          <p className="mt-2 max-w-sm text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
            {emptySubtitle}
          </p>
        )}
      </div>
    );
  }

  return (
    <>
      <div className="fade-up">
        {articles.map((article, i) =>
          variant === "zen" ? (
            <ZenRow
              key={article.id}
              article={article}
              index={i}
              selected={i === selected}
              revealed={i === revealed}
            />
          ) : (
            <ArticleRow
              key={article.id}
              article={article}
              index={i}
              selected={i === selected}
              onToggleSaved={toggleSaved}
              onShare={setSharing}
            />
          ),
        )}
        <p
          className="font-mono-nr px-5 py-6 text-center text-[11px]"
          style={{ color: "var(--ink-faint)" }}
        >
          {variant === "zen"
            ? "j / k navigate · enter open · a peek summary · s save · m read"
            : "j / k to navigate · enter to open · s to save · m to toggle read"}
        </p>
      </div>
      {sharing && <ShareModal article={sharing} onClose={() => setSharing(null)} />}
    </>
  );
}
