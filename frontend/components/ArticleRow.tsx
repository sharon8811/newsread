"use client";

import { useRouter } from "next/navigation";
import { type Article } from "@/lib/api";
import { domainOf, timeAgo } from "@/lib/format";
import { BookmarkIcon, ExternalIcon, ShareIcon } from "./icons";

export default function ArticleRow({
  article,
  selected,
  index,
  onToggleSaved,
  onShare,
}: {
  article: Article;
  selected?: boolean;
  index: number;
  onToggleSaved: (article: Article) => void;
  onShare: (article: Article) => void;
}) {
  const router = useRouter();

  return (
    <div
      data-row-index={index}
      onClick={() => router.push(`/article/${article.id}`)}
      className="group flex cursor-pointer items-start gap-3 border-b px-5 py-[15px] transition-colors"
      style={{
        borderColor: "var(--line-soft)",
        background: selected ? "var(--bg-hover)" : "transparent",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-raised)")}
      onMouseLeave={(e) =>
        (e.currentTarget.style.background = selected ? "var(--bg-hover)" : "transparent")
      }
    >
      <span
        className="dot-unread mt-[9px]"
        style={{ opacity: article.is_read ? 0 : 1 }}
      />
      <div className="min-w-0 flex-1">
        <h3
          className="font-serif-nr text-[17px] leading-snug"
          style={{
            color: article.is_read ? "var(--ink-dim)" : "var(--ink)",
            fontWeight: article.is_read ? 400 : 500,
          }}
        >
          {article.title}
        </h3>
        <p
          className="font-mono-nr mt-1 truncate text-[11px]"
          style={{ color: "var(--ink-faint)" }}
        >
          {domainOf(article.url)}
          {article.author ? ` · ${article.author}` : ""}
          {article.published_at ? ` · ${timeAgo(article.published_at)}` : ""}
        </p>
        {(article.summary || article.excerpt) && (
          <p
            className="mt-1.5 line-clamp-2 text-[13px] leading-relaxed"
            style={{ color: "var(--ink-dim)" }}
          >
            {article.summary ? (
              <>
                <span
                  className="font-mono-nr mr-1.5 text-[10px]"
                  style={{ color: "var(--accent)" }}
                >
                  ✦
                </span>
                {article.summary.split("\n")[0]}
              </>
            ) : (
              article.excerpt
            )}
          </p>
        )}
      </div>
      <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
        <button
          className={`icon-btn ${article.is_saved ? "active" : ""}`}
          title={article.is_saved ? "Unsave" : "Save for later"}
          onClick={(e) => {
            e.stopPropagation();
            onToggleSaved(article);
          }}
        >
          <BookmarkIcon size={15} filled={article.is_saved} />
        </button>
        <button
          className="icon-btn"
          title="Share with a note"
          onClick={(e) => {
            e.stopPropagation();
            onShare(article);
          }}
        >
          <ShareIcon size={15} />
        </button>
        <a
          className="icon-btn"
          title="Open original"
          href={article.url}
          target="_blank"
          rel="noreferrer"
          onClick={(e) => e.stopPropagation()}
        >
          <ExternalIcon size={15} />
        </a>
      </div>
    </div>
  );
}
