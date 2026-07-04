"use client";

import { mutate } from "swr";
import { api, type Feed, type User, type ViewMode } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ListIcon, StoriesIcon, ZenIcon } from "./icons";

const MODES: { mode: ViewMode; label: string; Icon: typeof ListIcon }[] = [
  { mode: "list", label: "List view", Icon: ListIcon },
  { mode: "zen", label: "Zen view — dense headlines", Icon: ZenIcon },
  { mode: "stories", label: "Stories view — one at a time", Icon: StoriesIcon },
];

function setFeedOverride(feedId: number, override: ViewMode | null) {
  // Optimistic cache update, then persist and revalidate.
  mutate(
    "/feeds",
    (feeds?: Feed[]) =>
      feeds?.map((f) => (f.id === feedId ? { ...f, view_override: override } : f)),
    { revalidate: false },
  );
  return api<Feed>(`/feeds/${feedId}/view`, {
    method: "PATCH",
    body: { view_override: override },
  }).finally(() => mutate("/feeds"));
}

export default function ViewSwitcher({
  view,
  feed,
  onSwitch,
}: {
  view: ViewMode;
  feed: Feed | null;
  onSwitch?: (view: ViewMode) => void; // notify the page of the new effective view
}) {
  const { user, updateUser } = useAuth();

  async function setDefaultView(mode: ViewMode) {
    if (user) updateUser({ ...user, default_view: mode });
    const updated = await api<User>("/users/me", {
      method: "PATCH",
      body: { default_view: mode },
    });
    updateUser(updated);
  }

  function pick(mode: ViewMode) {
    if (mode === view) return;
    onSwitch?.(mode);
    if (feed) setFeedOverride(feed.id, mode);
    else setDefaultView(mode);
  }

  function resetOverride() {
    if (!feed) return;
    onSwitch?.(user?.default_view ?? "list");
    setFeedOverride(feed.id, null);
  }

  async function makeDefault() {
    if (!feed) return;
    onSwitch?.(view);
    await setDefaultView(view);
    setFeedOverride(feed.id, null);
  }

  const overridden =
    feed != null &&
    feed.view_override != null &&
    feed.view_override !== user?.default_view;

  return (
    <div className="flex items-center gap-2">
      <div
        className="flex rounded-lg border p-0.5"
        style={{ borderColor: "var(--line)", background: "var(--bg-inset)" }}
      >
        {MODES.map(({ mode, label, Icon }) => (
          <button
            key={mode}
            onClick={() => pick(mode)}
            title={label}
            aria-label={label}
            className="rounded-md px-2.5 py-1 transition-colors"
            style={{
              background: view === mode ? "var(--bg-hover)" : "transparent",
              color: view === mode ? "var(--ink)" : "var(--ink-faint)",
            }}
          >
            <Icon size={14} />
          </button>
        ))}
      </div>
      {overridden && (
        <span className="font-mono-nr text-[11px]" style={{ color: "var(--ink-faint)" }}>
          feed view ·{" "}
          <button
            className="underline decoration-dotted underline-offset-2"
            onClick={resetOverride}
            title="Use your default view for this feed"
          >
            reset
          </button>{" "}
          ·{" "}
          <button
            className="underline decoration-dotted underline-offset-2"
            onClick={makeDefault}
            title="Make this your default view everywhere"
          >
            make default
          </button>
        </span>
      )}
    </div>
  );
}
