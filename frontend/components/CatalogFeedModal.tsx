"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { type CatalogEntry, type CatalogPreviewItem, type SubscribeOptions } from "@/lib/api";
import { fetchPreview, previewErrorMessage, type LoadedPreview } from "@/lib/feedPreview";
import { formatFeedType, freshness, timeAgo } from "@/lib/format";
import SubscribeQuickSettings, {
  DEFAULT_SUBSCRIBE_SETTINGS,
  toSubscribeOptions,
} from "./SubscribeQuickSettings";
import { CheckIcon, ExternalIcon, PlusIcon, XIcon } from "./icons";
import ErrorText from "./ui/ErrorText";

/** A preview story row; titles without a resolvable link render as plain text
 * instead of dead anchors (some feeds publish guid-only items). */
export function StoryRow({ item }: { item: CatalogPreviewItem }) {
  const body = (
    <>
      <span className="flex items-baseline justify-between gap-3">
        <span className={`text-[13.5px] font-medium leading-snug${item.url ? " group-hover:underline" : ""}`}>
          {item.title}
        </span>
        {item.published_at && (
          <span className="shrink-0 font-mono-nr text-[10.5px]" style={{ color: "var(--ink-faint)" }}>
            {timeAgo(item.published_at)}
          </span>
        )}
      </span>
      {item.summary && (
        <span className="mt-0.5 block line-clamp-2 text-[12px] leading-relaxed" style={{ color: "var(--ink-dim)" }}>
          {item.summary}
        </span>
      )}
    </>
  );
  if (!item.url) return <span className="block">{body}</span>;
  return (
    <a href={item.url} target="_blank" rel="noreferrer" className="group block">
      {body}
    </a>
  );
}

