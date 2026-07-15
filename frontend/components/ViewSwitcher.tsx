"use client";

import { mutate } from "swr";
import { api, type Feed, type User, type ViewMode } from "@/lib/api";
import { keys } from "@/lib/keys";
import { useAuth } from "@/lib/auth";
import { CardsIcon, ListIcon, StoriesIcon } from "./icons";

const MODES: { mode: ViewMode; label: string; Icon: typeof ListIcon }[] = [
  { mode: "cards", label: "Cards view", Icon: CardsIcon },
  { mode: "list", label: "List view", Icon: ListIcon },
  { mode: "stories", label: "Stories view — one at a time", Icon: StoriesIcon },
];

function setFeedOverride(feedId: number, override: ViewMode | null) {
  // Optimistic cache update; SWR rolls it back if the PATCH fails, then
  // revalidates either way.
  const apply = (feeds?: Feed[]) =>
    feeds?.map((f) => (f.id === feedId ? { ...f, view_override: override } : f));
  return mutate(
    keys.feeds,
    async (feeds?: Feed[]) => {
      await api<Feed>(`/feeds/${feedId}/settings`, {
        method: "PATCH",
        body: { view_override: override },
      });
      return apply(feeds) ?? [];
    },
    { optimisticData: (feeds?: Feed[]) => apply(feeds) ?? [], rollbackOnError: true },
  );
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
    onSwitch?.(user?.default_view ?? "cards");
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
        className="flex rounded-md border p-0.5"
        style={{ borderColor: "var(--line)", background: "var(--bg-inset)" }}
      >
        {MODES.map(({ mode, label, Icon }) => (
          <button
            key={mode}
            onClick={() => pick(mode)}
            title={label}
            aria-label={label}
            className="rounded px-2.5 py-1 transition-colors"
            style={{
              background: view === mode ? "var(--bg-raised)" : "transparent",
              color: view === mode ? "var(--ink)" : "var(--ink-faint)",
              boxShadow: view === mode ? "0 1px 2px rgba(28,30,34,0.08)" : "none",
            }}
          >
            <Icon size={14} />
          </button>
        ))}
      </div>
      {overridden && (
        <span className="font-mono-nr text-label" style={{ color: "var(--ink-faint)" }}>
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
