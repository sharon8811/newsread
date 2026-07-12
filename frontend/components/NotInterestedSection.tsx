"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher, type DislikeRule } from "@/lib/api";
import { mutateArticleLists } from "./ArticleList";
import { TrashIcon } from "./icons";

const KIND_LABELS: Record<DislikeRule["kind"], string> = {
  article: "article",
  entity: "entity",
  topic: "topic",
  story: "story",
};

function expiresIn(iso: string): string {
  const days = Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000));
  return days <= 1 ? "expires today" : `expires in ${days}d`;
}

/** Settings block listing every "not interested" rule. Deleting a rule is the
 * undo story: its suppressions cascade away server-side and the hidden
 * articles reappear. */
export default function NotInterestedSection() {
  const { data: rules } = useSWR<DislikeRule[]>("/interests/dislikes", fetcher);
  const [expanded, setExpanded] = useState<number | null>(null);
  const { data: hits } = useSWR<{ id: number; title: string }[]>(
    expanded !== null ? `/interests/dislikes/${expanded}/articles` : null,
    fetcher,
  );

  async function remove(rule: DislikeRule) {
    await api(`/interests/dislikes/${rule.id}`, { method: "DELETE" });
    if (expanded === rule.id) setExpanded(null);
    mutate("/interests/dislikes");
    mutateArticleLists();
  }

  if (!rules) return null;

  return (
    <section className="mt-9">
      <p className="mono-label">Not interested</p>
      <p className="mt-1.5 text-[13px]" style={{ color: "var(--ink-faint)" }}>
        Articles matching these rules are hidden from your feed. Remove a rule to bring
        its articles back.
      </p>

      {rules.length === 0 ? (
        <p className="mt-3 text-[13px]" style={{ color: "var(--ink-faint)" }}>
          Nothing muted. Use “Not interested” on an article to start.
        </p>
      ) : (
        <div className="mt-3.5 flex flex-col gap-1">
          {rules.map((rule) => (
            <div key={rule.id}>
              <div
                className="group flex items-center gap-2.5 rounded-md border px-3.5 py-2 text-[13.5px]"
                style={{ background: "var(--bg-raised)", borderColor: "var(--line)" }}
              >
                <span
                  className="font-mono-nr shrink-0 rounded-full border px-2 py-0.5 text-[10.5px]"
                  style={{ borderColor: "var(--line)", color: "var(--ink-faint)" }}
                >
                  {KIND_LABELS[rule.kind]}
                </span>
                <span className="min-w-0 flex-1 truncate">{rule.label}</span>
                <button
                  className="font-mono-nr shrink-0 text-[11px]"
                  style={{ color: "var(--ink-faint)" }}
                  title="Show recently hidden articles"
                  onClick={() => setExpanded((e) => (e === rule.id ? null : rule.id))}
                >
                  {rule.hidden_count} hidden
                  {rule.expires_at ? ` · ${expiresIn(rule.expires_at)}` : ""}
                </button>
                <button
                  className="icon-btn shrink-0 opacity-0 group-hover:opacity-100"
                  style={{ width: 24, height: 24 }}
                  title="Remove rule"
                  onClick={() => remove(rule)}
                >
                  <TrashIcon size={13} />
                </button>
              </div>
              {expanded === rule.id && (
                <div className="mb-1 ml-3 border-l pl-3 pt-1" style={{ borderColor: "var(--line)" }}>
                  {(hits ?? []).map((hit) => (
                    <p
                      key={hit.id}
                      className="truncate py-0.5 text-[12.5px]"
                      style={{ color: "var(--ink-dim)" }}
                    >
                      {hit.title}
                    </p>
                  ))}
                  {hits && hits.length === 0 && (
                    <p className="py-0.5 text-[12.5px]" style={{ color: "var(--ink-faint)" }}>
                      Nothing hidden recently.
                    </p>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
