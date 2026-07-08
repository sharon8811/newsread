"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import ActivityChart from "@/components/ActivityChart";
import { fetcher, type ActivityRange, type ActivitySummary } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import { localDay } from "@/lib/useReadingTimer";

const RANGES: Array<{ value: ActivityRange; label: string }> = [
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
  { value: "year", label: "Year" },
];

const RANGE_META: Record<
  ActivityRange,
  { total: string; vs: string; days: number; unit: string }
> = {
  week: { total: "This week", vs: "vs last week", days: 7, unit: "day" },
  month: { total: "Past 30 days", vs: "vs previous 30 days", days: 30, unit: "day" },
  year: { total: "Past year", vs: "vs previous year", days: 365, unit: "week" },
};

function Delta({ current, previous, vs }: { current: number; previous: number; vs: string }) {
  if (previous <= 0) return null;
  const pct = Math.round(((current - previous) / previous) * 100);
  const up = pct >= 0;
  return (
    <p className="font-mono-nr mt-1.5 text-[11px]" style={{ color: "var(--ink-faint)" }}>
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
      <p className="mt-1.5 text-[26px] font-semibold leading-none tracking-tight">{value}</p>
      {children}
    </div>
  );
}

function TimeList({
  title,
  rows,
}: {
  title: string;
  rows: Array<{ key: string; primary: React.ReactNode; sub?: string; seconds: number }>;
}) {
  const max = Math.max(...rows.map((r) => r.seconds), 1);
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
              <div className="min-w-0 flex-1 truncate text-[13.5px]">{row.primary}</div>
              <span
                className="font-mono-nr shrink-0 text-[11.5px]"
                style={{ color: "var(--ink-dim)" }}
              >
                {formatDuration(row.seconds)}
              </span>
            </div>
            {row.sub && (
              <p className="font-mono-nr truncate text-[10.5px]" style={{ color: "var(--ink-faint)" }}>
                {row.sub}
              </p>
            )}
            <div
              className="mt-1.5 h-[3px] overflow-hidden rounded-full"
              style={{ background: "var(--accent-soft)" }}
            >
              <div
                className="h-full rounded-full"
                style={{ background: "var(--accent)", width: `${(row.seconds / max) * 100}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function ActivityPage() {
  const [range, setRange] = useState<ActivityRange>("week");
  const { data } = useSWR<ActivitySummary>(
    `/activity/summary?range=${range}&today=${localDay()}`,
    fetcher,
  );
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
          <h1 className="text-[20px] font-semibold leading-none tracking-tight">Activity</h1>
          <div
            className="ml-auto flex rounded-md border p-0.5"
            style={{ borderColor: "var(--line)", background: "var(--bg-raised)" }}
          >
            {RANGES.map((r) => (
              <button
                key={r.value}
                className="rounded px-3 py-1 text-[12.5px] font-medium transition-colors"
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
      ) : (
        <div className="fade-up mx-auto max-w-[860px] px-5 py-8 sm:px-8">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <StatTile label={meta.total} value={formatDuration(data.total_seconds)}>
              <Delta
                current={data.total_seconds}
                previous={data.prev_total_seconds}
                vs={meta.vs}
              />
            </StatTile>
            <StatTile
              label="Daily average"
              value={formatDuration(data.total_seconds / meta.days)}
            />
            <StatTile
              label="Reading streak"
              value={`${data.streak_days} ${data.streak_days === 1 ? "day" : "days"}`}
            />
          </div>

          {data.total_seconds === 0 && data.prev_total_seconds === 0 ? (
            <div className="flex flex-col items-center px-8 py-20 text-center">
              <p className="text-[17px] font-medium" style={{ color: "var(--ink-dim)" }}>
                Nothing on the clock yet.
              </p>
              <p className="mt-1.5 text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
                Open any article and your reading time will show up here.
              </p>
            </div>
          ) : (
            <>
              <section
                className="mt-6 rounded-lg border p-5"
                style={{ borderColor: "var(--line-soft)", background: "var(--bg-raised)" }}
              >
                <p className="mono-label">
                  Reading time per {meta.unit}
                </p>
                <div className="mt-4">
                  <ActivityChart days={data.days} range={range} />
                </div>
              </section>

              <div className="mt-6 grid gap-6 sm:grid-cols-2">
                {data.top_feeds.length > 0 && (
                  <TimeList
                    title="Top sources"
                    rows={data.top_feeds.map((f) => ({
                      key: `feed-${f.feed_id}`,
                      primary: f.title,
                      seconds: f.seconds,
                    }))}
                  />
                )}
                {data.top_articles.length > 0 && (
                  <TimeList
                    title="Most read articles"
                    rows={data.top_articles.map((a) => ({
                      key: `article-${a.article_id}`,
                      primary: (
                        <Link href={`/article/${a.article_id}`} className="hover:underline">
                          {a.title}
                        </Link>
                      ),
                      sub: a.feed_title,
                      seconds: a.seconds,
                    }))}
                  />
                )}
              </div>
            </>
          )}
        </div>
      )}
    </>
  );
}
