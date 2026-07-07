"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import useSWR, { mutate } from "swr";
import {
  api,
  fetcher,
  PROJECT_STATUSES,
  type ProjectArticle,
  type ProjectComment,
  type ProjectTicketStatus,
} from "@/lib/api";
import { describeLink, domainOf, timeAgo } from "@/lib/format";
import { CheckIcon, CommentIcon, ExternalIcon, LockIcon, TrashIcon, UsersIcon, XIcon } from "./icons";

/** Shared pins of the same article collapse into one card; private pins stay
 * separate (they're the viewer's own — there is at most one per article). */
export function groupPins(pins: ProjectArticle[]): ProjectArticle[][] {
  const shared = new Map<number, ProjectArticle[]>();
  const groups: ProjectArticle[][] = [];
  for (const pin of pins) {
    if (!pin.is_shared) {
      groups.push([pin]);
    } else if (shared.has(pin.article.id)) {
      shared.get(pin.article.id)!.push(pin);
    } else {
      const group = [pin];
      shared.set(pin.article.id, group);
      groups.push(group);
    }
  }
  return groups;
}

/** A comment's attached link as a small provider chip (GitHub PR, YouTube…). */
function LinkChip({ url }: { url: string }) {
  const { label } = describeLink(url);
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="font-mono-nr inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px] transition-colors hover:bg-[var(--bg-hover)]"
      style={{ borderColor: "var(--line)", color: "var(--accent)" }}
      onClick={(e) => e.stopPropagation()}
    >
      <ExternalIcon size={10} />
      {label}
    </a>
  );
}

/** One card per article — the article's "ticket" in this project. `pins`
 * holds every visible pin of that article (several when multiple members
 * shared it independently); status and the comment thread are shared per
 * article, so they live on the card, not on a pin. */
