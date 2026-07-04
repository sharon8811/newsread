"use client";

import { useRouter } from "next/navigation";
import { type Article } from "@/lib/api";
import { domainOf, timeAgo } from "@/lib/format";

export default function ZenRow({
  article,
  index,
  selected,
  revealed,
}: {
  article: Article;
  index: number;
  selected: boolean;
  revealed: boolean;
}) {
  const router = useRouter();
  const summary = article.summary_short;

  return (
    <div className="group">
      <div
        data-row-index={index}
        onClick={() => router.push(`/article/${article.id}`)}
        className="flex cursor-pointer items-baseline gap-3 border-b px-5 py-[5px]"
        style={{
          borderColor: "var(--line-soft)",
          background: selected ? "var(--bg-hover)" : "transparent",
        }}
      >
        <span
          className="dot-unread shrink-0 self-center"
          style={{ opacity: article.is_read ? 0 : 1 }}
        />
        <span
          className="min-w-0 flex-1 truncate text-[13.5px]"
          style={{
            color: article.is_read ? "var(--ink-dim)" : "var(--ink)",
            fontWeight: article.is_read ? 400 : 500,
          }}
        >
          {article.title}
        </span>
        <span
          className="font-mono-nr shrink-0 whitespace-nowrap text-[10.5px]"
          style={{ color: "var(--ink-faint)" }}
        >
          {domainOf(article.url)}
          {article.published_at ? ` · ${timeAgo(article.published_at)}` : ""}
        </span>
      </div>
      {summary && (
        <p
          className={`${revealed ? "fade-up block" : "hidden group-hover:block"} border-b pb-1.5 pl-[38px] pr-5 pt-1 text-[12.5px] leading-relaxed`}
          style={{ color: "var(--ink-dim)", borderColor: "var(--line-soft)" }}
        >
          <span
            className="font-mono-nr mr-1.5 text-[10px]"
            style={{ color: "var(--accent)" }}
          >
            ✦
          </span>
          {summary}
        </p>
      )}
    </div>
  );
}
