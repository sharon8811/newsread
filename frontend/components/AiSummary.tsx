"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR, { mutate } from "swr";
import {
  api,
  fetcher,
  type AiStatus,
  type ArticleDetail,
  type SummaryTranslation,
  type TranslationLanguage,
  type User,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { keys } from "@/lib/keys";
import { RefreshIcon, SparkleIcon, TranslateIcon } from "./icons";
import LanguagePickerModal from "./LanguagePickerModal";
import ErrorText from "./ui/ErrorText";

/** Summaries generated before the markdown prompt use "• " bullet lines —
 * rewrite them into list items so they render the same as new ones. */
function asMarkdown(summary: string): string {
  return summary.replace(/^[ \t]*•\s*/gm, "- ");
}

export default function AiSummary({ article }: { article: ArticleDetail }) {
  const { data: status } = useSWR<AiStatus>(keys.aiStatus, fetcher);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestedRef = useRef(false);
  const skippedAsTooShort = article.summary_skipped_reason === "too_short";

  async function generate(force: boolean) {
    setGenerating(true);
    setError(null);
    try {
      await api(`/articles/${article.id}/summarize${force ? "?force=true" : ""}`, {
        method: "POST",
      });
      await mutate(keys.article(article.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Summarization failed");
    } finally {
      setGenerating(false);
    }
  }

  useEffect(() => {
    if (!status?.configured || article.summary || skippedAsTooShort || requestedRef.current) return;
    requestedRef.current = true;
    generate(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.configured, article.id, skippedAsTooShort]);

  if (skippedAsTooShort) {
    return (
      <section
        className="fade-up mt-7 rounded-md border p-5"
        style={{ borderColor: "var(--line)", background: "var(--paper-raised)" }}
      >
        <div className="flex items-center gap-2">
          <SparkleIcon size={13} className="shrink-0" />
          <span className="mono-label" style={{ color: "var(--ink-dim)" }}>
            AI Summary
          </span>
        </div>
        <p className="mt-3 text-body" style={{ color: "var(--ink-dim)" }}>
          This post is already short, so there’s no AI summary.
        </p>
      </section>
    );
  }

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
        <SummaryBody article={article} translatable={status?.translation === true} />
      ) : null}
    </section>
  );
}

/** The summary text plus the translate control. The original is held in the
 * article itself and never overwritten, so switching back is instant and a
 * failed translation costs the reader nothing. */
function SummaryBody({ article, translatable }: { article: ArticleDetail; translatable: boolean }) {
  const { user, updateUser } = useAuth();
  const { data: languages } = useSWR<TranslationLanguage[]>(
    translatable ? keys.translationLanguages : null,
    fetcher,
  );
  const [translation, setTranslation] = useState<SummaryTranslation | null>(null);
  const [showingOriginal, setShowingOriginal] = useState(false);
  const [translating, setTranslating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);

  // No reset-on-article effect: the article page mounts AiSummary with
  // key={article.id}, so moving to another article remounts this component
  // and the previous translation goes with it.
  const saved = user?.translation_language ?? null;
  const language = languages?.find((item) => item.code === (translation?.language ?? saved));
  const showingTranslation = translation !== null && !showingOriginal;
  const body = showingTranslation ? translation.text : article.summary;

  async function translate(code: string, makeDefault: boolean) {
    setPicking(false);
    setTranslating(true);
    setError(null);
    try {
      const result = await api<SummaryTranslation>(`/articles/${article.id}/translate`, {
        method: "POST",
        body: { language: code },
      });
      setTranslation(result);
      setShowingOriginal(false);
      if (makeDefault && code !== saved && user) {
        // Saving the default is a convenience, not the point of the click:
        // a failure here must not read as "the translation failed".
        try {
          updateUser(
            await api<User>("/users/me", {
              method: "PATCH",
              body: { translation_language: code },
            }),
          );
        } catch {
          updateUser({ ...user, translation_language: code });
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Translation failed");
    } finally {
      setTranslating(false);
    }
  }

  function onTranslateClick() {
    if (translation) {
      setShowingOriginal((value) => !value);
    } else if (saved) {
      translate(saved, false);
    } else {
      setPicking(true);
    }
  }

  return (
    <>
      {translatable && (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <button
            className="font-mono-nr flex items-center gap-1.5 text-label transition-colors"
            style={{ color: showingTranslation ? "var(--ink-faint)" : "var(--accent)" }}
            disabled={translating}
            onClick={onTranslateClick}
          >
            <TranslateIcon size={12} />
            {translating
              ? "translating…"
              : translation
                ? showingOriginal
                  ? `show ${language?.name.toLowerCase() ?? "translation"}`
                  : "show original"
                : saved && language
                  ? `translate to ${language.name.toLowerCase()}`
                  : "translate summary"}
          </button>
          {saved && !translating && (
            <button
              className="font-mono-nr text-label transition-colors"
              style={{ color: "var(--ink-faint)" }}
              onClick={() => setPicking(true)}
            >
              another language
            </button>
          )}
          {showingTranslation && translation.model && (
            <span className="font-mono-nr text-caption" style={{ color: "var(--ink-faint)" }}>
              {translation.model}
            </span>
          )}
        </div>
      )}

      {error && (
        <div className="mt-3">
          <ErrorText>{error}</ErrorText>
        </div>
      )}

      {showingTranslation && !translation.translated && (
        <p className="font-mono-nr mt-2 text-label" style={{ color: "var(--ink-faint)" }}>
          This summary is already in {language?.name ?? "that language"}.
        </p>
      )}

      {/* dir="auto" so a right-to-left summary — the original from a Hebrew or
          Arabic feed, or a translation into one — lays itself out correctly
          inside the otherwise left-to-right page. */}
      <div className="summary-md mt-3.5" dir="auto">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{asMarkdown(body)}</ReactMarkdown>
      </div>

      {picking && (
        <LanguagePickerModal
          current={saved}
          allowOneOff={Boolean(saved)}
          onPick={translate}
          onClose={() => setPicking(false)}
        />
      )}
    </>
  );
}
