"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import useSWR, { mutate } from "swr";
import {
  api,
  fetcher,
  type CatalogCategory,
  type CatalogEntry,
  type Feed,
} from "@/lib/api";
import { CheckIcon, PlusIcon, SearchIcon } from "@/components/icons";

function catalogKey(q: string, category: string | null): string {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (category) params.set("category", category);
  const qs = params.toString();
  return qs ? `/catalog?${qs}` : "/catalog";
}

export default function CatalogPage() {
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [busyUrl, setBusyUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 250);
    return () => clearTimeout(t);
  }, [search]);

  const key = catalogKey(q, category);
  const { data: entries } = useSWR<CatalogEntry[]>(key, fetcher);
  const { data: categories } = useSWR<CatalogCategory[]>(
    "/catalog/categories",
    fetcher,
  );

  async function subscribe(entry: CatalogEntry) {
    if (busyUrl) return;
    setBusyUrl(entry.url);
    setError(null);
    try {
      const feed = await api<Feed>("/feeds", {
        method: "POST",
        body: { url: entry.url },
      });
      // Flip this entry in place; the sidebar list refetches for real.
      mutate(
        key,
        (current: CatalogEntry[] | undefined) =>
          current?.map((e) =>
            e.url === entry.url
              ? { ...e, subscribed: true, feed_id: feed.id }
              : e,
          ),
        { revalidate: false },
      );
      mutate("/feeds");
    } catch (err) {
      setError(
        err instanceof Error
          ? `Could not subscribe to ${entry.title}: ${err.message}`
          : `Could not subscribe to ${entry.title}`,
      );
    } finally {
      setBusyUrl(null);
    }
  }

  return (
    <>
      <header
        className="sticky top-0 z-20 border-b px-4 pb-4 pt-4 sm:px-6 sm:pt-5"
        style={{
          background: "var(--bg-header)",
          backdropFilter: "blur(10px)",
          borderColor: "var(--line-soft)",
        }}
      >
        <div className="flex items-center gap-3">
          <h1 className="text-[20px] font-semibold leading-none tracking-tight">
            Catalog
          </h1>
          <div className="relative ml-auto w-full max-w-[280px]">
            <SearchIcon
              size={13}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2"
            />
            <input
              className="input"
              style={{ paddingLeft: 32, fontSize: 13, paddingTop: 6, paddingBottom: 6 }}
              placeholder="Search feeds…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>
        <div className="mt-3.5 flex flex-wrap gap-1.5">
          <CategoryChip
            label="All"
            active={category === null}
            onClick={() => setCategory(null)}
          />
          {categories?.map((c) => (
            <CategoryChip
              key={c.name}
              label={c.name}
              count={c.count}
              active={category === c.name}
              onClick={() => setCategory(category === c.name ? null : c.name)}
            />
          ))}
        </div>
      </header>

      <div className="px-4 py-4 sm:px-6">
        <p className="mb-3 text-[12.5px]" style={{ color: "var(--ink-faint)" }}>
          A curated directory of known feeds. Can’t find one? Add any RSS URL
          with the + in the sidebar.
        </p>
        {error && (
          <p className="mb-3 text-[13px]" style={{ color: "var(--danger)" }}>
            {error}
          </p>
        )}
        {entries?.length === 0 && (
          <p className="pt-6 text-center text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
            No feeds match{q ? ` “${q}”` : ""}.
          </p>
        )}
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {entries?.map((entry) => (
            <div
              key={entry.id}
              className="flex flex-col rounded-lg border p-4"
              style={{ borderColor: "var(--line-soft)", background: "var(--bg-inset)" }}
            >
              <div className="flex items-start justify-between gap-2">
                <h2 className="text-[14px] font-semibold leading-snug">
                  {entry.title}
                </h2>
                <span
                  className="mono-label shrink-0 rounded-full border px-2 py-0.5 text-[10px]"
                  style={{ borderColor: "var(--line-soft)", color: "var(--ink-faint)" }}
                >
                  {entry.category}
                </span>
              </div>
              {entry.description && (
                <p
                  className="mt-1.5 line-clamp-3 text-[12.5px] leading-relaxed"
                  style={{ color: "var(--ink-dim)" }}
                >
                  {entry.description}
                </p>
              )}
              <div className="mt-auto pt-3">
                {entry.subscribed ? (
                  <Link
                    href={`/?feed=${entry.feed_id}`}
                    className="btn w-full"
                    style={{ color: "var(--accent)" }}
                  >
                    <CheckIcon size={13} /> Subscribed — view
                  </Link>
                ) : (
                  <button
                    className="btn btn-accent w-full"
                    disabled={busyUrl !== null}
                    onClick={() => subscribe(entry)}
                  >
                    {busyUrl === entry.url ? (
                      "Subscribing…"
                    ) : (
                      <>
                        <PlusIcon size={13} /> Subscribe
                      </>
                    )}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

function CategoryChip({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count?: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className="rounded-full border px-2.5 py-1 text-[12px] transition-colors"
      style={{
        borderColor: active ? "var(--accent)" : "var(--line-soft)",
        background: active ? "var(--accent-soft)" : "transparent",
        color: active ? "var(--accent)" : "var(--ink-dim)",
      }}
      onClick={onClick}
    >
      {label}
      {count !== undefined && (
        <span className="font-mono-nr ml-1 text-[10.5px]" style={{ color: "var(--ink-faint)" }}>
          {count}
        </span>
      )}
    </button>
  );
}
