"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import useSWR from "swr";

import { CommentIcon, ExternalIcon, RefreshIcon } from "@/components/icons";
import type { ArticleDetail } from "@/lib/api";
import {
  discussionRefFor,
  fetchHNItem,
  fetchHNThread,
  type DiscussionComment,
  type DiscussionRef,
  type DiscussionSnapshot,
  type HNItem,
} from "@/lib/discussions";
import { timeAgo } from "@/lib/format";

function useStory(ref: DiscussionRef | null) {
  return useSWR<HNItem>(
    ref ? ["hackernews-story", ref.id] : null,
    () => fetchHNItem(ref!.id, { fresh: true }),
    { revalidateOnFocus: true, dedupingInterval: 30_000 },
  );
}

export type DiscussionDraft = { key: string; text: string };

export function HackerNewsDiscussionLink({ article }: { article: ArticleDetail }) {
  if (!discussionRefFor(article)) return null;
  return (
    <a className="btn min-h-11 justify-center" href="#hacker-news-discussion">
      <CommentIcon size={14} />
      Jump to discussion
    </a>
  );
}

function CommentBranch({
  comment,
  childrenByParent,
  depth,
  onDraft,
}: {
  comment: DiscussionComment;
  childrenByParent: Map<number, DiscussionComment[]>;
  depth: number;
  onDraft: (comment: DiscussionComment) => void;
}) {
  const replies = childrenByParent.get(comment.id) ?? [];
  return (
    <article
      className={depth > 0 ? "mt-4 border-l pl-4" : "py-5"}
      style={{ borderColor: "var(--line-soft)" }}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 font-mono-nr text-[11.5px]">
        <a
          href={`https://news.ycombinator.com/item?id=${comment.id}`}
          target="_blank"
          rel="noreferrer"
          className="font-medium"
          style={{ color: "var(--ink-dim)" }}
        >
          {comment.author ?? (comment.deleted ? "deleted" : "unknown")}
        </a>
        {comment.created_at && (
          <span style={{ color: "var(--ink-faint)" }}>{timeAgo(comment.created_at)}</span>
        )}
        <button
          className="ml-auto text-[11.5px] hover:underline"
          style={{ color: "var(--accent)" }}
          onClick={() => onDraft(comment)}
        >
          Draft reply
        </button>
      </div>
      <p
        className="mt-2 whitespace-pre-wrap text-[14px] leading-[1.55]"
        style={{ color: comment.dead ? "var(--ink-faint)" : "var(--ink)" }}
      >
        {comment.text || (comment.deleted ? "[deleted]" : "[no visible text]")}
      </p>
      {replies.map((reply) => (
        <CommentBranch
          key={reply.id}
          comment={reply}
          childrenByParent={childrenByParent}
          depth={depth + 1}
          onDraft={onDraft}
        />
      ))}
    </article>
  );
}

