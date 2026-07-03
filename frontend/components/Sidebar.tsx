"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher, type Feed } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import {
  BookmarkIcon,
  InboxIcon,
  LogoutIcon,
  PlusIcon,
  RssIcon,
  ShareIcon,
  TrashIcon,
  UsersIcon,
  XIcon,
} from "./icons";

function NavLink({
  href,
  active,
  icon,
  label,
  badge,
  badgeAccent,
}: {
  href: string;
  active: boolean;
  icon: React.ReactNode;
  label: string;
  badge?: number;
  badgeAccent?: boolean;
}) {
  return (
    <Link
      href={href}
      className="flex items-center gap-2.5 rounded-lg px-3 py-[7px] text-[13.5px] transition-colors"
      style={{
        background: active ? "var(--bg-hover)" : "transparent",
        color: active ? "var(--ink)" : "var(--ink-dim)",
      }}
    >
      <span style={{ color: active ? "var(--accent)" : "var(--ink-faint)" }}>
        {icon}
      </span>
      <span className="flex-1">{label}</span>
      {badge !== undefined && badge > 0 && (
        <span
          className="font-mono-nr rounded-full px-1.5 text-[10.5px] leading-[18px]"
          style={
            badgeAccent
              ? { background: "var(--accent)", color: "#17110a", fontWeight: 600 }
              : { color: "var(--ink-faint)" }
          }
        >
          {badge}
        </span>
      )}
    </Link>
  );
}

export default function Sidebar() {
  const { user, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const activeFeed = pathname === "/" ? searchParams.get("feed") : null;

  const { data: feeds } = useSWR<Feed[]>("/feeds", fetcher);
  const { data: unseen } = useSWR<{ count: number }>(
    "/shares/unseen-count",
    fetcher,
    { refreshInterval: 30_000 },
  );

  const [adding, setAdding] = useState(false);
  const [newUrl, setNewUrl] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const totalUnread = feeds?.reduce((sum, f) => sum + f.unread_count, 0) ?? 0;

  async function addFeed(e: React.FormEvent) {
    e.preventDefault();
    if (!newUrl.trim() || busy) return;
    setBusy(true);
    setAddError(null);
    try {
      const feed = await api<Feed>("/feeds", {
        method: "POST",
        body: { url: newUrl.trim() },
      });
      setNewUrl("");
      setAdding(false);
      mutate("/feeds");
      router.push(`/?feed=${feed.id}`);
    } catch (err) {
      setAddError(err instanceof Error ? err.message : "Could not add feed");
    } finally {
      setBusy(false);
    }
  }

  async function removeFeed(feedId: number) {
    await api(`/feeds/${feedId}`, { method: "DELETE" });
    mutate("/feeds");
    if (activeFeed === String(feedId)) router.push("/");
  }

  return (
    <aside
      className="flex h-screen w-[250px] shrink-0 flex-col border-r"
      style={{ borderColor: "var(--line-soft)", background: "var(--bg-inset)" }}
    >
      <div className="px-5 pb-4 pt-6">
        <Link href="/" className="wordmark text-[22px]">
          NewsRead<span className="dot">.</span>
        </Link>
      </div>

      <nav className="flex flex-col gap-0.5 px-2.5">
        <NavLink
          href="/"
          active={pathname === "/" && !activeFeed}
          icon={<InboxIcon />}
          label="Inbox"
          badge={totalUnread}
        />
        <NavLink
          href="/shared"
          active={pathname === "/shared"}
          icon={<UsersIcon />}
          label="Shared with me"
          badge={unseen?.count}
          badgeAccent
        />
        <NavLink
          href="/sent"
          active={pathname === "/sent"}
          icon={<ShareIcon />}
          label="Sent"
        />
        <NavLink
          href="/saved"
          active={pathname === "/saved"}
          icon={<BookmarkIcon />}
          label="Saved"
        />
      </nav>

      <div className="mt-7 flex items-center justify-between px-5">
        <span className="mono-label">Feeds</span>
        <button
          className="icon-btn"
          style={{ width: 22, height: 22 }}
          onClick={() => {
            setAdding((v) => !v);
            setAddError(null);
          }}
          title="Add feed"
        >
          {adding ? <XIcon size={13} /> : <PlusIcon size={13} />}
        </button>
      </div>

      {adding && (
        <form onSubmit={addFeed} className="fade-up mt-2 px-4">
          <input
            className="input"
            style={{ fontSize: 13, padding: "7px 10px" }}
            placeholder="https://example.com/feed.xml"
            value={newUrl}
            onChange={(e) => setNewUrl(e.target.value)}
            autoFocus
          />
          {addError && (
            <p className="mt-1.5 text-[12px]" style={{ color: "var(--danger)" }}>
              {addError}
            </p>
          )}
          <button className="btn btn-accent mt-2 w-full" disabled={busy} type="submit">
            {busy ? "Fetching…" : "Subscribe"}
          </button>
        </form>
      )}

      <div className="mt-2 flex-1 overflow-y-auto px-2.5 pb-3">
        {feeds?.length === 0 && !adding && (
          <p
            className="px-3 pt-2 text-[12.5px] leading-relaxed"
            style={{ color: "var(--ink-faint)" }}
          >
            No feeds yet. Add one with the + above.
          </p>
        )}
        {feeds?.map((feed) => {
          const active = activeFeed === String(feed.id);
          return (
            <div key={feed.id} className="group relative">
              <Link
                href={`/?feed=${feed.id}`}
                className="flex items-center gap-2.5 rounded-lg px-3 py-[7px] text-[13px] transition-colors"
                style={{
                  background: active ? "var(--bg-hover)" : "transparent",
                  color: active ? "var(--ink)" : "var(--ink-dim)",
                }}
              >
                <span style={{ color: active ? "var(--accent)" : "var(--ink-faint)" }}>
                  <RssIcon size={13} />
                </span>
                <span className="flex-1 truncate">{feed.title}</span>
                {feed.unread_count > 0 && (
                  <span
                    className="font-mono-nr text-[10.5px] group-hover:opacity-0"
                    style={{ color: "var(--ink-faint)" }}
                  >
                    {feed.unread_count}
                  </span>
                )}
              </Link>
              <button
                className="icon-btn absolute right-1.5 top-1/2 hidden -translate-y-1/2 group-hover:inline-flex"
                style={{ width: 24, height: 24 }}
                title="Unsubscribe"
                onClick={() => removeFeed(feed.id)}
              >
                <TrashIcon size={12} />
              </button>
            </div>
          );
        })}
      </div>

      <div
        className="flex items-center gap-2.5 border-t px-4 py-3.5"
        style={{ borderColor: "var(--line-soft)" }}
      >
        <div
          className="flex h-8 w-8 items-center justify-center rounded-full font-serif-nr text-[14px] italic"
          style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
        >
          {user?.name?.[0]?.toUpperCase() ?? "?"}
        </div>
        <div className="min-w-0 flex-1 leading-tight">
          <p className="truncate text-[13px]">{user?.name}</p>
          <p className="font-mono-nr truncate text-[11px]" style={{ color: "var(--ink-faint)" }}>
            @{user?.username}
          </p>
        </div>
        <button
          className="icon-btn"
          title="Sign out"
          onClick={() => {
            logout();
            router.push("/login");
          }}
        >
          <LogoutIcon size={15} />
        </button>
      </div>
    </aside>
  );
}
