"use client";

import { memo } from "react";
import { useRouter } from "next/navigation";
import { imageSrc, type Article } from "@/lib/api";
import { domainOf, timeAgo } from "@/lib/format";
import EntityBadges from "./EntityBadges";
import GeneratingIndicator from "./GeneratingIndicator";
import ReadStateIndicator from "./ReadStateIndicator";
import ReadToggleButton from "./ReadToggleButton";
import { BookmarkIcon, ExternalIcon, EyeOffIcon, FolderIcon, ShareIcon } from "./icons";

// Memoized: the reading list re-renders on every selection move and
// scroll-past mark, and handler props are stable — only the touched card
// should re-render.
function ArticleCard({
  article,
  selected,
  index,
  onToggleSaved,
  onShare,
  onAddToProject,
  onNotInterested,
  onOpen,
  onToggleRead,
  showReadState = false,
}: {
  article: Article;
  selected?: boolean;
  index: number;
  onToggleSaved: (article: Article) => void;
  onShare: (article: Article) => void;
  onAddToProject: (article: Article) => void;
  onNotInterested: (article: Article) => void;
  onOpen?: (article: Article) => void;
  onToggleRead?: (article: Article) => void;
  showReadState?: boolean;
}) {
  const router = useRouter();
  const summary = article.summary_short || article.excerpt;

  return (
    <article
      data-row-index={index}
      onClick={() =>
        onOpen ? onOpen(article) : router.push(`/article/${article.id}`)
      }
      className="group flex cursor-pointer flex-col overflow-hidden rounded-lg border transition-all duration-150 hover:-translate-y-0.5"
      style={{
        borderColor: selected ? "var(--accent-border)" : "var(--line-soft)",
        background: "var(--bg-raised)",
        boxShadow: selected
          ? "0 0 0 3px var(--accent-soft), 0 2px 12px rgba(28,30,34,0.08)"
          : "0 1px 3px rgba(28,30,34,0.05)",
      }}
      onMouseEnter={(e) => {
        if (!selected) e.currentTarget.style.boxShadow = "0 6px 24px rgba(28,30,34,0.12)";
      }}
      onMouseLeave={(e) => {
        if (!selected) e.currentTarget.style.boxShadow = "0 1px 3px rgba(28,30,34,0.05)";
      }}
    >
      {/* The media frame appears once there is a real image — or right away,
          as a shimmering "generating" placeholder, while an AI illustration
          renders in the background (the list polls and the finished image
          fades in). Text-only articles with nothing on the way stay compact. */}
      {(article.image_url || article.image_pending) && (
        <div
          className={`relative aspect-[2/1] w-full shrink-0 overflow-hidden ${
            article.image_url ? "" : "shimmer"
          }`}
          style={{ background: "var(--bg-hover)" }}
        >
          {article.image_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={imageSrc(article.image_url)}
              alt=""
              loading="lazy"
              className="fade-in h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
              style={{ opacity: article.is_read ? 0.55 : 1 }}
              onError={(e) => {
                e.currentTarget.style.display = "none";
              }}
            />
          ) : (
            <GeneratingIndicator />
          )}
        </div>
      )}

      <div className="flex flex-1 flex-col px-5 pb-4 pt-4 sm:px-7 sm:pt-5">
        <p
          className="font-mono-nr flex items-center gap-2 truncate text-label"
          style={{ color: "var(--ink-faint)" }}
        >
          {showReadState ? (
            <>
              <ReadStateIndicator isRead={article.is_read} />
              <span aria-hidden="true">·</span>
            </>
          ) : (
            <span className="dot-unread" style={{ opacity: article.is_read ? 0 : 1 }} />
          )}
          <span className="truncate">
            {domainOf(article.url)}
            {article.published_at ? ` · ${timeAgo(article.published_at)}` : ""}
          </span>
        </p>

        <h3
          className="font-serif-nr mt-2 text-title leading-snug sm:text-title"
          style={{
            color: article.is_read ? "var(--ink-dim)" : "var(--ink)",
            fontWeight: article.is_read ? 400 : 500,
          }}
        >
          {article.title}
        </h3>

        {summary && (
          <p
            className="mt-2 line-clamp-3 text-body-lg leading-relaxed"
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
            {summary}
          </p>
        )}

        {article.entities.length > 0 && (
          <p className="mt-2">
            <EntityBadges entities={article.entities} />
          </p>
        )}

        <div className="mt-auto flex items-center gap-2 pt-3">
          <span
            className="font-mono-nr min-w-0 truncate text-label"
            style={{ color: "var(--ink-faint)" }}
          >
            {article.author ?? article.feed_title}
          </span>
          <div className="ml-auto flex shrink-0 items-center gap-0.5 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100">
            {onToggleRead && (
              <ReadToggleButton
                article={article}
                onToggle={onToggleRead}
                className="min-h-11 min-w-11 sm:min-h-0 sm:min-w-0"
              />
            )}
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
        </div>
      </div>
    </article>
  );
}

export default memo(ArticleCard);
