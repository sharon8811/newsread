"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { mutate } from "swr";
import { api, type ProjectArticle } from "@/lib/api";
import { domainOf, timeAgo } from "@/lib/format";
import { LockIcon, TrashIcon, UsersIcon } from "./icons";

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

/** One card per article. `pins` holds every visible pin of that article —
 * usually one, several when multiple members shared it independently. */
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
  const myPin = pins.find((p) => p.added_by.id === myId);
  const isPrivate = pins.every((p) => !p.is_shared);
  const listKey = `/projects/${projectId}/articles`;

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

  // Removes what the viewer is allowed to remove: their own pin, and (for the
  // owner) every shared pin of this article.
  const removable = pins.filter(
    (p) => p.added_by.id === myId || (isOwner && p.is_shared),
  );
  const remove = () =>
    run(() =>
      Promise.all(
        removable.map((p) =>
          api(`/projects/${projectId}/articles/${p.id}`, { method: "DELETE" }),
        ),
      ),
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
        <span className="font-mono-nr ml-auto shrink-0 text-[11px]" style={{ color: "var(--ink-faint)" }}>
          {timeAgo(pins[0].shared_at ?? pins[0].created_at)}
        </span>
      </div>

      {pins.map(
        (pin) =>
          pin.note && (
            <blockquote key={pin.id} className="note-quote mt-3.5">
              {pin.note}
              {pins.length > 1 && (
                <span
                  className="font-mono-nr ml-2 text-[11px] not-italic"
                  style={{ color: "var(--ink-faint)" }}
                >
                  — @{pin.added_by.username}
                </span>
              )}
            </blockquote>
          ),
      )}

      <div
        className="mt-3.5 cursor-pointer rounded-md border p-3.5 transition-colors hover:bg-[var(--bg-hover)]"
        style={{ borderColor: "var(--line)" }}
        onClick={() => router.push(`/article/${article.id}`)}
      >
        <h3 className="font-serif-nr text-[16px] leading-snug">{article.title}</h3>
        <p className="font-mono-nr mt-1 text-[11px]" style={{ color: "var(--ink-faint)" }}>
          {domainOf(article.url)}
          {article.published_at ? ` · ${timeAgo(article.published_at)}` : ""}
        </p>
      </div>

      <div className="mt-3 flex items-center gap-2">
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
              Members of {projectName} will see this{myPin?.note ? " and your note" : ""}.
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
        {removable.length > 0 && !confirming && (
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

      {error && (
        <p className="mt-2 text-[12.5px]" style={{ color: "var(--danger)" }}>
          {error}
        </p>
      )}
    </div>
  );
}
