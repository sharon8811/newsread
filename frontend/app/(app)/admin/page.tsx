"use client";

import Link from "next/link";
import { notFound } from "next/navigation";
import { useState } from "react";
import AdminTrendChart from "@/components/AdminTrendChart";
import {
  USAGE_FEATURE_LABELS,
  type ActivityRange,
  type AdminTrendDay,
  type UsageFeatureKey,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { humanCount } from "@/lib/format";
import { useAdminOverview, useAdminTrends } from "@/lib/queries";

const RANGES: Array<{ value: ActivityRange; label: string }> = [
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
  { value: "year", label: "Year" },
];

// Chartable per-day series from AdminTrendsOut, in menu order.
const METRICS: Array<{ key: keyof AdminTrendDay; label: string; unit: string }> = [
  { key: "active_users", label: "Active users", unit: "users" },
  { key: "new_users", label: "New users", unit: "users" },
  { key: "new_subscriptions", label: "New subscriptions", unit: "subscriptions" },
  { key: "articles_ingested", label: "Articles ingested", unit: "articles" },
  { key: "articles_summarized", label: "Articles summarized", unit: "articles" },
  { key: "articles_read", label: "Articles read", unit: "articles" },
  { key: "reading_seconds", label: "Reading time", unit: "seconds" },
  { key: "llm_tokens", label: "LLM tokens", unit: "tokens" },
  { key: "llm_calls", label: "LLM calls", unit: "calls" },
  { key: "articles_failed", label: "Processing failures", unit: "events" },
  { key: "articles_skipped", label: "Summary skips", unit: "articles" },
];

function StatTile({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div
      className="rounded-lg border p-4"
      style={{ borderColor: "var(--line-soft)", background: "var(--bg-raised)" }}
    >
      <p className="mono-label">{label}</p>
      <p className="mt-1.5 text-display-lg font-semibold leading-none tracking-tight">{value}</p>
      {sub && (
        <p className="font-mono-nr mt-1.5 text-label" style={{ color: "var(--ink-faint)" }}>
          {sub}
        </p>
      )}
    </div>
  );
}

function BreakdownList({
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
              <span className="font-mono-nr shrink-0 text-label" style={{ color: "var(--ink-dim)" }}>
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

export default function AdminPage() {
  const { user } = useAuth();
  const [range, setRange] = useState<ActivityRange>("month");
  const [metric, setMetric] = useState<(typeof METRICS)[number]>(METRICS[0]);
  const { data: overview } = useAdminOverview();
  const { data: trends } = useAdminTrends(range);

  // The API is the boundary (regular users get 403s); this only keeps the
  // shell honest on a direct visit.
  if (user && user.role !== "owner" && user.role !== "admin") notFound();

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
          <h1 className="text-title font-semibold leading-none tracking-tight">Instance admin</h1>
          <Link
            href="/admin/users"
            className="btn ml-1"
            style={{ fontSize: 12.5 }}
          >
            Manage users
          </Link>
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

      {!overview ? (
        <div className="mx-auto max-w-[980px] px-5 py-8 sm:px-8">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="h-[92px] rounded-lg" style={{ background: "var(--bg-hover)" }} />
            ))}
          </div>
        </div>
      ) : (
        <div className="fade-up mx-auto max-w-[980px] px-5 py-8 sm:px-8">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <StatTile
              label="Users"
              value={String(overview.users_total)}
              sub={`+${overview.users_new_7d} this week · ${overview.users_suspended} suspended`}
            />
            <StatTile
              label="Active today"
              value={String(overview.active_today)}
              sub={`${overview.active_7d} this week · ${overview.active_30d} this month`}
            />
            <StatTile label="Subscriptions" value={String(overview.subscriptions_total)} />
            <StatTile
              label="Articles (24h)"
              value={String(overview.articles_ingested_24h)}
              sub={`${overview.articles_summarized_24h} summarized · ${overview.articles_skipped_24h} skipped · ${overview.articles_failed_24h} failed`}
            />
            <StatTile
              label="LLM tokens (7d)"
              value={humanCount(overview.llm_tokens_7d)}
              sub={`${humanCount(overview.llm_tokens_7d_system)} system · ${humanCount(overview.llm_tokens_7d_user)} user keys`}
            />
            <StatTile
              label="LLM calls (7d)"
              value={String(overview.llm_calls_7d)}
              sub={`${overview.llm_errors_7d} failed`}
            />
          </div>

          <section
            className="mt-6 rounded-lg border p-5"
            style={{ borderColor: "var(--line-soft)", background: "var(--bg-raised)" }}
          >
            <div className="flex items-center gap-3">
              <p className="mono-label">Trend</p>
              <select
                className="input ml-auto w-auto"
                style={{ fontSize: 12.5 }}
                aria-label="Trend metric"
                value={metric.key}
                onChange={(e) =>
                  setMetric(METRICS.find((m) => m.key === e.target.value) ?? METRICS[0])
                }
              >
                {METRICS.map((m) => (
                  <option key={m.key} value={m.key}>
                    {m.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="mt-4">
              {trends ? (
                <AdminTrendChart
                  points={trends.days.map((d) => ({
                    day: d.day,
                    value: Number(d[metric.key] ?? 0),
                  }))}
                  range={range}
                  unit={metric.unit}
                />
              ) : (
                <div className="h-[220px] rounded-lg" style={{ background: "var(--bg-hover)" }} />
              )}
            </div>
          </section>

          {trends && (
            <>
              <p className="font-mono-nr mt-6 text-label" style={{ color: "var(--ink-faint)" }}>
                LLM this {range}: {humanCount(trends.llm_tokens_system)} system-key tokens ·{" "}
                {humanCount(trends.llm_tokens_user)} on user keys
              </p>
              <div className="mt-3 grid gap-6 sm:grid-cols-2">
                {trends.llm_by_feature.length > 0 && (
                  <BreakdownList
                    title="LLM by feature"
                    rows={trends.llm_by_feature.map((f) => ({
                      key: `feature-${f.feature}`,
                      primary:
                        USAGE_FEATURE_LABELS[f.feature as UsageFeatureKey] ?? f.feature,
                      calls: f.calls,
                      tokens: f.tokens,
                    }))}
                  />
                )}
                {trends.llm_by_model.length > 0 && (
                  <BreakdownList
                    title="LLM by model"
                    rows={trends.llm_by_model.map((m) => ({
                      key: `model-${m.provider}-${m.model}`,
                      primary: m.model,
                      sub: m.provider,
                      calls: m.calls,
                      tokens: m.tokens,
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
