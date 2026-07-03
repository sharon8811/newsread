"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import useSWR, { mutate } from "swr";
import AiSummary from "@/components/AiSummary";
import { mutateArticleLists } from "@/components/ArticleList";
import QAPanel from "@/components/QAPanel";
import ShareModal from "@/components/ShareModal";
import {
  BookmarkIcon,
  CommentIcon,
  ExternalIcon,
  ShareIcon,
} from "@/components/icons";
import { api, fetcher, type ArticleDetail } from "@/lib/api";
import { domainOf, timeAgo } from "@/lib/format";

export default function ArticlePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const key = `/articles/${id}`;
  const { data: article, error } = useSWR<ArticleDetail>(key, fetcher);
  const [sharing, setSharing] = useState(false);
  const markedRef = useRef(false);

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
        <p className="font-serif-nr text-[22px] italic" style={{ color: "var(--ink-dim)" }}>
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
        <div className="h-9 w-3/4 rounded-lg" style={{ background: "var(--bg-raised)" }} />
        <div className="mt-4 h-4 w-1/3 rounded" style={{ background: "var(--bg-raised)" }} />
      </div>
    );
  }

  return (
    <article className="fade-up mx-auto max-w-[680px] px-8 pb-24 pt-10">
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
      <h1 className="font-serif-nr mt-2.5 text-[34px] font-medium leading-[1.18]">
        {article.title}
      </h1>
      <p className="font-mono-nr mt-3 text-[12px]" style={{ color: "var(--ink-faint)" }}>
        {domainOf(article.url)}
        {article.author ? ` · ${article.author}` : ""}
        {article.published_at ? ` · ${timeAgo(article.published_at)}` : ""}
      </p>

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
        </div>
      </div>

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

      <QAPanel article={article} />

      {sharing && <ShareModal article={article} onClose={() => setSharing(false)} />}
    </article>
  );
}
