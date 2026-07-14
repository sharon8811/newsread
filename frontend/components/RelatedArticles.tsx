"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  api,
  fetcher,
  type AiStatus,
  type ArticleDetail,
  type CoverageSynthesis,
  type RelatedArticle,
} from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { SparkleIcon } from "./icons";

/** Related coverage from the user's subscribed feeds (server-side hybrid:
 * shared-resource entities lead, embedding KNN boosted by shared name
 * entities fills the rest — up to five, fewer when the tail is weak), plus
 * the lazy "synthesize coverage" action: one LLM call over stored summaries,
 * only ever on click. The whole section hides when there is nothing related. */
export default function RelatedArticles({ article }: { article: ArticleDetail }) {
  const router = useRouter();
  const { data: related } = useSWR<RelatedArticle[]>(
    `/articles/${article.id}/related`,
    fetcher,
  );
  const { data: status } = useSWR<AiStatus>("/ai/status", fetcher);
  const [synthesis, setSynthesis] = useState<CoverageSynthesis | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!related || related.length === 0) return null;

  async function synthesize() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      setSynthesis(
        await api<CoverageSynthesis>(`/articles/${article.id}/related-synthesis`, {
          method: "POST",
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "The synthesis failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mt-10 border-t pt-7" style={{ borderColor: "var(--line-soft)" }}>
      <div className="flex flex-wrap items-center gap-3">
        <div>
          <p className="mono-label">From your feeds</p>
          <h2 className="font-serif-nr mt-1 text-[22px] font-medium">Related coverage</h2>
        </div>
        {status?.configured && !synthesis && !busy && (
          <button className="btn ml-auto" onClick={synthesize}>
            <SparkleIcon size={13} />
            Synthesize coverage
          </button>
        )}
      </div>

      <div className="mt-4 flex flex-col gap-2">
        {related.map((item) => (
          <div
            key={item.id}
            className="cursor-pointer rounded-md border p-3.5 transition-colors hover:bg-[var(--bg-hover)]"
            style={{ borderColor: "var(--line)", background: "var(--bg-raised)" }}
            onClick={() => router.push(`/article/${item.id}`)}
          >
            <div className="flex items-center gap-2">
              {!item.is_read && <span className="dot-unread shrink-0" />}
              <p className="font-serif-nr min-w-0 flex-1 truncate text-[16px]">
                {item.title}
              </p>
              {item.tier === "same_story" && (
                <span
                  className="font-mono-nr shrink-0 rounded-full border px-2 py-0.5 text-[10px]"
                  style={{
                    borderColor: "var(--accent-border)",
                    background: "var(--accent-soft)",
                    color: "var(--accent-bright)",
                  }}
                >
                  SAME STORY
                </span>
              )}
            </div>
            <p className="font-mono-nr mt-1 text-[11px]" style={{ color: "var(--ink-faint)" }}>
              {item.feed_title}
              {item.published_at ? ` · ${timeAgo(item.published_at)}` : ""}
            </p>
          </div>
        ))}
      </div>

      {busy && (
        <div
          className="mt-4 rounded-md border p-5"
          style={{ borderColor: "var(--accent-border)", background: "var(--accent-soft)" }}
        >
          <div className="flex items-center gap-2">
            <SparkleIcon size={13} />
            <span className="mono-label" style={{ color: "var(--accent)" }}>
              Coverage synthesis
            </span>
          </div>
          <div className="mt-3.5 flex flex-col gap-2.5">
            {[92, 100, 64].map((width, i) => (
              <div
                key={i}
                className="h-3 animate-pulse rounded"
                style={{
                  width: `${width}%`,
                  background: "var(--accent-border)",
                  animationDelay: `${i * 150}ms`,
                }}
              />
            ))}
          </div>
          <p className="font-mono-nr mt-3 text-[11px]" style={{ color: "var(--ink-faint)" }}>
            Reading the coverage…
          </p>
        </div>
      )}

      {error && !busy && (
        <div className="mt-4 flex items-center gap-3">
          <p className="text-[13px]" style={{ color: "var(--danger)" }}>
            {error}
          </p>
          <button className="btn" onClick={synthesize}>
            Try again
          </button>
        </div>
      )}

      {synthesis && !busy && (
        <div
          className="fade-up mt-4 rounded-md border p-5"
          style={{ borderColor: "var(--accent-border)", background: "var(--accent-soft)" }}
        >
          <div className="flex items-center gap-2">
            <SparkleIcon size={13} />
            <span className="mono-label" style={{ color: "var(--accent)" }}>
              Coverage synthesis
            </span>
          </div>

          <div className="summary-md mt-3.5">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{synthesis.overview}</ReactMarkdown>
          </div>

          {synthesis.timeline && (
            <div className="mt-4">
              <p className="mono-label" style={{ color: "var(--accent)" }}>
                Timeline
              </p>
              <div
                className="mt-2 flex flex-col gap-2 border-l pl-3.5"
                style={{ borderColor: "var(--accent-border)" }}
              >
                {synthesis.timeline.map((item, i) => (
                  <div key={i} className="flex items-baseline gap-2.5">
                    <span
                      className="font-mono-nr shrink-0 text-[11px]"
                      style={{ color: "var(--accent-bright)" }}
                    >
                      {item.when}
                    </span>
                    <span className="text-[14px] leading-relaxed">{item.what}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          {!synthesis.timeline && synthesis.timeline_raw && (
            <div className="summary-md mt-4">
              <p className="mono-label" style={{ color: "var(--accent)" }}>
                Timeline
              </p>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {synthesis.timeline_raw}
              </ReactMarkdown>
            </div>
          )}

          {synthesis.perspectives && (
            <div className="summary-md mt-4">
              <p className="mono-label" style={{ color: "var(--accent)" }}>
                Perspectives
              </p>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {synthesis.perspectives}
              </ReactMarkdown>
            </div>
          )}

          <p className="font-mono-nr mt-3.5 text-[10.5px]" style={{ color: "var(--ink-faint)" }}>
            {synthesis.sources.map((source) => `[${source.n}] ${source.title}`).join("  ·  ")}
          </p>
        </div>
      )}
    </section>
  );
}
