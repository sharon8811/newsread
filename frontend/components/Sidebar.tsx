"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState, useSyncExternalStore } from "react";
import { api, type Feed } from "@/lib/api";
import {
  addFeedToCache,
  useAiSettings,
  useFeeds,
  useHistorySummary,
  useProjects,
  useServerConfig,
  useUnseenShareCount,
} from "@/lib/queries";
import { useMutation } from "@/lib/useMutation";
import { useAuth } from "@/lib/auth";
import FeedSettingsModal from "./FeedSettingsModal";
import Avatar from "./ui/Avatar";
import ErrorText from "./ui/ErrorText";
import { isYouTubeChannelFeed } from "@/lib/youtube";
import {
  ActivityIcon,
  BookmarkIcon,
  ChevronUpIcon,
  CompassIcon,
  FolderIcon,
  GearIcon,
  InboxIcon,
  LinkIcon,
  ListIcon,
  LogoutIcon,
  MuteIcon,
  PlusIcon,
  RssIcon,
  ShareIcon,
  SparkleIcon,
  UsersIcon,
  XIcon,
  YouTubeIcon,
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
      className="flex items-center gap-2.5 rounded-md px-3 py-[7px] text-body transition-colors"
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
          className="font-mono-nr rounded-full px-1.5 text-caption leading-[18px]"
          style={
            badgeAccent
              ? { background: "var(--accent)", color: "var(--accent-ink)", fontWeight: 600 }
              : { color: "var(--ink-faint)" }
          }
        >
          {badge}
        </span>
      )}
    </Link>
  );
}

function FeedRow({
  feed,
  active,
  icon,
  indented,
  onSettings,
}: {
  feed: Feed;
  active: boolean;
  icon: React.ReactNode;
  indented?: boolean;
  onSettings: () => void;
}) {
  return (
    <div className="group relative">
      <Link
        href={`/?feed=${feed.id}`}
        className={`flex items-center gap-2.5 rounded-md py-[7px] text-body transition-colors ${
          indented ? "pl-7 pr-3" : "px-3"
        }`}
        style={{
          background: active ? "var(--bg-hover)" : "transparent",
          color: active ? "var(--ink)" : "var(--ink-dim)",
        }}
      >
        <span style={{ color: active ? "var(--accent)" : "var(--ink-faint)" }}>{icon}</span>
        <span
          className="flex-1 truncate"
          style={feed.is_muted ? { color: "var(--ink-faint)" } : undefined}
        >
          {feed.title}
        </span>
        {feed.is_muted ? (
          <span
            className="group-hover:opacity-0"
            style={{ color: "var(--ink-faint)" }}
            title="Muted"
          >
            <MuteIcon size={11} />
          </span>
        ) : (
          feed.unread_count > 0 && (
            <span
              className="font-mono-nr text-caption group-hover:opacity-0"
              style={{ color: "var(--ink-faint)" }}
            >
              {feed.unread_count}
            </span>
          )
        )}
      </Link>
      {/* Visibility via opacity, not `hidden`: .icon-btn is unlayered
          CSS whose display:inline-flex outranks the layered utility. */}
      <button
        className="icon-btn pointer-events-none absolute right-1.5 top-1/2 -translate-y-1/2 opacity-0 group-hover:pointer-events-auto group-hover:opacity-100"
        style={{ width: 24, height: 24 }}
        title="Feed settings"
        onClick={onSettings}
      >
        <GearIcon size={12} />
      </button>
    </div>
  );
}

// Whether the YouTube group is expanded, remembered across visits. Absent key
// means expanded, so the group behaves like the flat list it grew out of.
const YOUTUBE_OPEN_KEY = "newsread_youtube_group_open";

// The app shell keeps both sidebars mounted at once (desktop rail and mobile
// drawer, hidden by CSS), so this can't live in component state: collapsing
// one has to move the other, not just the storage key. Storage stays the
// source of truth, with a memory fallback for when it is unavailable.
let youTubeOpenFallback = true;
const youTubeListeners = new Set<() => void>();

function loadYouTubeOpen(): boolean {
  try {
    return localStorage.getItem(YOUTUBE_OPEN_KEY) !== "0";
  } catch {
    return youTubeOpenFallback;
  }
}

