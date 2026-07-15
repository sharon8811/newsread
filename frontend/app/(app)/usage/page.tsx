"use client";

import Link from "next/link";
import { useState } from "react";
import UsageChart from "@/components/UsageChart";
import {
  api,
  USAGE_FEATURE_LABELS,
  type ActivityRange,
  type UsageEvent,
  type UsageFeatureKey,
} from "@/lib/api";
import { humanCount, timeAgo } from "@/lib/format";
import { useUsageEvents, useUsageSummary } from "@/lib/queries";

const RANGES: Array<{ value: ActivityRange; label: string }> = [
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
  { value: "year", label: "Year" },
];

const RANGE_META: Record<ActivityRange, { total: string; vs: string; unit: string }> = {
  week: { total: "This week", vs: "vs last week", unit: "day" },
  month: { total: "Past 30 days", vs: "vs previous 30 days", unit: "day" },
  year: { total: "Past year", vs: "vs previous year", unit: "week" },
};

const EVENTS_PAGE = 20;

function featureLabel(feature: string): string {
  return USAGE_FEATURE_LABELS[feature as UsageFeatureKey] ?? feature;
}

function Delta({ current, previous, vs }: { current: number; previous: number; vs: string }) {
  if (previous <= 0) return null;
  const pct = Math.round(((current - previous) / previous) * 100);
  const up = pct >= 0;
  return (
    <p className="font-mono-nr mt-1.5 text-label" style={{ color: "var(--ink-faint)" }}>
      <span style={{ color: up ? "var(--accent)" : "var(--ink-dim)" }}>
        {up ? "▲" : "▼"} {Math.abs(pct)}%
      </span>{" "}
      {vs}
    </p>
  );
}

function StatTile({
  label,
  value,
  children,
}: {
  label: string;
  value: string;
  children?: React.ReactNode;
}) {
  return (
    <div
      className="rounded-lg border p-4"
      style={{ borderColor: "var(--line-soft)", background: "var(--bg-raised)" }}
    >
      <p className="mono-label">{label}</p>
      <p className="mt-1.5 text-display-lg font-semibold leading-none tracking-tight">{value}</p>
      {children}
    </div>
  );
}

