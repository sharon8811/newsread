"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import useSWR, { mutate } from "swr";
import AiSummary from "@/components/AiSummary";
import { mutateArticleLists } from "@/components/ArticleList";
import EntityCard from "@/components/EntityCard";
import ProjectPickerModal from "@/components/ProjectPickerModal";
import QAPanel from "@/components/QAPanel";
import ShareModal from "@/components/ShareModal";
import {
  BookmarkIcon,
  CommentIcon,
  ExternalIcon,
  FolderIcon,
  ShareIcon,
} from "@/components/icons";
import { api, fetcher, imageSrc, streamQA, type ArticleDetail } from "@/lib/api";
import { domainOf, timeAgo } from "@/lib/format";
import { useReadingTimer } from "@/lib/useReadingTimer";

export default function ArticlePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const key = `/articles/${id}`;
  // While an AI illustration is rendering, poll the detail so the image
  // appears the moment it lands (and the "generating" state clears if it
  // fails). Server-side pending stops reporting after ~3 min, which halts
  // the poll on its own.
  const { data: article, error } = useSWR<ArticleDetail>(key, fetcher, {
    refreshInterval: (data) =>
      data?.image_pending && !data.image_url ? 3000 : 0,
  });
  const [sharing, setSharing] = useState(false);
  const [pickingProject, setPickingProject] = useState(false);
  const markedRef = useRef(false);
  const hadImageRef = useRef<boolean | null>(null);

  useReadingTimer(article?.id);

  useEffect(() => {
    if (article && !article.is_read && !markedRef.current) {
      markedRef.current = true;
      api(`/articles/${article.id}/state`, {
        method: "POST",
        body: { is_read: true },
      }).then(() => {
        mutate(key);
        mutateArticleLists();
      });
    }
  }, [article, key]);

  // When a background-generated illustration lands (image_url goes from
  // absent to present for this article), propagate it to the card/row lists
  // so they pick up the new image too. Only fires on the transition, not for
  // articles that already had an image on open.
  useEffect(() => {
    if (article === undefined) return;
    const hasImage = Boolean(article.image_url);
    if (hadImageRef.current === false && hasImage) mutateArticleLists();
    hadImageRef.current = hasImage;
  }, [article]);

  async function toggleSaved() {
    if (!article) return;
    await api(`/articles/${article.id}/state`, {
      method: "POST",
      body: { is_saved: !article.is_saved },
    });
    mutate(key);
    mutateArticleLists();
  }

  if (error) {
    return (
      <div className="flex flex-col items-center px-8 py-28 text-center">
        <p className="text-[17px] font-medium" style={{ color: "var(--ink-dim)" }}>
          This article is out of reach.
        </p>
        <button className="btn mt-5" onClick={() => router.push("/")}>
          Back to inbox
        </button>
      </div>
    );
  }

  if (!article) {
    return (
      <div className="mx-auto max-w-[680px] px-8 py-14">
        <div className="h-9 w-3/4 rounded-md" style={{ background: "var(--bg-hover)" }} />
        <div className="mt-4 h-4 w-1/3 rounded" style={{ background: "var(--bg-hover)" }} />
      </div>
    );
  }

  return (
    <article className="fade-up mx-auto max-w-[680px] px-5 pb-24 pt-6 sm:px-8 sm:pt-10">
      <button
        className="font-mono-nr text-[11.5px] transition-colors"
        style={{ color: "var(--ink-faint)" }}
        onMouseEnter={(e) => (e.currentTarget.style.color = "var(--ink)")}
        onMouseLeave={(e) => (e.currentTarget.style.color = "var(--ink-faint)")}
        onClick={() => router.back()}
      >
        ← back
      </button>

      <p className="mono-label mt-7">{article.feed_title}</p>
      <h1 className="font-serif-nr mt-2.5 text-[27px] font-medium leading-[1.18] sm:text-[34px]">
        {article.title}
      </h1>
      <p className="font-mono-nr mt-3 text-[12px]" style={{ color: "var(--ink-faint)" }}>
        {domainOf(article.url)}
        {article.author ? ` · ${article.author}` : ""}
        {article.published_at ? ` · ${timeAgo(article.published_at)}` : ""}
      </p>

      {/* Illustration hero. While an AI image renders in the background we show
          a live "generating" placeholder (shimmer + label) that polls above;
          the finished image fades in. Nothing renders for articles with no
          image and none on the way. */}
      {(article.image_url || article.image_pending) && (
        <div
          className={`relative mt-6 aspect-[2/1] w-full overflow-hidden rounded-lg border ${
            article.image_url ? "" : "shimmer"
          }`}
          style={{ borderColor: "var(--line-soft)", background: "var(--bg-hover)" }}
        >
          {article.image_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={imageSrc(article.image_url)}
              alt=""
              className="fade-in h-full w-full object-cover"
              onError={(e) => {
                e.currentTarget.style.display = "none";
              }}
            />
          ) : (
            <span
              role="status"
              className="font-mono-nr absolute inset-0 flex items-center justify-center gap-2 text-[11px]"
              style={{ color: "var(--ink-faint)" }}
            >
              <span aria-hidden="true" style={{ color: "var(--accent)" }}>
                ✦
              </span>
              generating illustration
              <span aria-hidden="true" className="inline-flex items-center gap-1">
                {[0, 1, 2].map((i) => (
                  <span
                    key={i}
                    className="typing-dot"
                    style={{ animationDelay: `${i * 0.18}s` }}
                  />
                ))}
              </span>
            </span>
          )}
        </div>
      )}

      <div
        className="mt-7 flex flex-wrap items-center gap-2 border-y py-3.5"
        style={{ borderColor: "var(--line-soft)" }}
      >
        <a className="btn btn-accent" href={article.url} target="_blank" rel="noreferrer">
          <ExternalIcon size={14} />
          Read original
        </a>
        {article.comments_url && (
          <a className="btn" href={article.comments_url} target="_blank" rel="noreferrer">
            <CommentIcon size={14} />
            Discussion
          </a>
        )}
        <div className="ml-auto flex items-center gap-1">
          <button
            className={`icon-btn ${article.is_saved ? "active" : ""}`}
            style={{ width: 34, height: 34 }}
            title={article.is_saved ? "Unsave" : "Save for later"}
            onClick={toggleSaved}
          >
            <BookmarkIcon size={16} filled={article.is_saved} />
          </button>
          <button
            className="btn"
            title="Share with a note"
            onClick={() => setSharing(true)}
          >
            <ShareIcon size={14} />
            Share
          </button>
          <button
            className="btn"
            title="Add to project"
            onClick={() => setPickingProject(true)}
          >
            <FolderIcon size={14} />
            Project
          </button>
        </div>
      </div>

      <EntityCard entities={article.entities} />

      <AiSummary article={article} />

      {article.content_html ? (
        <div
          className="reader mt-8"
          dangerouslySetInnerHTML={{ __html: article.content_html }}
        />
      ) : (
        <p className="reader mt-8 italic" style={{ color: "var(--ink-dim)" }}>
          This feed only provides a headline — use “Read original” above.
        </p>
      )}

      <QAPanel
        qaKey={`/articles/${article.id}/qa`}
        stream={(q, onEvent) => streamQA(article.id, q, onEvent)}
        heading="Ask the article"
        placeholder="Ask anything about this article…"
        suggestions={[
          "What are the key points?",
          "Why does this matter?",
          "What is the counterargument?",
        ]}
      />

      {sharing && <ShareModal article={article} onClose={() => setSharing(false)} />}
      {pickingProject && (
        <ProjectPickerModal article={article} onClose={() => setPickingProject(false)} />
      )}
    </article>
  );
}
