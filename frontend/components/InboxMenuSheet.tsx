"use client";

import Modal, { ModalTitle } from "./Modal";
import ViewSwitcher from "./ViewSwitcher";
import { CheckAllIcon, GearIcon, RefreshIcon } from "./icons";
import type { Feed, ViewMode } from "@/lib/api";

// Everything the inbox header used to spend three rows on, on phones. The bar
// itself stays one fixed-height row, which is what keeps assisted scrolling and
// scroll-past auto-read (both measured against the header) honest.
export default function InboxMenuSheet({
  feed,
  view,
  tab,
  onTab,
  onView,
  onRefresh,
  onSettings,
  onMarkAllRead,
  onClose,
}: {
  feed: Feed | null;
  view: ViewMode;
  tab: "unread" | "all";
  onTab: (tab: "unread" | "all") => void;
  onView: (view: ViewMode) => void;
  onRefresh: () => void;
  onSettings: () => void;
  onMarkAllRead: () => void;
  onClose: () => void;
}) {
  function run(action: () => void) {
    action();
    onClose();
  }

  return (
    <Modal placement="drawer" onClose={onClose} contentClassName="h-auto max-h-[88dvh] pb-6">
      <div className="px-5 pt-5">
        <ModalTitle className="mono-label">{feed ? feed.title : "Inbox"}</ModalTitle>

        {view !== "stories" && (
          <div
            className="mt-4 flex rounded-md border p-0.5"
            style={{ borderColor: "var(--line)", background: "var(--bg-inset)" }}
          >
            {(["unread", "all"] as const).map((t) => (
              <button
                key={t}
                onClick={() => run(() => onTab(t))}
                className="flex-1 rounded px-3.5 py-2 text-body-sm font-medium capitalize transition-colors"
                style={{
                  background: tab === t ? "var(--bg-raised)" : "transparent",
                  color: tab === t ? "var(--ink)" : "var(--ink-faint)",
                }}
              >
                {t}
              </button>
            ))}
          </div>
        )}

        <div className="mt-4">
          <ViewSwitcher
            view={view}
            feed={feed}
            onSwitch={(next) => run(() => onView(next))}
          />
        </div>

        <div className="mt-5 flex flex-col gap-2 border-t pt-4" style={{ borderColor: "var(--line-soft)" }}>
          {feed && (
            <button className="btn min-h-11 justify-start" onClick={() => run(onRefresh)}>
              <RefreshIcon size={14} />
              Refresh feed
            </button>
          )}
          {feed && (
            <button className="btn min-h-11 justify-start" onClick={() => run(onSettings)}>
              <GearIcon size={14} />
              Feed settings
            </button>
          )}
          <button className="btn min-h-11 justify-start" onClick={() => run(onMarkAllRead)}>
            <CheckAllIcon size={15} />
            Mark all read
          </button>
        </div>
      </div>
    </Modal>
  );
}
