"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";

import QAPanel from "@/components/QAPanel";
import { CommentIcon, ExternalIcon, RefreshIcon } from "@/components/icons";
import { streamDiscussionQA, type ArticleDetail } from "@/lib/api";
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

export function HackerNewsDiscussionLink({ article }: { article: ArticleDetail }) {
  const ref = discussionRefFor(article);
  const { data: story } = useStory(ref);
  if (!ref) return null;
  const details = story
    ? `${story.score ?? 0} points, ${story.descendants ?? 0} comments`
    : "Discussion";
  return (
    <a className="btn" href={ref.canonicalUrl} target="_blank" rel="noreferrer">
      <CommentIcon size={14} />
      {details}
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

export default function HackerNewsDiscussion({ article }: { article: ArticleDetail }) {
  const ref = discussionRefFor(article);
  const { data: story, error: storyError, isLoading: storyLoading, mutate } = useStory(ref);
  const [open, setOpen] = useState(false);
  const [snapshot, setSnapshot] = useState<DiscussionSnapshot | null>(null);
  const [threadError, setThreadError] = useState<string | null>(null);
  const [threadLoading, setThreadLoading] = useState(false);
  const [prefill, setPrefill] = useState<{ key: string; text: string } | null>(null);
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

  if (!ref) return null;

  async function loadThread(limit: number): Promise<DiscussionSnapshot> {
    if (!story) throw new Error("The Hacker News story is still loading");
    if (snapshot && snapshot.included_total >= Math.min(limit, story.descendants ?? limit)) {
      return snapshot;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setThreadLoading(true);
    setThreadError(null);
    try {
      const next = await fetchHNThread(story, limit, controller.signal);
      setSnapshot(next);
      return next;
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not load the discussion";
      setThreadError(message);
      throw error;
    } finally {
      setThreadLoading(false);
    }
  }

  async function toggleThread() {
    const nextOpen = !open;
    setOpen(nextOpen);
    if (nextOpen && !snapshot && story) {
      await loadThread(120).catch(() => {});
    }
  }

  const topLevel = story ? childrenByParent.get(story.id) ?? [] : [];
  const count = story?.descendants ?? 0;

  return (
    <section className="mt-10 border-t pt-7" style={{ borderColor: "var(--line-soft)" }}>
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
        <div className="ml-auto flex items-center gap-2">
          <button
            className="icon-btn"
            style={{ width: 34, height: 34 }}
            title="Refresh points and comment count"
            onClick={() => mutate()}
          >
            <RefreshIcon size={14} />
          </button>
          <a className="btn" href={ref.canonicalUrl} target="_blank" rel="noreferrer">
            <ExternalIcon size={13} />
            Open on HN
          </a>
          <button className="btn btn-accent" onClick={toggleThread} disabled={!story}>
            {open ? "Hide comments" : "Read comments"}
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
                      setPrefill({
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
                  <button className="btn" disabled={threadLoading} onClick={() => loadThread(300)}>
                    {threadLoading ? "Loading" : "Load more for analysis"}
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {story && (
        <QAPanel
          key={prefill?.key ?? "discussion-qa"}
          qaKey={`/articles/${article.id}/discussion/qa`}
          stream={async (question, onEvent) => {
            const completeSnapshot = await loadThread(300);
            return streamDiscussionQA(article.id, question, completeSnapshot, onEvent);
          }}
          heading="Ask the discussion"
          placeholder="Ask what commenters think, or describe the comment you want to draft"
          suggestions={[
            "Summarize the discussion",
            "Where do commenters disagree?",
            "What did commenters add beyond the article?",
            "Trace how the conversation evolved",
            "Find corrections and unresolved questions",
            "Draft a concise HN comment",
          ]}
          initialInput={prefill?.text ?? ""}
        />
      )}
    </section>
  );
}
