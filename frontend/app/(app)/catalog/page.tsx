"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import useSWR, { mutate } from "swr";
import {
  api,
  fetcher,
  type CatalogCategory,
  type CatalogEntry,
  type Feed,
} from "@/lib/api";
import { CheckIcon, PlusIcon, SearchIcon } from "@/components/icons";

type CatalogSort = "name" | "popular" | "recommended";

function catalogKey(q: string, category: string | null, sort: CatalogSort): string {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (category) params.set("category", category);
  if (sort !== "name") params.set("sort", sort);
  const qs = params.toString();
  return qs ? `/catalog?${qs}` : "/catalog";
}

function freshness(value: string | null): string | null {
  if (!value) return null;
  const days = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 86_400_000));
  if (days === 0) return "Updated today";
  if (days === 1) return "Updated yesterday";
  if (days < 30) return `Updated ${days} days ago`;
  if (days < 365) {
    const months = Math.floor(days / 30);
    return `Updated ${months} ${months === 1 ? "month" : "months"} ago`;
  }
  const years = Math.floor(days / 365);
  return `Updated ${years} ${years === 1 ? "year" : "years"} ago`;
}

function formatType(value: string | null): string {
  if (!value) return "RSS";
  if (value.includes("atom")) return "Atom";
  if (value.includes("json")) return "JSON Feed";
  return "RSS";
}

