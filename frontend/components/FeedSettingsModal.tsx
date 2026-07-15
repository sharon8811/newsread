"use client";

import { useState } from "react";
import { mutate } from "swr";
import { mutateArticleLists } from "./ArticleList";
import {
  api,
  type Feed,
  type FeedSettingsPatch,
  type SortOrder,
  type ViewMode,
} from "@/lib/api";
import { keys } from "@/lib/keys";
import { TrashIcon } from "./icons";
import Modal, { ModalHeader } from "./Modal";
import Button from "./ui/Button";
import ConfirmButton from "./ui/ConfirmButton";
import ErrorText from "./ui/ErrorText";
import Toggle from "./ui/Toggle";

const RETENTION_OPTIONS = [
  { value: 0, label: "Keep forever" },
  { value: 7, label: "1 week" },
  { value: 30, label: "1 month" },
  { value: 90, label: "3 months" },
  { value: 365, label: "1 year" },
];

const REFRESH_OPTIONS = [
  { value: 15, label: "Every 15 min" },
  { value: 30, label: "Every 30 min" },
  { value: 60, label: "Every hour" },
  { value: 180, label: "Every 3 hours" },
  { value: 360, label: "Every 6 hours" },
  { value: 1440, label: "Once a day" },
];

function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5">
      <div className="min-w-0">
        <p className="text-body">{label}</p>
        {hint && (
          <p className="mt-0.5 text-label" style={{ color: "var(--ink-faint)" }}>
            {hint}
          </p>
        )}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

export default function FeedSettingsModal({
  feed,
  onClose,
  onUnsubscribed,
}: {
  feed: Feed;
  onClose: () => void;
  onUnsubscribed?: () => void;
}) {
  const [title, setTitle] = useState(feed.title_override ?? "");
  const [view, setView] = useState<ViewMode | "default">(feed.view_override ?? "default");
  const [sort, setSort] = useState<SortOrder>(feed.sort_order ?? "newest");
  const [retention, setRetention] = useState(feed.retention_days ?? 0);
  const [muted, setMuted] = useState(feed.is_muted);
  const [aiEnabled, setAiEnabled] = useState(feed.ai_enabled);
  const [imageGenEnabled, setImageGenEnabled] = useState(feed.image_gen_enabled);
  const [refreshMinutes, setRefreshMinutes] = useState(feed.refresh_interval_minutes);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function buildPatch(): FeedSettingsPatch {
    const patch: FeedSettingsPatch = {};
    const trimmed = title.trim();
    if (trimmed !== (feed.title_override ?? "")) patch.title_override = trimmed || null;
    const viewValue = view === "default" ? null : view;
    if (viewValue !== feed.view_override) patch.view_override = viewValue;
    const sortValue = sort === "newest" ? null : sort;
    if (sortValue !== feed.sort_order) patch.sort_order = sortValue;
    const retentionValue = retention === 0 ? null : retention;
    if (retentionValue !== feed.retention_days) patch.retention_days = retentionValue;
    if (muted !== feed.is_muted) patch.is_muted = muted;
    if (aiEnabled !== feed.ai_enabled) patch.ai_enabled = aiEnabled;
    if (imageGenEnabled !== feed.image_gen_enabled)
      patch.image_gen_enabled = imageGenEnabled;
    if (refreshMinutes !== feed.refresh_interval_minutes)
      patch.refresh_interval_minutes = refreshMinutes;
    return patch;
  }

  async function save() {
    if (busy) return;
    const patch = buildPatch();
    if (Object.keys(patch).length === 0) {
      onClose();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api<Feed>(`/feeds/${feed.id}/settings`, { method: "PATCH", body: patch });
      mutateArticleLists();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save settings");
      setBusy(false);
    }
  }

  async function unsubscribe() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/feeds/${feed.id}`, { method: "DELETE" });
      mutate(keys.feeds);
      onUnsubscribed?.();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not unsubscribe");
      setBusy(false);
    }
  }

  const refreshOptions = REFRESH_OPTIONS.some((o) => o.value === feed.refresh_interval_minutes)
    ? REFRESH_OPTIONS
    : [
        ...REFRESH_OPTIONS,
        { value: feed.refresh_interval_minutes, label: `Every ${feed.refresh_interval_minutes} min` },
      ].sort((a, b) => a.value - b.value);

  return (
    <Modal
      onClose={onClose}
      contentClassName="max-h-[calc(100dvh-3rem)] overflow-y-auto p-6"
    >
        <ModalHeader eyebrow="Feed settings" title={feed.title} titleClassName="truncate" />

        <div className="mt-4">
          <label
            className="text-body-sm font-medium"
            style={{ color: "var(--ink-dim)" }}
            htmlFor="feed-title-input"
          >
            Custom name
          </label>
          <input
            id="feed-title-input"
            className="input mt-1.5"
            style={{ fontSize: 13.5 }}
            placeholder={feed.title_override ? feed.title : `${feed.title} (original name)`}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>

        <div className="mt-3 divide-y divide-[color:var(--line-soft)]">
          <Row label="View" hint="Layout used when reading this feed">
            <select
              className="input"
              style={{ fontSize: 13, width: 150 }}
              aria-label="View mode"
              value={view}
              onChange={(e) => setView(e.target.value as ViewMode | "default")}
            >
              <option value="default">My default</option>
              <option value="cards">Cards</option>
              <option value="list">List</option>
              <option value="stories">Stories</option>
            </select>
          </Row>

          <Row label="Sort order">
            <select
              className="input"
              style={{ fontSize: 13, width: 150 }}
              aria-label="Sort order"
              value={sort}
              onChange={(e) => setSort(e.target.value as SortOrder)}
            >
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
            </select>
          </Row>

          <Row label="Keep articles" hint="Saved articles are always kept">
            <select
              className="input"
              style={{ fontSize: 13, width: 150 }}
              aria-label="Retention"
              value={retention}
              onChange={(e) => setRetention(Number(e.target.value))}
            >
              {RETENTION_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Row>

          <Row label="Mute" hint="Hide from Inbox and unread counts">
            <Toggle checked={muted} onChange={setMuted} label="Mute feed" />
          </Row>

          <Row label="AI summaries" hint="Applies to everyone subscribed to this feed">
            <Toggle checked={aiEnabled} onChange={setAiEnabled} label="AI summaries" />
          </Row>

          <Row
            label="AI images"
            hint="Generate illustrations for articles without one; applies to everyone subscribed"
          >
            <Toggle
              checked={imageGenEnabled}
              onChange={setImageGenEnabled}
              label="AI images"
            />
          </Row>

          <Row label="Check for new articles" hint="Applies to everyone subscribed to this feed">
            <select
              className="input"
              style={{ fontSize: 13, width: 150 }}
              aria-label="Refresh interval"
              value={refreshMinutes}
              onChange={(e) => setRefreshMinutes(Number(e.target.value))}
            >
              {refreshOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Row>
        </div>

        <ErrorText className="mt-2">{error}</ErrorText>

        <div className="mt-5 flex items-center justify-between">
          <ConfirmButton
            onConfirm={unsubscribe}
            confirmLabel="Really unsubscribe?"
            disabled={busy}
          >
            <TrashIcon size={13} />
            Unsubscribe
          </ConfirmButton>
          <div className="flex items-center gap-2">
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button variant="primary" onClick={save} loading={busy}>
              {busy ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
    </Modal>
  );
}
