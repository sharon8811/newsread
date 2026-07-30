"use client";

import { timeAgo } from "@/lib/format";

/** One compact "from your feeds" reference row — related coverage under an
 * article, and the article list on an entity page. Sans rather than the
 * editorial serif: these are references, not headlines, and they stay legible
 * at phone widths where a truncated serif line reads as noise. */
export default function FeedArticleRow({
  title,
  feedTitle,
  publishedAt,
  isRead,
  badge,
  onClick,
}: {
  title: string;
  feedTitle: string;
  publishedAt: string | null;
  isRead: boolean;
  /** Optional trailing chip on the metadata line (e.g. the SAME STORY tier). */
  badge?: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <div
      className="cursor-pointer rounded-md border p-3.5 transition-colors hover:bg-[var(--bg-hover)]"
      style={{ borderColor: "var(--line)", background: "var(--bg-raised)" }}
      onClick={onClick}
    >
      <div className="flex items-start gap-2">
        {!isRead && <span className="dot-unread mt-[7px]" />}
        <p
          dir="auto"
          className="line-clamp-2 min-w-0 flex-1 text-body-lg font-medium leading-snug"
        >
          {title}
        </p>
      </div>
      {/* The badge rides the metadata line so the title keeps the full card
          width on both of its lines, and only the feed name truncates — the
          timestamp stays put however long the feed title is. */}
      <div className="mt-1.5 flex items-center gap-2">
        <p
          className="font-mono-nr flex min-w-0 flex-1 items-center gap-1.5 text-label"
          style={{ color: "var(--ink-dim)" }}
        >
          <span className="truncate">{feedTitle}</span>
          {publishedAt && <span className="shrink-0">· {timeAgo(publishedAt)}</span>}
        </p>
        {badge}
      </div>
    </div>
  );
}