function TokenList({
  title,
  rows,
}: {
  title: string;
  rows: Array<{ key: string; primary: string; sub?: string; calls: number; tokens: number }>;
}) {
  const max = Math.max(...rows.map((r) => r.tokens), 1);
  return (
    <section
      className="rounded-lg border p-5"
      style={{ borderColor: "var(--line-soft)", background: "var(--bg-raised)" }}
    >
      <p className="mono-label">{title}</p>
      <ul className="mt-3 flex flex-col gap-3">
        {rows.map((row) => (
          <li key={row.key}>
            <div className="flex items-baseline gap-3">
              <div className="min-w-0 flex-1 truncate text-body">{row.primary}</div>
              <span
                className="font-mono-nr shrink-0 text-label"
                style={{ color: "var(--ink-dim)" }}
              >
                {humanCount(row.tokens)} tok · {row.calls} {row.calls === 1 ? "call" : "calls"}
              </span>
            </div>
            {row.sub && (
              <p className="font-mono-nr truncate text-caption" style={{ color: "var(--ink-faint)" }}>
                {row.sub}
              </p>
            )}
            <div
              className="mt-1.5 h-[3px] overflow-hidden rounded-full"
              style={{ background: "var(--accent-soft)" }}
            >
              <div
                className="h-full rounded-full"
                style={{ background: "var(--accent)", width: `${(row.tokens / max) * 100}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function EventLog() {
  const { data: firstPage } = useUsageEvents(EVENTS_PAGE);
  const [older, setOlder] = useState<UsageEvent[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);
  const [exhausted, setExhausted] = useState(false);

  if (!firstPage || firstPage.length === 0) return null;
  const events = [...firstPage, ...older];

  async function loadMore() {
    setLoadingMore(true);
    try {
      const page = await api<UsageEvent[]>(
        `/usage/events?limit=${EVENTS_PAGE}&before_id=${events[events.length - 1].id}`,
      );
      setOlder((prev) => [...prev, ...page]);
      if (page.length < EVENTS_PAGE) setExhausted(true);
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <section
      className="mt-6 rounded-lg border p-5"
      style={{ borderColor: "var(--line-soft)", background: "var(--bg-raised)" }}
    >
      <p className="mono-label">Recent calls</p>
      <ul className="mt-3 flex flex-col divide-y divide-[color:var(--line-soft)]">
        {events.map((event) => (
          <li key={event.id} className="flex items-baseline gap-3 py-2 text-body">
            <span className="w-[110px] shrink-0 truncate">{featureLabel(event.feature)}</span>
            <span
              className="font-mono-nr min-w-0 flex-1 truncate text-label"
              style={{ color: "var(--ink-faint)" }}
            >
              {event.model}
            </span>
            {event.status === "error" ? (
              <span
                className="font-mono-nr shrink-0 text-label"
                style={{ color: "var(--danger)" }}
                title={event.error ?? undefined}
              >
                failed
              </span>
            ) : (
              <span
                className="font-mono-nr shrink-0 text-label"
                style={{ color: "var(--ink-dim)" }}
              >
                {humanCount(event.prompt_tokens + event.completion_tokens)} tok
              </span>
            )}
            <span
              className="font-mono-nr w-[64px] shrink-0 text-right text-label"
              style={{ color: "var(--ink-faint)" }}
            >
              {timeAgo(event.created_at)}
            </span>
          </li>
        ))}
      </ul>
      {firstPage.length === EVENTS_PAGE && !exhausted && (
        <button className="btn mt-3" disabled={loadingMore} onClick={loadMore}>
          {loadingMore ? "Loading…" : "Load more"}
        </button>
      )}
    </section>
  );
}

export default function UsagePage() {
  const [range, setRange] = useState<ActivityRange>("week");
  const { data } = useUsageSummary(range);
  const meta = RANGE_META[range];

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
          <h1 className="text-title font-semibold leading-none tracking-tight">AI usage</h1>
          <div
            className="ml-auto flex rounded-md border p-0.5"
            style={{ borderColor: "var(--line)", background: "var(--bg-raised)" }}
          >
            {RANGES.map((r) => (
              <button
                key={r.value}
                className="rounded px-3 py-1 text-body-sm font-medium transition-colors"
                style={{
                  background: range === r.value ? "var(--bg-hover)" : "transparent",
                  color: range === r.value ? "var(--ink)" : "var(--ink-faint)",
                }}
                onClick={() => setRange(r.value)}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>
      </header>

      {!data ? (
        <div className="mx-auto max-w-[860px] px-5 py-8 sm:px-8">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-[92px] rounded-lg" style={{ background: "var(--bg-hover)" }} />
            ))}
          </div>
          <div className="mt-6 h-[280px] rounded-lg" style={{ background: "var(--bg-hover)" }} />
        </div>
      ) : !data.configured && data.total_calls === 0 && data.prev_total_tokens === 0 ? (
        <div className="flex flex-col items-center px-8 py-20 text-center">
          <p className="text-lead font-medium" style={{ color: "var(--ink-dim)" }}>
            No AI usage on your own key yet.
          </p>
          <p className="mt-1.5 text-body" style={{ color: "var(--ink-faint)" }}>
            Add your API key in{" "}
            <Link href="/settings" className="hover:underline" style={{ color: "var(--accent)" }}>
              Settings
            </Link>{" "}
            and every summary, Q&amp;A and share message billed to it shows up here.
          </p>
        </div>
      ) : (
        <div className="fade-up mx-auto max-w-[860px] px-5 py-8 sm:px-8">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <StatTile label={meta.total} value={`${humanCount(data.total_tokens)} tokens`}>
              <Delta
                current={data.total_tokens}
                previous={data.prev_total_tokens}
                vs={meta.vs}
              />
            </StatTile>
            <StatTile
              label="Calls"
              value={String(data.total_calls)}
            />
            <StatTile label="Failed calls" value={String(data.error_count)} />
          </div>

          {data.total_calls === 0 ? (
            <div className="flex flex-col items-center px-8 py-20 text-center">
              <p className="text-lead font-medium" style={{ color: "var(--ink-dim)" }}>
                Nothing in this window.
              </p>
              <p className="mt-1.5 text-body" style={{ color: "var(--ink-faint)" }}>
                Calls on your key will show up here as you use summaries, Q&amp;A and sharing.
              </p>
            </div>
          ) : (
            <>
              <section
                className="mt-6 rounded-lg border p-5"
                style={{ borderColor: "var(--line-soft)", background: "var(--bg-raised)" }}
              >
                <p className="mono-label">Tokens per {meta.unit}</p>
                <div className="mt-4">
                  <UsageChart days={data.days} range={range} />
                </div>
              </section>

              <div className="mt-6 grid gap-6 sm:grid-cols-2">
                {data.by_feature.length > 0 && (
                  <TokenList
                    title="By feature"
                    rows={data.by_feature.map((f) => ({
                      key: `feature-${f.feature}`,
                      primary: featureLabel(f.feature),
                      calls: f.calls,
                      tokens: f.tokens,
                    }))}
                  />
                )}
                {data.by_model.length > 0 && (
                  <TokenList
                    title="By model"
                    rows={data.by_model.map((m) => ({
                      key: `model-${m.provider}-${m.model}`,
                      primary: m.model,
                      sub: m.provider,
                      calls: m.calls,
                      tokens: m.tokens,
                    }))}
                  />
                )}
              </div>

              <EventLog />
            </>
          )}
        </div>
      )}
    </>
  );
}