export function useHackerNewsDiscussion(article: ArticleDetail) {
  const ref = discussionRefFor(article);
  const { data: story, error: storyError, isLoading: storyLoading, mutate } = useStory(ref);
  const [open, setOpen] = useState(false);
  const [snapshot, setSnapshot] = useState<DiscussionSnapshot | null>(null);
  const [fetchedLimit, setFetchedLimit] = useState(0);
  const [threadError, setThreadError] = useState<string | null>(null);
  const [threadLoading, setThreadLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const childrenByParent = useMemo(() => {
    const map = new Map<number, DiscussionComment[]>();
    for (const comment of snapshot?.comments ?? []) {
      if (comment.parent_id === null) continue;
      map.set(comment.parent_id, [...(map.get(comment.parent_id) ?? []), comment]);
    }
    return map;
  }, [snapshot]);

  async function loadThread(limit: number, force = false): Promise<DiscussionSnapshot> {
    if (!story) throw new Error("The Hacker News story is still loading");
    const target = Math.min(limit, 300);
    if (
      !force &&
      snapshot &&
      (fetchedLimit >= target || snapshot.included_total < fetchedLimit)
    ) {
      return snapshot;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setThreadLoading(true);
    setThreadError(null);
    try {
      const next = await fetchHNThread(story, target, controller.signal);
      if (abortRef.current === controller) {
        setSnapshot(next);
        setFetchedLimit(target);
      }
      return next;
    } catch (error) {
      if (abortRef.current === controller && !controller.signal.aborted) {
        setThreadError(
          error instanceof Error ? error.message : "Could not load the discussion",
        );
      }
      throw error;
    } finally {
      if (abortRef.current === controller) setThreadLoading(false);
    }
  }

  async function toggleThread() {
    const nextOpen = !open;
    setOpen(nextOpen);
    if (nextOpen && !snapshot && story) await loadThread(120).catch(() => {});
  }

  return {
    ref,
    story,
    storyError,
    storyLoading,
    mutate,
    open,
    snapshot,
    threadError,
    threadLoading,
    childrenByParent,
    loadThread,
    toggleThread,
  };
}

export type HackerNewsDiscussionState = ReturnType<typeof useHackerNewsDiscussion>;

export function HackerNewsDiscussionController({
  article,
  children,
}: {
  article: ArticleDetail;
  children: (discussion: HackerNewsDiscussionState) => ReactNode;
}) {
  return children(useHackerNewsDiscussion(article));
}

export function HackerNewsDiscussionView({
  discussion,
  onDraft,
}: {
  discussion: HackerNewsDiscussionState;
  onDraft: (draft: DiscussionDraft) => void;
}) {
  const {
    ref,
    story,
    storyError,
    storyLoading,
    mutate,
    open,
    snapshot,
    threadError,
    threadLoading,
    childrenByParent,
    loadThread,
    toggleThread,
  } = discussion;
  if (!ref) return null;

  const topLevel = story ? childrenByParent.get(story.id) ?? [] : [];
  const count = story?.descendants ?? 0;

  return (
    <section
      id="hacker-news-discussion"
      className="mt-10 scroll-mt-6 border-t pt-7"
      style={{ borderColor: "var(--line-soft)" }}
    >
      <div className="flex flex-wrap items-center gap-3">
        <div>
          <h2 className="font-serif-nr text-[22px] font-medium">Hacker News discussion</h2>
          <p className="mt-1 font-mono-nr text-[11.5px]" style={{ color: "var(--ink-faint)" }}>
            {story
              ? `${story.score ?? 0} points, ${count} comments`
              : storyLoading
                ? "Refreshing points and comments"
                : "Live discussion unavailable"}
          </p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <button
            className="icon-btn min-h-11 min-w-11"
            title="Refresh points and comment count"
            onClick={() => mutate()}
          >
            <RefreshIcon size={14} />
          </button>
          <a className="btn min-h-11" href={ref.canonicalUrl} target="_blank" rel="noreferrer">
            <ExternalIcon size={13} />
            Open on HN
          </a>
          <button className="btn btn-accent min-h-11" onClick={toggleThread} disabled={!story}>
            {open ? "Hide comments" : "Show comments"}
          </button>
        </div>
      </div>

      {storyError && (
        <p className="mt-4 text-[13px]" style={{ color: "var(--danger)" }}>
          Could not refresh Hacker News. The original discussion link still works.
        </p>
      )}

      {open && (
        <div className="mt-5">
          {threadLoading && !snapshot && (
            <div className="space-y-3" aria-label="Loading comments">
              <div className="h-20 rounded-lg shimmer" />
              <div className="h-16 rounded-lg shimmer" />
            </div>
          )}
          {threadError && (
            <p className="text-[13px]" style={{ color: "var(--danger)" }}>
              {threadError}
            </p>
          )}
          {snapshot && (
            <>
              <div className="border-y" style={{ borderColor: "var(--line-soft)" }}>
                {topLevel.length === 0 && (
                  <p className="py-6 text-[13px]" style={{ color: "var(--ink-faint)" }}>
                    No visible comments yet.
                  </p>
                )}
                {topLevel.map((comment) => (
                  <CommentBranch
                    key={comment.id}
                    comment={comment}
                    childrenByParent={childrenByParent}
                    depth={0}
                    onDraft={(target) =>
                      onDraft({
                        key: `${target.id}-${Date.now()}`,
                        text: `Draft a thoughtful reply to comment ${target.id}. My point is: `,
                      })
                    }
                  />
                ))}
              </div>
              <div className="mt-4 flex flex-wrap items-center gap-3">
                <span className="text-[12px]" style={{ color: "var(--ink-faint)" }}>
                  Loaded {snapshot.included_total} of {snapshot.reported_total} comments
                </span>
                {snapshot.included_total < Math.min(snapshot.reported_total, 300) && (
                  <button
                    className="btn"
                    disabled={threadLoading}
                    onClick={() => loadThread(300, true).catch(() => {})}
                  >
                    {threadLoading ? "Loading" : "Load more for analysis"}
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}

export default function HackerNewsDiscussion({
  article,
  onDraft = () => {},
}: {
  article: ArticleDetail;
  onDraft?: (draft: DiscussionDraft) => void;
}) {
  const discussion = useHackerNewsDiscussion(article);
  return <HackerNewsDiscussionView discussion={discussion} onDraft={onDraft} />;
}
