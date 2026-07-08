"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { api, fetcher, imageSrc, type Article } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { articlesKey, mutateArticleLists } from "./ArticleList";
import EntityBadges from "./EntityBadges";
import { BookmarkIcon, ChevronUpIcon, XIcon } from "./icons";

const FALLBACK_BG =
  "radial-gradient(120% 100% at 20% 0%, var(--accent-soft), transparent 60%), linear-gradient(160deg, #1b2130, var(--bg-inset))";

export default function StoriesView({
  feedId,
  filter = "unread",
  markOnAdvance = true,
  onExit,
}: {
  feedId?: string | null;
  filter?: "unread" | "saved";
  markOnAdvance?: boolean;
  onExit: () => void;
}) {
  const router = useRouter();
  const key = articlesKey({ filter, feedId });
  const { data, isLoading } = useSWR<Article[]>(key, fetcher);

  // Snapshot the queue: live SWR data reshuffles as items get marked read.
  const [queue, setQueue] = useState<Article[] | null>(null);
  const [index, setIndex] = useState(0);
  const [done, setDone] = useState(false);
  const snapKey = useRef<string | null>(null);

  useEffect(() => {
    if (data && snapKey.current !== key) {
      snapKey.current = key;
      setQueue(data);
      setIndex(0);
      setDone(false);
    }
  }, [data, key]);

  // One revalidation on exit covers all the cards marked read along the way.
  useEffect(() => () => mutateArticleLists(), []);

  const article = queue?.[index];

  const markRead = useCallback(
    (a: Article | undefined) => {
      if (!markOnAdvance || !a || a.is_read) return;
      setQueue((q) =>
        q ? q.map((x) => (x.id === a.id ? { ...x, is_read: true } : x)) : q,
      );
      api(`/articles/${a.id}/state`, {
        method: "POST",
        body: { is_read: true },
      }).catch(() => {});
    },
    [markOnAdvance],
  );

  const advance = useCallback(() => {
    if (!queue) return;
    markRead(queue[index]);
    if (index < queue.length - 1) setIndex(index + 1);
    else setDone(true);
  }, [queue, index, markRead]);

  const back = useCallback(() => {
    if (done) setDone(false);
    else setIndex((i) => Math.max(0, i - 1));
  }, [done]);

  const openArticle = useCallback(() => {
    if (article) router.push(`/article/${article.id}`);
  }, [article, router]);

  const toggleSaved = useCallback(() => {
    if (!article) return;
    const next = !article.is_saved;
    setQueue((q) =>
      q ? q.map((x) => (x.id === article.id ? { ...x, is_saved: next } : x)) : q,
    );
    api(`/articles/${article.id}/state`, {
      method: "POST",
      body: { is_saved: next },
    }).catch(() => {});
  }, [article]);

  // Preload the next couple of card images.
  useEffect(() => {
    if (!queue) return;
    for (const next of [queue[index + 1], queue[index + 2]]) {
      if (next?.image_url) new window.Image().src = imageSrc(next.image_url)!;
    }
  }, [queue, index]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target.isContentEditable
      )
        return;
      if (e.key === "ArrowRight" || e.key === " ") {
        if (!done) advance();
      } else if (e.key === "ArrowLeft") {
        back();
      } else if (e.key === "Enter" || e.key === "ArrowUp") {
        if (!done) openArticle();
      } else if (e.key === "Escape") {
        onExit();
      } else if (e.key === "s") {
        if (!done) toggleSaved();
      } else {
        return;
      }
      e.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [advance, back, openArticle, toggleSaved, onExit, done]);

  // Swipe up opens the article.
  const touchY = useRef<number | null>(null);

  if (isLoading || queue === null) {
    return (
      <div className="stories-scope fixed inset-0 z-50 p-6" style={{ background: "var(--bg)" }}>
        <div
          className="h-full w-full animate-pulse rounded-lg"
          style={{ background: "var(--bg-raised)" }}
        />
      </div>
    );
  }

  if (queue.length === 0 || done) {
    return (
      <div
        className="stories-scope fixed inset-0 z-50 flex flex-col items-center justify-center gap-2 text-center"
        style={{ background: "var(--bg)" }}
      >
        <p className="text-[19px] font-semibold tracking-tight" style={{ color: "var(--ink-dim)" }}>
          {queue.length === 0 ? "All caught up." : "You're up to date."}
        </p>
        <p className="font-mono-nr text-[11px]" style={{ color: "var(--ink-faint)" }}>
          {queue.length === 0
            ? filter === "saved"
              ? "Nothing saved to flip through."
              : "New stories land here as your feeds refresh."
            : `${queue.length} ${queue.length === 1 ? "story" : "stories"} read`}
        </p>
        <div className="mt-4 flex items-center gap-2">
          {done && (
            <button className="btn btn-ghost" onClick={back}>
              Go back
            </button>
          )}
          <button className="btn btn-accent" onClick={onExit}>
            Back to list
          </button>
        </div>
      </div>
    );
  }

  const a = article!;
  const summaryText = a.summary_medium || a.summary_short || a.excerpt;
  const isAiSummary = Boolean(a.summary_medium || a.summary_short);
  const compact = queue.length > 30;

  return (
    <div
      className="stories-scope fixed inset-0 z-50 select-none overflow-hidden"
      style={{ background: "var(--bg)" }}
      onTouchStart={(e) => (touchY.current = e.touches[0].clientY)}
      onTouchEnd={(e) => {
        if (
          touchY.current !== null &&
          e.changedTouches[0].clientY - touchY.current < -60
        )
          openArticle();
        touchY.current = null;
      }}
    >
      {/* Card background, keyed by index so .fade-up replays per card */}
      <div
        key={a.id}
        className="fade-up absolute inset-0 bg-cover bg-center"
        style={{
          backgroundImage: a.image_url
            ? `linear-gradient(to bottom, rgba(10,11,15,0.35), rgba(10,11,15,0.55) 55%, rgba(10,11,15,0.96)), url(${JSON.stringify(imageSrc(a.image_url))})`
            : FALLBACK_BG,
        }}
      />

      {/* Progress */}
      <div className="absolute inset-x-0 top-0 z-20 flex items-center gap-[3px] px-4 pt-3">
        {compact ? (
          <>
            <div
              className="h-[2.5px] flex-1 overflow-hidden rounded-full"
              style={{ background: "rgba(255,255,255,0.28)" }}
            >
              <div
                className="h-full rounded-full transition-[width] duration-200"
                style={{
                  width: `${((index + 1) / queue.length) * 100}%`,
                  background: "var(--accent)",
                }}
              />
            </div>
            <span
              className="font-mono-nr pl-2 text-[10.5px]"
              style={{ color: "var(--ink-dim)" }}
            >
              {index + 1} / {queue.length}
            </span>
          </>
        ) : (
          queue.map((item, i) => (
            <div
              key={item.id}
              className="h-[2.5px] flex-1 rounded-full transition-colors duration-200"
              style={{
                background:
                  i <= index ? "var(--accent)" : "rgba(255,255,255,0.28)",
              }}
            />
          ))
        )}
      </div>

      {/* Top-right controls */}
      <div className="absolute right-4 top-7 z-20 flex items-center gap-1">
        <button
          className={`icon-btn ${a.is_saved ? "active" : ""}`}
          title={a.is_saved ? "Unsave" : "Save for later"}
          onClick={toggleSaved}
        >
          <BookmarkIcon size={16} filled={a.is_saved} />
        </button>
        <button className="icon-btn" title="Exit stories (Esc)" onClick={onExit}>
          <XIcon size={16} />
        </button>
      </div>

      {/* Tap zones */}
      <div className="absolute inset-y-0 left-0 z-10 w-[30%]" onClick={back} />
      <div
        className="absolute inset-y-0 right-0 z-10 w-[70%]"
        onClick={advance}
      />

      {/* Content (clicks pass through to the tap zones except on the button) */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-20 px-6 pb-16 sm:px-12 md:px-20">
        <div key={a.id} className="fade-up mx-auto max-w-2xl">
          <p
            className="font-mono-nr mb-2 text-[11px]"
            style={{ color: "var(--ink-dim)" }}
          >
            {a.feed_title}
            {a.published_at ? ` · ${timeAgo(a.published_at)}` : ""}
            {a.is_read ? " · read" : ""}
          </p>
          <h2
            className="font-serif-nr text-[28px] leading-tight sm:text-[34px]"
            style={{ color: "var(--ink)" }}
          >
            {a.title}
          </h2>
          {a.entities.length > 0 && (
            <p className="mt-2">
              <EntityBadges entities={a.entities} max={2} />
            </p>
          )}
          {summaryText && (
            <p
              className="mt-3 line-clamp-6 text-[15px] leading-relaxed"
              style={{ color: "var(--ink-dim)" }}
            >
              {isAiSummary && (
                <span
                  className="font-mono-nr mr-1.5 text-[10px]"
                  style={{ color: "var(--accent)" }}
                >
                  ✦
                </span>
              )}
              {summaryText}
            </p>
          )}
        </div>
        <button
          className="font-mono-nr pointer-events-auto absolute inset-x-0 bottom-0 z-20 mx-auto flex w-fit flex-col items-center pb-3 text-[10.5px] transition-colors"
          style={{ color: "var(--ink-faint)" }}
          onClick={openArticle}
        >
          <ChevronUpIcon size={14} />
          read full article
        </button>
      </div>
    </div>
  );
}
