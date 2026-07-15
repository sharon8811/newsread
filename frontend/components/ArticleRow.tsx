"use client";

import { memo, useState } from "react";
import { useRouter } from "next/navigation";
import { imageSrc, type Article } from "@/lib/api";
import { domainOf, timeAgo } from "@/lib/format";
import EntityBadges from "./EntityBadges";
import GeneratingIndicator from "./GeneratingIndicator";
import { BookmarkIcon, ExternalIcon, EyeOffIcon, FolderIcon, ShareIcon } from "./icons";

// Memoized: the reading list re-renders on every selection move and
// scroll-past mark, and handler props are stable — only the touched row
// should re-render.
function ArticleRow({
  article,
  selected,
  index,
  onToggleSaved,
  onShare,
  onAddToProject,
  onNotInterested,
  onOpen,
}: {
  article: Article;
  selected?: boolean;
  index: number;
  onToggleSaved: (article: Article) => void;
  onShare: (article: Article) => void;
  onAddToProject: (article: Article) => void;
  onNotInterested: (article: Article) => void;
  onOpen?: (article: Article) => void;
}) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);

  // Reading level 1: one-liner. Level 2: expand in place. Level 3 lives on the article page.
  const oneLiner = article.summary_short || article.excerpt;
  const readMore = article.summary_medium;

  return (
    <div
      data-row-index={index}
      onClick={() =>
        onOpen ? onOpen(article) : router.push(`/article/${article.id}`)
      }
      className="group flex cursor-pointer items-start gap-3 border-b px-4 py-[18px] transition-colors sm:gap-4 sm:px-6"
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
        className="dot-unread mt-[10px]"
        style={{ opacity: article.is_read ? 0 : 1 }}
      />
      <div className="min-w-0 flex-1">
        <h3
          className="font-serif-nr text-lead leading-snug sm:text-title"
          style={{
            color: article.is_read ? "var(--ink-dim)" : "var(--ink)",
            fontWeight: article.is_read ? 400 : 500,
          }}
        >
          {article.title}
        </h3>
        <p
          className="font-mono-nr mt-1.5 truncate text-label"
          style={{ color: "var(--ink-faint)" }}
        >
          {domainOf(article.url)}
          {article.author ? ` · ${article.author}` : ""}
          {article.published_at ? ` · ${timeAgo(article.published_at)}` : ""}
        </p>

        {article.entities.length > 0 && (
          <p className="mt-1">
            <EntityBadges entities={article.entities} />
          </p>
        )}

        {oneLiner && (
          <p
            className="mt-2 line-clamp-2 text-body-lg leading-relaxed"
            style={{ color: "var(--ink-dim)" }}
          >
            {article.summary_short && (
              <span
                className="font-mono-nr mr-1.5 text-caption"
                style={{ color: "var(--accent)" }}
              >
                ✦
              </span>
            )}
            {oneLiner}
          </p>
        )}

        {readMore && (
          <button
            className="font-mono-nr mt-2 text-label transition-colors"
            style={{ color: expanded ? "var(--ink-faint)" : "var(--accent)" }}
            onClick={(e) => {
              e.stopPropagation();
              setExpanded((v) => !v);
            }}
          >
            {expanded ? "− show less" : "+ read more"}
          </button>
        )}

        {expanded && readMore && (
          <p
            className="fade-up mt-2 border-l pl-3 text-body-lg leading-relaxed"
            style={{ color: "var(--ink)", borderColor: "var(--accent-border)" }}
          >
            {readMore}
          </p>
        )}
      </div>

      <div className="hidden items-center gap-0.5 self-center opacity-0 transition-opacity group-hover:opacity-100 sm:flex">
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
        <button
          className="icon-btn"
          title="Add to project"
          onClick={(e) => {
            e.stopPropagation();
            onAddToProject(article);
          }}
        >
          <FolderIcon size={15} />
        </button>
        <button
          className="icon-btn"
          title="Not interested"
          onClick={(e) => {
            e.stopPropagation();
            onNotInterested(article);
          }}
        >
          <EyeOffIcon size={15} />
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

      {/* The thumbnail frame appears once a real image exists — or right away,
          shimmering, while an AI illustration renders in the background (the
          list polls and the image pops in when it lands). Text-only articles
          with nothing on the way never carry an empty box. */}
      {(article.image_url || article.image_pending) && (
        <div
          className={`relative h-[58px] w-[86px] shrink-0 overflow-hidden rounded-lg border sm:h-[84px] sm:w-[126px] ${
            article.image_url ? "" : "shimmer"
          }`}
          style={{ borderColor: "var(--line-soft)", background: "var(--bg-hover)" }}
        >
          {article.image_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={imageSrc(article.image_url)}
              alt=""
              loading="lazy"
              className="fade-in h-full w-full object-cover"
              style={{ opacity: article.is_read ? 0.6 : 1 }}
              onError={(e) => {
                e.currentTarget.style.display = "none";
              }}
            />
          ) : (
            <GeneratingIndicator compact />
          )}
        </div>
      )}
    </div>
  );
}

export default memo(ArticleRow);