export default function CatalogFeedModal({
  entry,
  busy,
  disabled,
  error,
  onSubscribe,
  onClose,
}: {
  entry: CatalogEntry;
  busy: boolean;
  disabled: boolean;
  error: string | null;
  onSubscribe: (options: SubscribeOptions) => void;
  onClose: () => void;
}) {
  // Fetched in the reader's browser when the publisher allows it; the key
  // doubles as the server fallback path for feeds that block cross-origin reads.
  const previewKey = `/catalog/${entry.id}/preview`;
  // A preview is a one-shot snapshot: on failure show the error state instead
  // of hammering the publisher (and our fallback endpoint) with retries.
  const { data: preview, error: previewError, isLoading } = useSWR<LoadedPreview>(
    previewKey,
    () => fetchPreview(entry.url, previewKey),
    { shouldRetryOnError: false, revalidateOnFocus: false },
  );
  const [settings, setSettings] = useState(DEFAULT_SUBSCRIBE_SETTINGS);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const updated = freshness(entry.latest_item_at);
  const cachedStories = entry.preview_items;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6"
      style={{ background: "var(--bg-scrim)", backdropFilter: "blur(4px)" }}
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={entry.title}
        className="fade-up flex max-h-[min(680px,90vh)] w-full max-w-[560px] flex-col rounded-lg border"
        style={{
          background: "var(--bg-raised)",
          borderColor: "var(--line)",
          boxShadow: "var(--shadow-modal)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="border-b p-6 pb-4" style={{ borderColor: "var(--line-soft)" }}>
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="mono-label">
                {entry.category} · {formatFeedType(entry.content_type)}
              </p>
              <h2 className="font-serif-nr mt-1.5 text-[20px] leading-snug">{entry.title}</h2>
              {entry.site_url ? (
                <a
                  href={entry.site_url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-1 inline-flex max-w-full items-center gap-1 font-mono-nr text-[11.5px] hover:underline"
                  style={{ color: "var(--ink-faint)" }}
                >
                  <span className="truncate">{entry.source_host}</span>
                  <ExternalIcon size={11} />
                </a>
              ) : (
                <p className="mt-1 truncate font-mono-nr text-[11.5px]" style={{ color: "var(--ink-faint)" }}>
                  {entry.source_host}
                </p>
              )}
            </div>
            <button className="icon-btn shrink-0" aria-label="Close" onClick={onClose}>
              <XIcon size={16} />
            </button>
          </div>
          <div
            className="mt-3 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11px]"
            style={{ color: "var(--ink-faint)" }}
          >
            {updated && <span>{updated}</span>}
            {entry.item_count !== null && (
              <span>{entry.item_count} recent {entry.item_count === 1 ? "item" : "items"}</span>
            )}
            {entry.subscriber_count > 0 && (
              <span>{entry.subscriber_count} {entry.subscriber_count === 1 ? "reader" : "readers"}</span>
            )}
            {entry.health_status === "healthy" && <span style={{ color: "var(--accent)" }}>Healthy</span>}
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
          {entry.description && (
            <p className="mb-4 text-[13px] leading-relaxed" style={{ color: "var(--ink-dim)" }}>
              {entry.description}
            </p>
          )}
          <p className="mono-label">Latest stories</p>
          {isLoading && (
            <div className="mt-2 space-y-2" aria-label="Loading stories">
              {Array.from({ length: 3 }).map((_, index) => (
                <div
                  key={index}
                  className="h-12 animate-pulse rounded-md"
                  style={{ background: "var(--bg-inset)" }}
                />
              ))}
            </div>
          )}
          {preview && preview.items.length === 0 && (
            <p className="mt-2 text-[12.5px]" style={{ color: "var(--ink-faint)" }}>
              This feed has no stories right now.
            </p>
          )}
          {preview && preview.items.length > 0 && (
            <ul className="mt-1">
              {preview.items.map((item, index) => (
                <li
                  key={`${item.url ?? item.title}-${index}`}
                  className="border-b py-2.5 last:border-b-0"
                  style={{ borderColor: "var(--line-soft)" }}
                >
                  <StoryRow item={item} />
                </li>
              ))}
            </ul>
          )}
          {previewError && cachedStories.length > 0 && (
            <>
              <p className="mt-2 text-[11.5px]" style={{ color: "var(--ink-faint)" }}>
                Live preview is unavailable; showing a recent snapshot.
              </p>
              <ul className="mt-1">
                {cachedStories.map((item, index) => (
                  <li
                    key={`${item.url ?? item.title}-${index}`}
                    className="flex items-baseline justify-between gap-3 border-b py-2.5 last:border-b-0"
                    style={{ borderColor: "var(--line-soft)" }}
                  >
                    <span className="text-[13px] leading-snug">{item.title}</span>
                    {item.published_at && (
                      <span className="shrink-0 font-mono-nr text-[10.5px]" style={{ color: "var(--ink-faint)" }}>
                        {timeAgo(item.published_at)}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
          {previewError && cachedStories.length === 0 && (
            <ErrorText className="mt-2">
              {previewErrorMessage(previewError, "Could not load stories from this feed right now.")}
            </ErrorText>
          )}
        </div>

        <footer className="border-t px-6 py-4" style={{ borderColor: "var(--line-soft)" }}>
          {!entry.subscribed && (
            <div className="mb-3">
              <SubscribeQuickSettings value={settings} onChange={setSettings} disabled={disabled} />
            </div>
          )}
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate font-mono-nr text-[10.5px]" style={{ color: "var(--ink-faint)" }}>
                {entry.url}
              </p>
              {error && (
                <ErrorText className="mt-1">
                  {error}
                </ErrorText>
              )}
            </div>
            {entry.subscribed ? (
              <Link href={`/?feed=${entry.feed_id}`} className="btn shrink-0" style={{ color: "var(--accent)" }}>
                <CheckIcon size={13} /> View feed
              </Link>
            ) : (
              <button
                className="btn btn-accent shrink-0"
                disabled={disabled}
                onClick={() => onSubscribe(toSubscribeOptions(settings))}
              >
                {busy ? "Subscribing…" : <><PlusIcon size={13} /> Subscribe</>}
              </button>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}
