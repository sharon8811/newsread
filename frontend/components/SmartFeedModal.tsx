"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import useSWR, { mutate } from "swr";
import {
  api,
  fetcher,
  type Feed,
  type SmartFeed,
  type SmartFeedResolve,
} from "@/lib/api";
import { fetchPreview, type LoadedPreview } from "@/lib/feedPreview";
import SubscribeQuickSettings, {
  DEFAULT_SUBSCRIBE_SETTINGS,
  toSubscribeOptions,
} from "./SubscribeQuickSettings";
import { StoryRow } from "./CatalogFeedModal";
import { CheckIcon, ExternalIcon, PlusIcon, XIcon } from "./icons";

/** Subscribe to a topic-parameterized source (any subreddit, news query…):
 * type a topic — or paste the topic's page URL — preview it live, subscribe. */
export default function SmartFeedModal({
  provider,
  onClose,
}: {
  provider: SmartFeed;
  onClose: () => void;
}) {
  const [topic, setTopic] = useState("");
  const [debounced, setDebounced] = useState("");
  const [settings, setSettings] = useState(DEFAULT_SUBSCRIBE_SETTINGS);
  const [busy, setBusy] = useState(false);
  // Keyed by feed URL so switching topics naturally clears both (derived below).
  const [lastError, setLastError] = useState<{ url: string; message: string } | null>(null);
  const [subscribed, setSubscribed] = useState<{ url: string; feed: Feed } | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(topic.trim()), 450);
    return () => clearTimeout(t);
  }, [topic]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const encoded = encodeURIComponent(debounced);
  const { data: resolved, error: resolveError } = useSWR<SmartFeedResolve>(
    debounced ? `/catalog/smart/${provider.key}/resolve?topic=${encoded}` : null,
    fetcher,
  );
  // Fetched in the browser when the publisher allows it; the key doubles as
  // the server fallback path (see fetchPreview).
  const previewKey = resolved ? `/catalog/smart/${provider.key}/preview?topic=${encoded}` : null;
  const { data: preview, error: previewError, isLoading: previewLoading } = useSWR<LoadedPreview>(
    resolved ? previewKey : null,
    () => fetchPreview(resolved!.url, previewKey!),
  );

  // A fresh topic means a different feed: success and error states only apply
  // while the input still resolves to the URL they were produced for.
  const feed = subscribed && subscribed.url === resolved?.url ? subscribed.feed : null;
  const subscribeError = lastError && lastError.url === resolved?.url ? lastError.message : null;

  async function subscribe() {
    if (!resolved || busy) return;
    setBusy(true);
    setLastError(null);
    try {
      const created = await api<Feed>("/feeds", {
        method: "POST",
        body: { url: resolved.url, ...toSubscribeOptions(settings) },
      });
      setSubscribed({ url: resolved.url, feed: created });
      mutate("/feeds");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Something went wrong";
      setLastError({ url: resolved.url, message: `Could not subscribe to ${resolved.title}: ${message}` });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6"
      style={{ background: "var(--bg-scrim)", backdropFilter: "blur(4px)" }}
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={provider.name}
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
              <p className="mono-label">{provider.category} · Smart feed</p>
              <h2 className="font-serif-nr mt-1.5 text-[20px] leading-snug">{provider.name}</h2>
              <a
                href={provider.site_url}
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-flex max-w-full items-center gap-1 font-mono-nr text-[11.5px] hover:underline"
                style={{ color: "var(--ink-faint)" }}
              >
                <span className="truncate">{provider.site_url.replace(/^https?:\/\//, "")}</span>
                <ExternalIcon size={11} />
              </a>
            </div>
            <button className="icon-btn shrink-0" aria-label="Close" onClick={onClose}>
              <XIcon size={16} />
            </button>
          </div>
          <p className="mt-3 text-[13px] leading-relaxed" style={{ color: "var(--ink-dim)" }}>
            {provider.description}
          </p>
          <label className="mt-3 block text-[12px] font-medium">
            {provider.topic_label}
            <input
              className="input mt-1"
              placeholder={provider.topic_hint}
              value={topic}
              autoFocus
              onChange={(event) => setTopic(event.target.value)}
            />
          </label>
          {provider.example_topics.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <span className="text-[11px]" style={{ color: "var(--ink-faint)" }}>Try:</span>
              {provider.example_topics.map((example) => (
                <button
                  key={example}
                  className="rounded-full border px-2 py-0.5 text-[11.5px]"
                  style={{ borderColor: "var(--line-soft)", color: "var(--ink-dim)" }}
                  onClick={() => setTopic(example)}
                >
                  {example}
                </button>
              ))}
            </div>
          )}
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
          {!debounced && (
            <p className="text-[12.5px]" style={{ color: "var(--ink-faint)" }}>
              Enter a {provider.topic_label.toLowerCase()} to preview its latest stories.
            </p>
          )}
          {resolveError && (
            <p className="text-[12.5px]" role="alert" style={{ color: "var(--danger)" }}>
              {resolveError instanceof Error ? resolveError.message : "That topic could not be resolved."}
            </p>
          )}
          {resolved && (
            <>
              <p className="mono-label">Latest stories · {resolved.title}</p>
              {previewLoading && (
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
                  No stories for this topic right now — you can still subscribe.
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
              {previewError && (
                <p className="mt-2 text-[12.5px]" role="alert" style={{ color: "var(--danger)" }}>
                  Could not load stories for this topic right now.
                </p>
              )}
            </>
          )}
        </div>

        <footer className="border-t px-6 py-4" style={{ borderColor: "var(--line-soft)" }}>
          {!feed && (
            <div className="mb-3">
              <SubscribeQuickSettings value={settings} onChange={setSettings} disabled={busy} />
            </div>
          )}
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate font-mono-nr text-[10.5px]" style={{ color: "var(--ink-faint)" }}>
                {resolved?.url ?? provider.topic_hint}
              </p>
              {subscribeError && (
                <p className="mt-1 text-[12px]" role="alert" style={{ color: "var(--danger)" }}>
                  {subscribeError}
                </p>
              )}
            </div>
            {feed ? (
              <Link href={`/?feed=${feed.id}`} className="btn shrink-0" style={{ color: "var(--accent)" }}>
                <CheckIcon size={13} /> View feed
              </Link>
            ) : (
              <button
                className="btn btn-accent shrink-0"
                disabled={!resolved || busy}
                onClick={subscribe}
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
