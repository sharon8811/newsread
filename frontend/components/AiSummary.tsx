"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR, { mutate } from "swr";
import { api, fetcher, type AiStatus, type ArticleDetail } from "@/lib/api";
import { RefreshIcon, SparkleIcon } from "./icons";
import ErrorText from "./ui/ErrorText";

/** Summaries generated before the markdown prompt use "• " bullet lines —
 * rewrite them into list items so they render the same as new ones. */
function asMarkdown(summary: string): string {
  return summary.replace(/^[ \t]*•\s*/gm, "- ");
}

export default function AiSummary({ article }: { article: ArticleDetail }) {
  const { data: status } = useSWR<AiStatus>("/ai/status", fetcher);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestedRef = useRef(false);

  async function generate(force: boolean) {
    setGenerating(true);
    setError(null);
    try {
      await api(`/articles/${article.id}/summarize${force ? "?force=true" : ""}`, {
        method: "POST",
      });
      await mutate(`/articles/${article.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Summarization failed");
    } finally {
      setGenerating(false);
    }
  }

  useEffect(() => {
    if (!status?.configured || article.summary || requestedRef.current) return;
    requestedRef.current = true;
    generate(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.configured, article.id]);

  if (!status?.configured) return null;

  return (
    <section
      className="fade-up mt-7 rounded-md border p-5"
      style={{ borderColor: "var(--accent-border)", background: "var(--accent-soft)" }}
    >
      <div className="flex items-center gap-2">
        <SparkleIcon size={13} className="shrink-0" />
        <span className="mono-label" style={{ color: "var(--accent)" }}>
          AI Summary
        </span>
        {article.summary_model && !generating && (
          <span className="font-mono-nr text-caption" style={{ color: "var(--ink-faint)" }}>
            {article.summary_model}
          </span>
        )}
        {article.summary && !generating && (
          <button
            className="icon-btn ml-auto"
            style={{ width: 24, height: 24 }}
            title="Regenerate summary"
            onClick={() => generate(true)}
          >
            <RefreshIcon size={12} />
          </button>
        )}
      </div>

      {generating ? (
        <div className="mt-3.5 flex flex-col gap-2.5">
          {[92, 100, 64].map((w, i) => (
            <div
              key={i}
              className="h-3.5 animate-pulse rounded"
              style={{
                width: `${w}%`,
                background: "var(--line)",
                animationDelay: `${i * 150}ms`,
              }}
            />
          ))}
          <p className="font-mono-nr mt-1 text-label" style={{ color: "var(--ink-faint)" }}>
            Reading the full article…
          </p>
        </div>
      ) : error ? (
        <div className="mt-3">
          <ErrorText>
            {error}
          </ErrorText>
          <button className="btn mt-2.5" onClick={() => generate(false)}>
            Try again
          </button>
        </div>
      ) : article.summary ? (
        <div className="summary-md mt-3.5">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {asMarkdown(article.summary)}
          </ReactMarkdown>
        </div>
      ) : null}
    </section>
  );
}
