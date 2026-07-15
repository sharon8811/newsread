"use client";

import { useRouter } from "next/navigation";
import { mutate } from "swr";
import { api, type Share } from "@/lib/api";
import { domainOf, timeAgo } from "@/lib/format";
import Avatar from "./ui/Avatar";

export default function ShareCard({
  share,
  direction,
}: {
  share: Share;
  direction: "received" | "sent";
}) {
  const router = useRouter();
  const isNew = direction === "received" && share.seen_at === null;

  async function open() {
    if (isNew) {
      await api(`/shares/${share.id}/seen`, { method: "POST" }).catch(() => {});
      mutate("/shares/received");
      mutate("/shares/unseen-count");
    }
    router.push(`/article/${share.article.id}`);
  }

  return (
    <div
      onClick={open}
      className="group cursor-pointer border-b px-5 py-5 transition-colors"
      style={{ borderColor: "var(--line-soft)" }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-raised)")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
    >
      <div className="flex items-center gap-2.5">
        <Avatar
          name={
            direction === "received" ? share.from_user.name : share.to_users[0]?.name
          }
        />
        <p className="text-[13px]" style={{ color: "var(--ink-dim)" }}>
          {direction === "received" ? (
            <>
              <span style={{ color: "var(--ink)" }}>{share.from_user.name}</span>
              <span
                className="font-mono-nr text-[11.5px]"
                style={{ color: "var(--ink-faint)" }}
              >
                {" "}
                @{share.from_user.username}
              </span>{" "}
              shared this with you
            </>
          ) : (
            <>
              To{" "}
              {share.to_users.map((u, i) => (
                <span key={u.id}>
                  {i > 0 && ", "}
                  <span className="font-mono-nr text-[12px]" style={{ color: "var(--accent-bright)" }}>
                    @{u.username}
                  </span>
                </span>
              ))}
            </>
          )}
        </p>
        <span
          className="font-mono-nr ml-auto text-[11px]"
          style={{ color: "var(--ink-faint)" }}
        >
          {timeAgo(share.created_at)}
        </span>
        {isNew && <span className="dot-unread dot-new" />}
      </div>

      {share.note && <blockquote className="note-quote mt-3.5">{share.note}</blockquote>}

      <div className="mt-3.5 rounded-md border p-3.5" style={{ borderColor: "var(--line)" }}>
        <h3 className="font-serif-nr text-[16px] leading-snug">{share.article.title}</h3>
        <p
          className="font-mono-nr mt-1 text-[11px]"
          style={{ color: "var(--ink-faint)" }}
        >
          {domainOf(share.article.url)}
          {share.article.published_at
            ? ` · ${timeAgo(share.article.published_at)}`
            : ""}
        </p>
      </div>
    </div>
  );
}
