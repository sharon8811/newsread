"use client";

import Link from "next/link";
import { notFound } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import {
  api,
  type BrowserHistoryPage,
  type BrowserHistorySort,
} from "@/lib/api";
import { timeAgo } from "@/lib/format";
import {
  mutateBrowserHistory,
  mutateBrowserHistorySettings,
  useHistory,
  useHistorySummary,
  useServerConfig,
} from "@/lib/queries";
import { useDebouncedValue } from "@/lib/useDebouncedValue";
import {
  ExternalIcon,
  EyeOffIcon,
  ListIcon,
  SearchIcon,
  TrashIcon,
} from "@/components/icons";
import ConfirmButton from "@/components/ui/ConfirmButton";
import ErrorText from "@/components/ui/ErrorText";
import Skeleton from "@/components/ui/Skeleton";

function safePageUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function isValidHostnameFilter(value: string): boolean {
  if (!value) return true;
  if (value.length > 253 || /\s/.test(value)) return false;
  if (/^\d+(?:\.\d+){3}$/.test(value)) return false;
  try {
    const parsed = new URL(`http://${value}`);
    return (
      parsed.hostname === value.toLowerCase() &&
      parsed.hostname.includes(".") &&
      !parsed.hostname.endsWith(".")
    );
  } catch {
    return false;
  }
}

function visitLabel(count: number) {
  return count === 1 ? "1 visit" : `${count} visits`;
}