function storeYouTubeOpen(open: boolean) {
  try {
    localStorage.setItem(YOUTUBE_OPEN_KEY, open ? "1" : "0");
  } catch {
    // Storage disabled (private browsing): the group still toggles for this
    // session, it just forgets between visits.
    youTubeOpenFallback = open;
  }
  for (const listener of youTubeListeners) listener();
}

function subscribeYouTubeOpen(listener: () => void) {
  youTubeListeners.add(listener);
  return () => {
    youTubeListeners.delete(listener);
  };
}

export default function Sidebar() {
  const { user, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const activeFeed = pathname === "/" ? searchParams.get("feed") : null;

  const { data: feeds } = useFeeds();
  const { data: unseen } = useUnseenShareCount({ refreshInterval: 30_000 });
  const { data: projects } = useProjects({ refreshInterval: 30_000 });
  const projectUnseen = projects?.reduce((sum, p) => sum + p.unseen_count, 0) ?? 0;
  // AI usage is only tracked for calls on the user's own key, so the page is
  // only offered once they've saved one.
  const { data: aiSettings } = useAiSettings();
  const { data: config } = useServerConfig();
  const historyEnabled = config?.browser_history_enabled === true;
  const { data: historySummary } = useHistorySummary(historyEnabled);

  const [adding, setAdding] = useState(false);
  const [newUrl, setNewUrl] = useState("");
  const [settingsFeed, setSettingsFeed] = useState<Feed | null>(null);
  // Reads localStorage: the app shell only mounts the sidebar once auth has
  // resolved on the client, so the server snapshot is never the real answer.
  const youTubeOpen = useSyncExternalStore(subscribeYouTubeOpen, loadYouTubeOpen, () => true);

  const totalUnread =
    feeds?.reduce((sum, f) => (f.is_muted ? sum : sum + f.unread_count), 0) ?? 0;

  const youTubeFeeds = feeds?.filter((f) => isYouTubeChannelFeed(f.url)) ?? [];
  const otherFeeds = feeds?.filter((f) => !isYouTubeChannelFeed(f.url)) ?? [];
  // Only shown while the group is collapsed — expanded, each channel carries
  // its own count and a second total would double-report.
  const youTubeUnread = youTubeFeeds.reduce(
    (sum, f) => (f.is_muted ? sum : sum + f.unread_count),
    0,
  );

  function toggleYouTube() {
    storeYouTubeOpen(!youTubeOpen);
  }

  const {
    run: addFeed,
    busy,
    error: addError,
    setError: setAddError,
  } = useMutation(
    (url: string) => api<Feed>("/feeds", { method: "POST", body: { url } }),
    {
      fallbackError: "Could not add feed",
      onSuccess(feed) {
        setNewUrl("");
        setAdding(false);
        addFeedToCache(feed);
        router.push(`/?feed=${feed.id}`);
      },
    },
  );

  function submitFeed(e: React.FormEvent) {
    e.preventDefault();
    if (newUrl.trim()) addFeed(newUrl.trim());
  }


  return (
    <aside
      className="flex h-dvh w-[250px] shrink-0 flex-col border-r"
      style={{ borderColor: "var(--line-soft)", background: "var(--bg-inset)" }}
    >
      <div className="px-5 pb-4 pt-6">
        <Link href="/" className="wordmark text-display">
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
        <NavLink
          href="/imported"
          active={pathname === "/imported"}
          icon={<LinkIcon />}
          label="Imported"
        />
        {historyEnabled &&
          (historySummary?.has_active_connection || historySummary?.has_history) && (
            <NavLink
              href="/history"
              active={pathname === "/history"}
              icon={<ListIcon />}
              label="History"
            />
          )}
        <NavLink
          href="/catalog"
          active={pathname === "/catalog"}
          icon={<CompassIcon />}
          label="Catalog"
        />
        <NavLink
          href="/projects"
          active={pathname.startsWith("/projects")}
          icon={<FolderIcon />}
          label="Projects"
          badge={projectUnseen}
          badgeAccent
        />
        <NavLink
          href="/activity"
          active={pathname === "/activity"}
          icon={<ActivityIcon />}
          label="Activity"
        />
        {aiSettings?.configured && (
          <NavLink
            href="/usage"
            active={pathname === "/usage"}
            icon={<SparkleIcon />}
            label="AI usage"
          />
        )}
        {(user?.role === "owner" || user?.role === "admin") && (
          <NavLink
            href="/admin"
            active={pathname.startsWith("/admin")}
            icon={<UsersIcon />}
            label="Admin"
          />
        )}
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
        <form onSubmit={submitFeed} className="fade-up mt-2 px-4">
          <input
            className="input"
            style={{ fontSize: 13, padding: "7px 10px" }}
            // A site URL works too — the server resolves the feed it advertises.
            placeholder="example.com or its feed URL"
            value={newUrl}
            onChange={(e) => setNewUrl(e.target.value)}
            autoFocus
          />
          <ErrorText className="mt-1.5">{addError}</ErrorText>
          <button className="btn btn-accent mt-2 w-full" disabled={busy} type="submit">
            {busy ? "Fetching…" : "Subscribe"}
          </button>
          {/* The way out of a failed paste: never hide the catalog behind the
              form that just rejected their URL. */}
          <p className="mt-2 text-body-sm leading-relaxed" style={{ color: "var(--ink-faint)" }}>
            Or browse the{" "}
            <Link href="/catalog" className="underline" style={{ color: "var(--accent)" }}>
              catalog
            </Link>
            .
          </p>
        </form>
      )}

      <div className="mt-2 flex-1 overflow-y-auto px-2.5 pb-3">
        {feeds?.length === 0 && !adding && (
          <p
            className="px-3 pt-2 text-body-sm leading-relaxed"
            style={{ color: "var(--ink-faint)" }}
          >
            No feeds yet. Add one with the + above, or browse the{" "}
            <Link href="/catalog" className="underline" style={{ color: "var(--accent)" }}>
              catalog
            </Link>
            .
          </p>
        )}
        {otherFeeds.map((feed) => (
          <FeedRow
            key={feed.id}
            feed={feed}
            active={activeFeed === String(feed.id)}
            icon={<RssIcon size={13} />}
            onSettings={() => setSettingsFeed(feed)}
          />
        ))}

        {/* Followed YouTube channels live in one collapsible folder so a
            handful of them can't crowd out the rest of the feed list. */}
        {youTubeFeeds.length > 0 && (
          <div>
            <button
              className="flex w-full items-center gap-2.5 rounded-md px-3 py-[7px] text-left text-body transition-colors"
              style={{ color: "var(--ink-dim)" }}
              onClick={toggleYouTube}
              aria-expanded={youTubeOpen}
              title={youTubeOpen ? "Collapse YouTube channels" : "Expand YouTube channels"}
            >
              <span style={{ color: "var(--ink-faint)" }}>
                <YouTubeIcon size={13} />
              </span>
              <span className="flex-1 truncate">YouTube channels</span>
              {!youTubeOpen && youTubeUnread > 0 && (
                <span
                  className="font-mono-nr text-caption"
                  style={{ color: "var(--ink-faint)" }}
                >
                  {youTubeUnread}
                </span>
              )}
              <span
                className="transition-transform"
                style={{
                  color: "var(--ink-faint)",
                  transform: youTubeOpen ? undefined : "rotate(180deg)",
                }}
              >
                <ChevronUpIcon size={12} />
              </span>
            </button>
            {youTubeOpen &&
              youTubeFeeds.map((feed) => (
                <FeedRow
                  key={feed.id}
                  feed={feed}
                  active={activeFeed === String(feed.id)}
                  icon={<YouTubeIcon size={13} />}
                  indented
                  onSettings={() => setSettingsFeed(feed)}
                />
              ))}
          </div>
        )}
      </div>

      {settingsFeed && (
        <FeedSettingsModal
          feed={settingsFeed}
          onClose={() => setSettingsFeed(null)}
          onUnsubscribed={() => {
            if (activeFeed === String(settingsFeed.id)) router.push("/");
          }}
        />
      )}

      <div
        className="flex items-center gap-2.5 border-t px-4 py-3.5"
        style={{ borderColor: "var(--line-soft)" }}
      >
        <Avatar name={user?.name} size="lg" />
        <div className="min-w-0 flex-1 leading-tight">
          <p className="truncate text-body">{user?.name}</p>
          <p className="font-mono-nr truncate text-label" style={{ color: "var(--ink-faint)" }}>
            @{user?.username}
          </p>
        </div>
        <Link href="/settings" className="icon-btn" title="Settings">
          <GearIcon size={15} />
        </Link>
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
