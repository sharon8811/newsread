"use client";

import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  api,
  type BrowserHistoryCitation,
  type BrowserHistoryDocumentSummary,
} from "@/lib/api";
import { useHistoryDocumentSummary } from "@/lib/queries";
import { buildTextFragmentUrl } from "@/lib/textFragments";
import { ExternalIcon, RefreshIcon, SparkleIcon } from "./icons";
import ErrorText from "./ui/ErrorText";

const CITATION_LINK_PREFIX = "#newsread-citation-";

function citationNavigationData(citation: BrowserHistoryCitation): string {
  return JSON.stringify({
    version: 1,
    url: citation.url,
    anchor: {
      quote: citation.quote,
      prefix: citation.prefix,
      suffix: citation.suffix,
    },
  });
}

function citedMarkdown(
  markdown: string,
  labels: ReadonlySet<number>,
): string {
  // Only real sources become markers, so a bracketed year stays prose. The
  // space before a marker goes with it: a superscript belongs against the
  // claim it supports.
  return markdown.replace(/[ \t]*\[(\d+)\](?!\()/g, (match, label: string) =>
    labels.has(Number(label))
      ? `[${label}](${CITATION_LINK_PREFIX}${label})`
      : match,
  );
}

export default function HistoryDocumentSummary({
  documentId,
}: {
  documentId: number;
}) {
  const { data: summary, error: loadError, mutate } =
    useHistoryDocumentSummary(documentId);
  const [requesting, setRequesting] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [selectedCitationLabel, setSelectedCitationLabel] = useState<
    number | null
  >(null);
  // The source list is reference material — a dozen quotes above the fold
  // buried the summary itself.
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const citationsByLabel = useMemo(
    () =>
      new Map(
        (summary?.citations ?? []).map((citation) => [
          citation.label,
          citation,
        ]),
      ),
    [summary?.citations],
  );
  const selectedCitation =
    selectedCitationLabel === null
      ? null
      : (citationsByLabel.get(selectedCitationLabel) ?? null);

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
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                a: ({ href, children, ...props }) => {
                  if (href?.startsWith(CITATION_LINK_PREFIX)) {
                    const label = Number(href.slice(CITATION_LINK_PREFIX.length));
                    const citation = citationsByLabel.get(label);
                    if (citation) {
                      return (
                        <button
                          type="button"
                          className="citation-marker"
                          aria-label={`[${label}]`}
                          aria-expanded={selectedCitationLabel === label}
                          aria-controls="history-citation-preview"
                          onClick={() => setSelectedCitationLabel(label)}
                        >
                          {label}
                        </button>
                      );
                    }
                    // A bracketed number the model did not cite — a year, a
                    // version — must not become a dead in-page link.
                    return <>[{label}]</>;
                  }
                  return (
                    <a href={href} {...props}>
                      {children}
                    </a>
                  );
                },
              }}
            >
              {citedMarkdown(summary.markdown, new Set(citationsByLabel.keys()))}
            </ReactMarkdown>
          </div>
          {selectedCitation && (
            <CitationPreview
              citation={selectedCitation}
              onClose={() => setSelectedCitationLabel(null)}
            />
          )}
          {(summary.citations?.length ?? 0) > 0 && (
            <div
              className="mt-5 border-t pt-4"
              style={{ borderColor: "var(--accent-border)" }}
            >
              <button
                type="button"
                className="mono-label"
                style={{ color: "var(--ink-faint)" }}
                aria-expanded={sourcesOpen}
                aria-controls="history-citation-sources"
                onClick={() => setSourcesOpen((open) => !open)}
              >
                {sourcesOpen ? "Hide" : "Show"} {summary.citations?.length}{" "}
                {summary.citations?.length === 1 ? "source" : "sources"}
              </button>
            </div>
          )}
          {sourcesOpen && (summary.citations?.length ?? 0) > 0 && (
            <ol id="history-citation-sources" className="mt-3 space-y-2">
              {(summary.citations ?? []).map((citation) => (
                <li
                  key={`${citation.label}-${citation.block_id}`}
                  className="flex gap-2 text-body-sm leading-relaxed"
                  style={{ color: "var(--ink-dim)" }}
                >
                  <button
                    type="button"
                    className="flex min-w-0 flex-1 gap-2 text-left"
                    aria-expanded={selectedCitationLabel === citation.label}
                    aria-controls="history-citation-preview"
                    onClick={() => setSelectedCitationLabel(citation.label)}
                  >
                    <span className="font-mono-nr shrink-0">
                      [{citation.label}]
                    </span>
                    <span className="line-clamp-2">“{citation.quote}”</span>
                    {citation.url && (
                      <ExternalIcon className="ml-auto shrink-0" size={12} />
                    )}
                  </button>
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

function CitationPreview({
  citation,
  onClose,
}: {
  citation: BrowserHistoryCitation;
  onClose: () => void;
}) {
  const highlightedUrl = buildTextFragmentUrl(citation.url, citation);

  return (
    <aside
      id="history-citation-preview"
      className="mt-4 rounded-md border p-4"
      style={{
        borderColor: "var(--accent-border)",
        background: "var(--paper)",
      }}
      aria-label={`Citation ${citation.label}`}
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="mono-label" style={{ color: "var(--ink-faint)" }}>
            Source [{citation.label}]
          </p>
          <blockquote
            className="mt-2 text-body-sm leading-relaxed"
            style={{ color: "var(--ink)" }}
          >
            “{citation.quote}”
          </blockquote>
        </div>
        <button
          type="button"
          className="font-mono-nr text-label"
          style={{ color: "var(--ink-faint)" }}
          onClick={onClose}
          aria-label="Close citation preview"
        >
          Close
        </button>
      </div>
      {highlightedUrl ? (
        <a
          className="btn btn-accent mt-4"
          href={highlightedUrl}
          target="_blank"
          rel="noopener noreferrer"
          data-newsread-citation={citationNavigationData(citation)}
        >
          Open highlighted source
          <ExternalIcon size={12} />
        </a>
      ) : (
        <p
          className="font-mono-nr mt-3 text-label"
          style={{ color: "var(--ink-faint)" }}
        >
          This saved version no longer has an active page location.
        </p>
      )}
    </aside>
  );
}
