"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import useSWR, { mutate } from "swr";
import { api, fetcher, type Article } from "@/lib/api";
import ArticleCard from "./ArticleCard";
import ArticleRow from "./ArticleRow";
import ShareModal from "./ShareModal";

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
  variant?: "cards" | "list";
  refreshInterval?: number;
}) {
  const router = useRouter();
  const key = articlesKey({ filter, feedId, q });
  const { data: articles, isLoading } = useSWR<Article[]>(key, fetcher, {
    refreshInterval,
  });
  const [selected, setSelected] = useState(0);
  const [sharing, setSharing] = useState<Article | null>(null);

  useEffect(() => {
    setSelected(0);
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
      } else if (e.key === "k") {
        setSelected((s) => Math.max(s - 1, 0));
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
  }, [articles, selected, sharing, router, toggleSaved, toggleRead]);

  useEffect(() => {
    document
      .querySelector(`[data-row-index="${selected}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  if (isLoading) {
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
            />
          ))
        )}
        <p
          className="font-mono-nr px-5 py-6 text-center text-[11px]"
          style={{ color: "var(--ink-faint)" }}
        >
          j / k to navigate · enter to open · s to save · m to toggle read
        </p>
      </div>
      {sharing && <ShareModal article={sharing} onClose={() => setSharing(null)} />}
    </>
  );
}
