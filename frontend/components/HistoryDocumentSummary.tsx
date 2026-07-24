"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type BrowserHistoryDocumentSummary } from "@/lib/api";
import { useHistoryDocumentSummary } from "@/lib/queries";
import { ExternalIcon, RefreshIcon, SparkleIcon } from "./icons";
import ErrorText from "./ui/ErrorText";

export default function HistoryDocumentSummary({
  documentId,
}: {
  documentId: number;
}) {
  const { data: summary, error: loadError, mutate } =
    useHistoryDocumentSummary(documentId);
  const [requesting, setRequesting] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);

  async function generate(force = false) {
    setRequesting(true);
    setRequestError(null);
    try {
      const result = await api<BrowserHistoryDocumentSummary>(
        `/history/documents/${documentId}/summarize${force ? "?force=true" : ""}`,
        { method: "POST" },
      );
      await mutate(result, { revalidate: false });
    } catch (error) {
      setRequestError(
        error instanceof Error ? error.message : "Could not generate the summary",
      );
    } finally {
      setRequesting(false);
    }
  }

  const generating =
    requesting || summary?.state === "queued" || summary?.state === "running";

  return (
    <section
      className="mt-7 rounded-lg border p-5 sm:p-6"
      style={{
        borderColor: "var(--accent-border)",
        background: "var(--accent-soft)",
      }}
    >
      <div className="flex items-center gap-2">
        <SparkleIcon size={13} />
        <span className="mono-label" style={{ color: "var(--accent)" }}>
          Page summary
        </span>
        {summary?.model && summary.state === "ready" && (
          <span
            className="font-mono-nr text-caption"
            style={{ color: "var(--ink-faint)" }}
          >
            {summary.model}
          </span>
        )}
        {summary?.state === "ready" && (
          <button
            className="icon-btn ml-auto"
            title="Regenerate summary"
            onClick={() => generate(true)}
          >
            <RefreshIcon size={12} />
          </button>
        )}
      </div>

      {loadError || requestError || summary?.state === "error" ? (
        <div className="mt-4">
          <ErrorText>
            {requestError ??
              (summary?.error_code === "invalid_model_output"
                ? "The model returned an invalid cited summary."
                : "Could not generate the saved-page summary.")}
          </ErrorText>
          <button className="btn mt-3" onClick={() => generate()}>
            Try again
          </button>
        </div>
      ) : generating ? (
        <div className="mt-4 space-y-2.5" aria-live="polite">
          {[95, 88, 64].map((width) => (
            <div
              key={width}
              className="h-3.5 animate-pulse rounded"
              style={{ width: `${width}%`, background: "var(--line)" }}
            />
          ))}
          <p
            className="font-mono-nr pt-1 text-label"
            style={{ color: "var(--ink-faint)" }}
          >
            Summarizing the saved version…
          </p>
        </div>
      ) : summary?.state === "ready" && summary.markdown ? (
        <>
          <div className="summary-md mt-4">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {summary.markdown}
            </ReactMarkdown>
          </div>
          {(summary.citations?.length ?? 0) > 0 && (
            <ol
              className="mt-5 space-y-2 border-t pt-4"
              style={{ borderColor: "var(--accent-border)" }}
            >
              {(summary.citations ?? []).map((citation) => (
                <li
                  key={`${citation.label}-${citation.block_id}`}
                  className="flex gap-2 text-body-sm leading-relaxed"
                  style={{ color: "var(--ink-dim)" }}
                >
                  <span className="font-mono-nr shrink-0">[{citation.label}]</span>
                  <span className="line-clamp-2">“{citation.quote}”</span>
                  {citation.url && (
                    <a
                      className="ml-auto shrink-0"
                      href={citation.url}
                      target="_blank"
                      rel="noreferrer"
                      title="Open source page"
                    >
                      <ExternalIcon size={12} />
                    </a>
                  )}
                </li>
              ))}
            </ol>
          )}
        </>
      ) : summary?.state === "too_short" ? (
        <p className="mt-4 text-body" style={{ color: "var(--ink-dim)" }}>
          This saved page is already short, so it does not need a summary.
        </p>
      ) : (
        <div className="mt-4">
          <p className="text-body leading-relaxed" style={{ color: "var(--ink-dim)" }}>
            Generate a cited summary of this exact saved version. Nothing is sent
            to the language model until you click.
          </p>
          <button className="btn btn-accent mt-4" onClick={() => generate()}>
            <SparkleIcon size={13} />
            Summarize saved page
          </button>
        </div>
      )}
    </section>
  );
}