export default function CatalogPage() {
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [sort, setSort] = useState<CatalogSort>("name");
  const [busyUrl, setBusyUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAllTopics, setShowAllTopics] = useState(false);
  const [showSubmit, setShowSubmit] = useState(false);
  const [visibleCount, setVisibleCount] = useState(60);

  useEffect(() => {
    const t = setTimeout(() => {
      setQ(search.trim());
      setVisibleCount(60);
    }, 450);
    return () => clearTimeout(t);
  }, [search]);

  const key = catalogKey(q, category, sort);
  const { data: entries, error: loadError, isLoading } = useSWR<CatalogEntry[]>(key, fetcher);
  const { data: categories } = useSWR<CatalogCategory[]>("/catalog/categories", fetcher);
  const visibleCategories = showAllTopics ? categories : categories?.slice(0, 12);

  async function subscribe(entry: CatalogEntry) {
    if (busyUrl) return;
    setBusyUrl(entry.url);
    setError(null);
    try {
      const feed = await api<Feed>("/feeds", { method: "POST", body: { url: entry.url } });
      mutate(
        key,
        (current: CatalogEntry[] | undefined) => current?.map((item) =>
          item.url === entry.url ? { ...item, subscribed: true, feed_id: feed.id } : item,
        ),
        { revalidate: false },
      );
      mutate("/feeds");
    } catch (err) {
      setError(err instanceof Error ? `Could not subscribe to ${entry.title}: ${err.message}` : `Could not subscribe to ${entry.title}`);
    } finally {
      setBusyUrl(null);
    }
  }

  return (
    <>
      <header
        className="sticky top-0 z-20 border-b px-4 pb-3 pt-4 sm:px-6 sm:pt-5"
        style={{ background: "var(--bg-header)", backdropFilter: "blur(10px)", borderColor: "var(--line-soft)" }}
      >
        <div className="flex flex-wrap items-center gap-3">
          <div>
            <h1 className="text-[20px] font-semibold leading-none tracking-tight">Catalog</h1>
            <p className="mt-1 text-[11.5px]" style={{ color: "var(--ink-faint)" }}>
              Healthy, hand-curated feeds with semantic discovery
            </p>
          </div>
          <div className="relative ml-auto min-w-[220px] flex-1 sm:max-w-[360px]">
            <SearchIcon size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              aria-label="Search feeds"
              className="input"
              style={{ paddingLeft: 32, fontSize: 13, paddingTop: 7, paddingBottom: 7 }}
              placeholder="Search feeds…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <button className="btn" onClick={() => setShowSubmit((value) => !value)}>
            <PlusIcon size={13} /> Suggest feed
          </button>
        </div>
        <div className="mt-3 flex items-start gap-2">
          <div className="flex flex-1 flex-wrap gap-1.5">
            <CategoryChip label="All" active={category === null} onClick={() => { setCategory(null); setVisibleCount(60); }} />
            {visibleCategories?.map((item) => (
              <CategoryChip
                key={item.name}
                label={item.name}
                count={item.count}
                active={category === item.name}
                onClick={() => { setCategory(category === item.name ? null : item.name); setVisibleCount(60); }}
              />
            ))}
            {(categories?.length ?? 0) > 12 && (
              <button className="rounded-full border px-2.5 py-1 text-[12px]" style={{ borderColor: "var(--line-soft)", color: "var(--ink-dim)" }} onClick={() => setShowAllTopics((value) => !value)}>
                {showAllTopics ? "Fewer topics" : `More topics (${(categories?.length ?? 12) - 12})`}
              </button>
            )}
          </div>
        </div>
      </header>

      <main className="px-4 py-4 sm:px-6">
        {showSubmit && <SubmissionForm categories={categories ?? []} onDone={() => setShowSubmit(false)} />}
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <p className="mr-auto text-[12.5px]" style={{ color: "var(--ink-faint)" }}>
            {entries ? `${entries.length} feeds` : "Loading feeds"}{q ? ` matching “${q}”` : ""}
          </p>
          <SortButton label="For you" active={sort === "recommended"} onClick={() => { setSort("recommended"); setVisibleCount(60); }} />
          <SortButton label="Popular" active={sort === "popular"} onClick={() => { setSort("popular"); setVisibleCount(60); }} />
          <SortButton label="A-Z" active={sort === "name"} onClick={() => { setSort("name"); setVisibleCount(60); }} />
        </div>
        {error && <p className="mb-3 text-[13px]" role="alert" style={{ color: "var(--danger)" }}>{error}</p>}
        {loadError && <p className="rounded-lg border p-4 text-[13px]" role="alert" style={{ borderColor: "var(--line-soft)", color: "var(--danger)" }}>Could not load the catalog. Please try again.</p>}
        {isLoading && <CatalogSkeleton />}
        {entries?.length === 0 && (
          <div className="py-14 text-center">
            <p className="text-[14px] font-medium">No feeds match your filters</p>
            <p className="mt-1 text-[12.5px]" style={{ color: "var(--ink-faint)" }}>Try a broader phrase or clear the topic filter.</p>
          </div>
        )}
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {entries?.slice(0, visibleCount).map((entry) => <FeedCard key={entry.id} entry={entry} busy={busyUrl === entry.url} disabled={busyUrl !== null} onSubscribe={() => subscribe(entry)} />)}
        </div>
        {entries && entries.length > visibleCount && (
          <div className="pt-5 text-center">
            <button className="btn" onClick={() => setVisibleCount((count) => count + 60)}>
              Load more feeds
            </button>
          </div>
        )}
      </main>
    </>
  );
}