function HistoryRow({ page }: { page: BrowserHistoryPage }) {
  const [busy, setBusy] = useState<"delete" | "exclude" | null>(null);
  const href = safePageUrl(page.url);

  async function deletePage() {
    setBusy("delete");
    try {
      await api<void>(`/history/${page.id}`, {
        method: "DELETE",
      });
      mutateBrowserHistory();
      toast.success("History item deleted");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not delete the item");
    } finally {
      setBusy(null);
    }
  }

  async function excludeDomain() {
    setBusy("exclude");
    try {
      await api("/history/domain-rules", {
        method: "POST",
        body: {
          hostname: page.hostname,
          match_subdomains: true,
          mode: "exclude",
          delete_existing: true,
        },
      });
      mutateBrowserHistory();
      mutateBrowserHistorySettings();
      toast.success(`${page.hostname} excluded`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not exclude the domain");
    } finally {
      setBusy(null);
    }
  }

  const title = page.title.trim() || page.url;

  return (
    <article
      className="group border-b py-5 first:pt-0 last:border-b-0 last:pb-0"
      style={{ borderColor: "var(--line-soft)" }}
    >
      <div className="flex items-start gap-4">
        <div className="min-w-0 flex-1">
          {href ? (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex max-w-full items-start gap-1.5 text-lead font-medium leading-snug hover:underline"
            >
              <span className="min-w-0 break-words">{title}</span>
              <ExternalIcon className="mt-1 shrink-0" size={13} />
            </a>
          ) : (
            <p className="break-words text-lead font-medium leading-snug">{title}</p>
          )}
          <p
            className="font-mono-nr mt-1 truncate text-caption"
            style={{ color: "var(--accent)" }}
          >
            {page.hostname}
          </p>
          {page.text_excerpt && (
            <p
              className="mt-2 line-clamp-2 text-body leading-relaxed"
              style={{ color: "var(--ink-dim)" }}
            >
              {page.text_excerpt}
            </p>
          )}
          <div
            className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-caption"
            style={{ color: "var(--ink-faint)" }}
          >
            <span>{timeAgo(page.last_visited_at)}</span>
            <span aria-hidden="true">·</span>
            <span>{visitLabel(page.visit_count)}</span>
            {page.source_browsers.length > 0 && (
              <>
                <span aria-hidden="true">·</span>
                <span>{page.source_browsers.join(", ")}</span>
              </>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1 opacity-100 sm:opacity-0 sm:transition-opacity sm:group-hover:opacity-100 sm:group-focus-within:opacity-100">
          <ConfirmButton
            className="px-2"
            variant="ghost"
            title={`Exclude ${page.hostname} and delete its history`}
            aria-label={`Exclude ${page.hostname} and delete its history`}
            onConfirm={excludeDomain}
            confirmLabel="Exclude?"
            disabled={busy !== null}
          >
            <EyeOffIcon size={14} />
          </ConfirmButton>
          <ConfirmButton
            className="px-2"
            title={`Delete ${title}`}
            aria-label={`Delete ${title}`}
            onConfirm={deletePage}
            confirmLabel="Delete?"
            disabled={busy !== null}
          >
            <TrashIcon size={14} />
          </ConfirmButton>
        </div>
      </div>
    </article>
  );
}

export default function HistoryPage() {
  const { data: config } = useServerConfig();
  const enabled = config?.browser_history_enabled === true;
  const { data: summary, error: summaryError } = useHistorySummary(enabled);
  const [query, setQuery] = useState("");
  const [hostname, setHostname] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [querySort, setQuerySort] = useState<BrowserHistorySort>("relevance");
  const [cursorStack, setCursorStack] = useState<(string | undefined)[]>([
    undefined,
  ]);
  const [pageIndex, setPageIndex] = useState(0);
  const debouncedQuery = useDebouncedValue(query.trim(), 250);
  const debouncedHostname = useDebouncedValue(hostname.trim(), 250);
  const hostnameFilterValid = isValidHostnameFilter(debouncedHostname);
  const sort = debouncedQuery ? querySort : "recent";
  const shouldLoadHistory =
    enabled && summary?.has_history === true && hostnameFilterValid;
  const {
    data: historyPage,
    error,
    isLoading,
    mutate,
  } = useHistory(
    {
      q: debouncedQuery || undefined,
      hostname: debouncedHostname || undefined,
      dateFrom: dateFrom || undefined,
      dateTo: dateTo || undefined,
      sort,
      cursor: cursorStack[pageIndex],
    },
    shouldLoadHistory,
  );
  const pages = historyPage?.items;

  function resetPagination() {
    setCursorStack([undefined]);
    setPageIndex(0);
  }

  function showNextPage() {
    if (!historyPage?.nextCursor) return;
    setCursorStack((current) => [
      ...current.slice(0, pageIndex + 1),
      historyPage.nextCursor ?? undefined,
    ]);
    setPageIndex((current) => current + 1);
  }

  if (config && !enabled) notFound();

  const filtered =
    Boolean(debouncedQuery) ||
    Boolean(debouncedHostname) ||
    Boolean(dateFrom) ||
    Boolean(dateTo);

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
        <div className="mx-auto flex max-w-[860px] items-center gap-3">
          <div>
            <h1 className="text-title font-semibold leading-none tracking-tight">
              History
            </h1>
            {summary?.has_history && (
              <p className="mt-1 text-caption" style={{ color: "var(--ink-faint)" }}>
                {summary.history_count} saved{" "}
                {summary.history_count === 1 ? "page" : "pages"}
              </p>
            )}
          </div>
          <Link href="/settings#browser-history" className="btn ml-auto">
            History settings
          </Link>
        </div>
      </header>

      <div className="mx-auto w-full max-w-[860px] px-4 py-6 sm:px-6 sm:py-8">
        {!config || (enabled && !summary && !summaryError) ? (
          <div className="space-y-3">
            <Skeleton className="h-11" />
            <Skeleton className="h-32" />
            <Skeleton className="h-32" />
          </div>
        ) : summaryError ? (
          <div className="py-20 text-center">
            <ErrorText>Could not load browser history.</ErrorText>
          </div>
        ) : summary && !summary.has_active_connection && !summary.has_history ? (
          <div className="flex flex-col items-center px-6 py-20 text-center">
            <span
              className="flex h-12 w-12 items-center justify-center rounded-full"
              style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
            >
              <ListIcon size={20} />
            </span>
            <p className="mt-4 text-lead font-medium">Pair your first browser</p>
            <p
              className="mt-1.5 max-w-[440px] text-body leading-relaxed"
              style={{ color: "var(--ink-faint)" }}
            >
              Create a one-time pairing token in Settings, then add it to the
              NewsRead Chrome extension. Synced pages will appear here.
            </p>
            <Link href="/settings#browser-history" className="btn btn-accent mt-5">
              Set up browser history
            </Link>
          </div>
        ) : (
          <>
            <section aria-label="History filters">
              <div className="relative">
                <span
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2"
                  style={{ color: "var(--ink-faint)" }}
                >
                  <SearchIcon size={15} />
                </span>
                <input
                  className="input"
                  style={{ paddingLeft: 36 }}
                  type="search"
                  aria-label="Search browser history"
                  placeholder="Search titles, domains, and page text…"
                  value={query}
                  autoFocus
                  onChange={(event) => {
                    setQuery(event.target.value);
                    resetPagination();
                  }}
                />
              </div>
              <div className="mt-2.5 grid grid-cols-2 gap-2 sm:grid-cols-[minmax(0,1fr)_auto_auto_auto]">
                <input
                  className="input col-span-2 sm:col-span-1"
                  aria-label="Filter by domain"
                  aria-describedby={
                    hostnameFilterValid ? undefined : "history-domain-help"
                  }
                  aria-invalid={!hostnameFilterValid}
                  placeholder="Domain"
                  value={hostname}
                  onChange={(event) => {
                    setHostname(event.target.value);
                    resetPagination();
                  }}
                />
                <input
                  className="input min-w-0"
                  type="date"
                  aria-label="Visited after"
                  value={dateFrom}
                  onChange={(event) => {
                    setDateFrom(event.target.value);
                    resetPagination();
                  }}
                />
                <input
                  className="input min-w-0"
                  type="date"
                  aria-label="Visited before"
                  value={dateTo}
                  onChange={(event) => {
                    setDateTo(event.target.value);
                    resetPagination();
                  }}
                />
                <select
                  className="input col-span-2 w-full sm:col-span-1 sm:w-auto"
                  aria-label="Sort history"
                  value={sort}
                  onChange={(event) => {
                    setQuerySort(event.target.value as BrowserHistorySort);
                    resetPagination();
                  }}
                >
                  <option value="relevance" disabled={!debouncedQuery}>
                    Best match
                  </option>
                  <option value="recent">Most recent</option>
                </select>
              </div>
              {!hostnameFilterValid && (
                <p
                  id="history-domain-help"
                  className="mt-2 text-body-sm"
                  style={{ color: "var(--danger)" }}
                >
                  Enter a full domain such as example.com.
                </p>
              )}
            </section>

            <section
              className="fade-up mt-6 rounded-lg border px-4 py-5 sm:px-5"
              style={{
                background: "var(--bg-raised)",
                borderColor: "var(--line-soft)",
              }}
              aria-live="polite"
            >
              {!hostnameFilterValid ? (
                <div className="py-10 text-center">
                  <p className="text-body-lg font-medium">
                    Finish entering the domain to filter history.
                  </p>
                </div>
              ) : error ? (
                <div className="py-10 text-center">
                  <ErrorText>Could not load these history results.</ErrorText>
                  <button className="btn mt-3" onClick={() => mutate()}>
                    Try again
                  </button>
                </div>
              ) : isLoading && !pages ? (
                <div className="space-y-5">
                  <Skeleton className="h-24" />
                  <Skeleton className="h-24" />
                  <Skeleton className="h-24" />
                </div>
              ) : !summary?.has_history || pages?.length === 0 ? (
                <div className="py-12 text-center">
                  <p className="text-body-lg font-medium">
                    {filtered ? "Nothing matched those filters." : "No pages synced yet."}
                  </p>
                  <p className="mt-1.5 text-body-sm" style={{ color: "var(--ink-faint)" }}>
                    {filtered
                      ? "Try a broader search or clear one of the filters."
                      : "Keep the paired extension running while you browse."}
                  </p>
                </div>
              ) : (
                <>
                  {pages?.map((page) => (
                    <HistoryRow key={page.id} page={page} />
                  ))}
                  {(pageIndex > 0 || historyPage?.nextCursor) && (
                    <nav
                      className="mt-5 flex items-center justify-between border-t pt-4"
                      style={{ borderColor: "var(--line-soft)" }}
                      aria-label="History pages"
                    >
                      <button
                        className="btn"
                        disabled={pageIndex === 0}
                        onClick={() =>
                          setPageIndex((current) => Math.max(0, current - 1))
                        }
                      >
                        Previous
                      </button>
                      <span
                        className="text-caption"
                        style={{ color: "var(--ink-faint)" }}
                      >
                        Page {pageIndex + 1}
                      </span>
                      <button
                        className="btn"
                        disabled={!historyPage?.nextCursor}
                        onClick={showNextPage}
                      >
                        Next
                      </button>
                    </nav>
                  )}
                </>
              )}
            </section>
          </>
        )}
      </div>
    </>
  );
}