export default function ProjectPinCard({
  pins,
  myId,
  isOwner,
  projectName,
}: {
  pins: ProjectArticle[];
  myId: number;
  isOwner: boolean;
  projectName: string;
}) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const article = pins[0].article;
  const projectId = pins[0].project_id;
  const status = pins[0].status;
  const statusBy = pins[0].status_updated_by;
  const commentCount = pins.reduce((max, p) => Math.max(max, p.comment_count), 0);
  const myPin = pins.find((p) => p.added_by.id === myId);
  const isPrivate = pins.every((p) => !p.is_shared);
  const listKey = `/projects/${projectId}/articles`;
  const threadKey = `/projects/${projectId}/articles/by-article/${article.id}/comments`;

  // Moving the ticket asks for an optional closing note before it applies.
  const [pendingStatus, setPendingStatus] = useState<ProjectTicketStatus | null>(null);
  const [statusNote, setStatusNote] = useState("");
  const [statusLink, setStatusLink] = useState("");

  const [expanded, setExpanded] = useState(false);
  const { data: comments } = useSWR<ProjectComment[]>(expanded ? threadKey : null, fetcher);
  const [draft, setDraft] = useState("");
  const [draftLink, setDraftLink] = useState("");
  const [linking, setLinking] = useState(false);

  function refresh() {
    mutate(listKey);
    mutate("/projects");
    mutate(`/projects/${projectId}`);
  }

  async function run(fn: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await fn();
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  const applyStatus = () =>
    run(async () => {
      await api(`/projects/${projectId}/articles/by-article/${article.id}/status`, {
        method: "PUT",
        body: {
          status: pendingStatus,
          comment: statusNote.trim() || null,
          link_url: statusLink.trim() || null,
        },
      });
      setPendingStatus(null);
      setStatusNote("");
      setStatusLink("");
      mutate(threadKey);
    });

  const postComment = () =>
    run(async () => {
      await api(`/projects/${projectId}/articles/by-article/${article.id}/comments`, {
        method: "POST",
        body: { body: draft.trim(), link_url: draftLink.trim() || null },
      });
      setDraft("");
      setDraftLink("");
      setLinking(false);
      mutate(threadKey);
    });

  const deleteComment = (commentId: number) =>
    run(async () => {
      await api(`/projects/${projectId}/comments/${commentId}`, { method: "DELETE" });
      mutate(threadKey);
    });

  const publish = () =>
    run(() =>
      api(`/projects/${projectId}/articles/${myPin!.id}`, {
        method: "PATCH",
        body: { is_shared: true },
      }),
    );

  const makePrivate = () =>
    run(() =>
      api(`/projects/${projectId}/articles/${myPin!.id}`, {
        method: "PATCH",
        body: { is_shared: false },
      }),
    );

  // One call removes everything the viewer may remove (their own pin, plus all
  // shared pins for the owner) atomically server-side.
  const removable = pins.some(
    (p) => p.added_by.id === myId || (isOwner && p.is_shared),
  );
  const remove = () =>
    run(() =>
      api(`/projects/${projectId}/articles/by-article/${article.id}`, {
        method: "DELETE",
      }),
    );

  return (
    <div
      className="border-b px-5 py-5 transition-colors"
      style={{ borderColor: "var(--line-soft)" }}
    >
      <div className="flex items-center gap-2.5">
        {pins.map((pin) => (
          <span
            key={pin.id}
            className="flex h-7 w-7 items-center justify-center rounded-full text-[12px] font-semibold"
            style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
            title={`@${pin.added_by.username}`}
          >
            {pin.added_by.name[0]?.toUpperCase()}
          </span>
        ))}
        <p className="min-w-0 truncate text-[13px]" style={{ color: "var(--ink-dim)" }}>
          {pins.map((pin, i) => (
            <span key={pin.id}>
              {i > 0 && ", "}
              <span style={{ color: "var(--ink)" }}>
                {pin.added_by.id === myId ? "You" : pin.added_by.name}
              </span>
            </span>
          ))}{" "}
          added this
        </p>
        {isPrivate && (
          <span
            className="font-mono-nr flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px]"
            style={{ borderColor: "var(--line)", color: "var(--ink-faint)" }}
          >
            <LockIcon size={10} />
            Only you
          </span>
        )}
        {status === "done" && (
          <span
            className="font-mono-nr flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px]"
            style={{ borderColor: "var(--line)", color: "var(--accent)" }}
            title={statusBy ? `Marked done by @${statusBy.username}` : "Done"}
          >
            <CheckIcon size={10} />
            Done
          </span>
        )}
        <span className="font-mono-nr ml-auto shrink-0 text-[11px]" style={{ color: "var(--ink-faint)" }}>
          {timeAgo(pins[0].shared_at ?? pins[0].created_at)}
        </span>
      </div>

      <div
        className="mt-3.5 cursor-pointer rounded-md border p-3.5 transition-colors hover:bg-[var(--bg-hover)]"
        style={{
          borderColor: "var(--line)",
          opacity: status === "done" ? 0.65 : 1,
        }}
        onClick={() => router.push(`/article/${article.id}`)}
      >
        <h3 className="font-serif-nr text-[16px] leading-snug">{article.title}</h3>
        <p className="font-mono-nr mt-1 text-[11px]" style={{ color: "var(--ink-faint)" }}>
          {domainOf(article.url)}
          {article.published_at ? ` · ${timeAgo(article.published_at)}` : ""}
        </p>
      </div>

      <div className="mt-3 flex items-center gap-2">
        <select
          className="btn"
          style={{ fontSize: 12, paddingTop: 4, paddingBottom: 4 }}
          aria-label="Status"
          value={pendingStatus ?? status}
          disabled={busy}
          onChange={(e) => {
            const next = e.target.value as ProjectTicketStatus;
            setPendingStatus(next === status ? null : next);
          }}
        >
          {PROJECT_STATUSES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <button
          className="btn"
          style={{ fontSize: 12 }}
          onClick={() => setExpanded((v) => !v)}
        >
          <CommentIcon size={13} />
          {commentCount > 0 ? commentCount : "Comment"}
        </button>
        {myPin && !myPin.is_shared && !confirming && (
          <button
            className="btn"
            style={{ fontSize: 12 }}
            disabled={busy}
            onClick={() => setConfirming(true)}
          >
            <UsersIcon size={13} />
            Share with project
          </button>
        )}
        {confirming && (
          <>
            <span className="text-[12.5px]" style={{ color: "var(--ink-dim)" }}>
              Members of {projectName} will see this.
            </span>
            <button className="btn btn-accent" style={{ fontSize: 12 }} disabled={busy} onClick={publish}>
              Share
            </button>
            <button className="btn" style={{ fontSize: 12 }} onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </>
        )}
        {myPin?.is_shared && (
          <button className="btn" style={{ fontSize: 12 }} disabled={busy} onClick={makePrivate}>
            <LockIcon size={13} />
            Make private
          </button>
        )}
        {removable && !confirming && (
          <button
            className="icon-btn ml-auto"
            title="Remove from project"
            disabled={busy}
            onClick={remove}
          >
            <TrashIcon size={14} />
          </button>
        )}
      </div>

      {pendingStatus && (
        <div className="fade-up mt-3 flex flex-col gap-2">
          <input
            className="input"
            style={{ fontSize: 13, padding: "7px 10px" }}
            placeholder={
              pendingStatus === "done"
                ? "Optional closing note — what wrapped this up?"
                : "Optional note — why is this open again?"
            }
            value={statusNote}
            onChange={(e) => setStatusNote(e.target.value)}
            autoFocus
          />
          <input
            className="input"
            style={{ fontSize: 13, padding: "7px 10px" }}
            placeholder="Optional link — a PR, a video…"
            value={statusLink}
            onChange={(e) => setStatusLink(e.target.value)}
          />
          <div className="flex items-center gap-2">
            <button className="btn btn-accent" style={{ fontSize: 12 }} disabled={busy} onClick={applyStatus}>
              {pendingStatus === "done"
                ? "Mark done"
                : pendingStatus === "open"
                  ? "Reopen"
                  : `Move to ${PROJECT_STATUSES.find((s) => s.value === pendingStatus)?.label}`}
            </button>
            <button
              className="btn"
              style={{ fontSize: 12 }}
              onClick={() => {
                setPendingStatus(null);
                setStatusNote("");
                setStatusLink("");
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {expanded && (
        <div className="mt-3.5 border-t pt-3" style={{ borderColor: "var(--line-soft)" }}>
          {(comments ?? []).map((comment) => (
            <div key={comment.id} className="group/comment mb-2.5 flex items-start gap-2.5">
              <span
                className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold"
                style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
                title={`@${comment.author.username}`}
              >
                {comment.author.name[0]?.toUpperCase()}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-[11.5px]" style={{ color: "var(--ink-faint)" }}>
                  <span style={{ color: "var(--ink-dim)" }}>
                    {comment.author.id === myId ? "You" : comment.author.name}
                  </span>{" "}
                  · {timeAgo(comment.created_at)}
                </p>
                {comment.body && (
                  <p className="mt-0.5 text-[13.5px] leading-normal">{comment.body}</p>
                )}
                {comment.link_url && (
                  <div className="mt-1">
                    <LinkChip url={comment.link_url} />
                  </div>
                )}
              </div>
              {(comment.author.id === myId || isOwner) && (
                <button
                  className="icon-btn opacity-0 group-hover/comment:opacity-100"
                  title="Delete comment"
                  disabled={busy}
                  onClick={() => deleteComment(comment.id)}
                >
                  <XIcon size={11} />
                </button>
              )}
            </div>
          ))}
          {comments?.length === 0 && (
            <p className="mb-2.5 text-[12.5px]" style={{ color: "var(--ink-faint)" }}>
              No comments yet — start the thread.
            </p>
          )}
          <div className="flex items-center gap-2">
            <input
              className="input flex-1"
              style={{ fontSize: 13, padding: "7px 10px" }}
              placeholder="Add a comment…"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && draft.trim()) postComment();
              }}
            />
            <button
              className={`icon-btn ${linking ? "active" : ""}`}
              title="Attach a link"
              onClick={() => setLinking((v) => !v)}
            >
              <ExternalIcon size={13} />
            </button>
            <button
              className="btn btn-accent"
              style={{ fontSize: 12 }}
              disabled={busy || !draft.trim()}
              onClick={postComment}
            >
              Post
            </button>
          </div>
          {linking && (
            <input
              className="input mt-2"
              style={{ fontSize: 13, padding: "7px 10px" }}
              placeholder="https:// link to attach — a PR, a video…"
              value={draftLink}
              onChange={(e) => setDraftLink(e.target.value)}
            />
          )}
        </div>
      )}

      {error && (
        <p className="mt-2 text-[12.5px]" style={{ color: "var(--danger)" }}>
          {error}
        </p>
      )}
    </div>
  );
}