function FeedCard({ entry, busy, disabled, onSubscribe }: { entry: CatalogEntry; busy: boolean; disabled: boolean; onSubscribe: () => void }) {
  const updated = freshness(entry.latest_item_at);
  return (
    <article className="flex min-h-[230px] flex-col rounded-lg border p-4" style={{ borderColor: "var(--line-soft)", background: "var(--bg-inset)" }}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="truncate text-[14px] font-semibold leading-snug">{entry.title}</h2>
          <p className="mt-0.5 truncate font-mono-nr text-[10.5px]" style={{ color: "var(--ink-faint)" }}>{entry.source_host} · {formatType(entry.content_type)}</p>
        </div>
        <span className="mono-label shrink-0 rounded-full border px-2 py-0.5 text-[10px]" style={{ borderColor: "var(--line-soft)", color: "var(--ink-faint)" }}>{entry.category}</span>
      </div>
      <p className="mt-2 line-clamp-3 text-[12.5px] leading-relaxed" style={{ color: "var(--ink-dim)" }}>{entry.description}</p>
      {entry.preview_items.length > 0 && (
        <details className="mt-2 text-[11.5px]" style={{ color: "var(--ink-dim)" }}>
          <summary className="cursor-pointer select-none">Preview latest stories</summary>
          <ul className="mt-1.5 space-y-1.5 border-l pl-2.5" style={{ borderColor: "var(--line)" }}>
            {entry.preview_items.slice(0, 3).map((item) => <li key={`${item.url}-${item.title}`} className="line-clamp-1">{item.title}</li>)}
          </ul>
        </details>
      )}
      <div className="mt-auto flex flex-wrap items-center gap-x-2 pt-3 text-[10.5px]" style={{ color: "var(--ink-faint)" }}>
        {updated && <span>{updated}</span>}
        {entry.item_count !== null && <span>{entry.item_count} recent {entry.item_count === 1 ? "item" : "items"}</span>}
        {entry.subscriber_count > 0 && <span>{entry.subscriber_count} {entry.subscriber_count === 1 ? "reader" : "readers"}</span>}
        {entry.match_reason && <span style={{ color: "var(--accent)" }}>{entry.match_reason}</span>}
      </div>
      <div className="pt-2.5">
        {entry.subscribed ? (
          <Link href={`/?feed=${entry.feed_id}`} className="btn w-full" style={{ color: "var(--accent)" }}><CheckIcon size={13} /> Subscribed, view feed</Link>
        ) : (
          <button className="btn btn-accent w-full" disabled={disabled} onClick={onSubscribe}>{busy ? "Subscribing…" : <><PlusIcon size={13} /> Subscribe</>}</button>
        )}
      </div>
    </article>
  );
}

function SubmissionForm({ categories, onDone }: { categories: CatalogCategory[]; onDone: () => void }) {
  const [url, setUrl] = useState("");
  const [category, setCategory] = useState("");
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setState("busy"); setError(null);
    try {
      await api("/catalog/submissions", { method: "POST", body: { url, category: category || null } });
      setState("done");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not submit this feed"); setState("idle");
    }
  }
  if (state === "done") return <div className="mb-4 rounded-lg border p-4 text-[13px]" style={{ borderColor: "var(--accent-border)", background: "var(--accent-soft)" }}>Thanks. The feed passed validation and is queued for review. <button className="underline" onClick={onDone}>Close</button></div>;
  return (
    <form onSubmit={submit} className="mb-4 grid gap-3 rounded-lg border p-4 sm:grid-cols-[1fr_180px_auto]" style={{ borderColor: "var(--line-soft)", background: "var(--bg-raised)" }}>
      <label className="text-[12px] font-medium">Feed URL<input className="input mt-1" type="url" required value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com/feed.xml" /></label>
      <label className="text-[12px] font-medium">Topic<select className="input mt-1" value={category} onChange={(event) => setCategory(event.target.value)}><option value="">Choose later</option>{categories.map((item) => <option key={item.name}>{item.name}</option>)}</select></label>
      <button className="btn btn-accent self-end" disabled={state === "busy"}>{state === "busy" ? "Validating…" : "Submit"}</button>
      {error && <p className="text-[12px] sm:col-span-3" role="alert" style={{ color: "var(--danger)" }}>{error}</p>}
    </form>
  );
}

function SortButton({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) { return <button className="rounded-md px-2 py-1 text-[11.5px]" style={{ background: active ? "var(--accent-soft)" : "transparent", color: active ? "var(--accent)" : "var(--ink-faint)" }} onClick={onClick}>{label}</button>; }

function CategoryChip({ label, count, active, onClick }: { label: string; count?: number; active: boolean; onClick: () => void }) { return <button className="rounded-full border px-2.5 py-1 text-[12px] transition-colors" style={{ borderColor: active ? "var(--accent)" : "var(--line-soft)", background: active ? "var(--accent-soft)" : "transparent", color: active ? "var(--accent)" : "var(--ink-dim)" }} onClick={onClick}>{label}{count !== undefined && <span className="ml-1 font-mono-nr text-[10.5px]" style={{ color: "var(--ink-faint)" }}>{count}</span>}</button>; }

function CatalogSkeleton() { return <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-label="Loading catalog">{Array.from({ length: 6 }).map((_, index) => <div key={index} className="h-[230px] animate-pulse rounded-lg border" style={{ borderColor: "var(--line-soft)", background: "var(--bg-inset)" }} />)}</div>; }
