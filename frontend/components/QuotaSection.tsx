"use client";

import { useQuota } from "@/lib/queries";

// Read-only plan/allowance block (#119): tier, monthly usage, reset date.
// Deliberately no purchase, upgrade, or plan-selection controls — tiers are
// assigned by the instance's administrators in this phase.
export default function QuotaSection() {
  const { data } = useQuota();
  if (!data) return null;
  const finite = data.allowance != null;
  const pct = finite ? Math.min(100, (data.used / Math.max(1, data.allowance!)) * 100) : 0;

  return (
    <section className="mt-9">
      <p className="mono-label">Plan</p>
      <div
        className="mt-3.5 rounded-lg border p-4"
        style={{ background: "var(--bg-raised)", borderColor: "var(--line)" }}
      >
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-body font-semibold">{data.tier_name}</span>
          <span className="font-mono-nr text-label" style={{ color: "var(--ink-faint)" }}>
            {finite
              ? `${data.used} of ${data.allowance} articles this month`
              : `${data.used} articles this month · no limit`}
            {data.exempt && " · administrator, never limited"}
          </span>
        </div>
        {finite && (
          <div
            className="mt-2.5 h-[4px] overflow-hidden rounded-full"
            style={{ background: "var(--accent-soft)" }}
          >
            <div
              className="h-full rounded-full"
              style={{
                background: pct >= 100 ? "var(--danger)" : "var(--accent)",
                width: `${pct}%`,
              }}
            />
          </div>
        )}
        <p className="mt-2 text-body-sm" style={{ color: "var(--ink-faint)" }}>
          An article counts only when it is processed for you; cached and shared summaries are
          free.{finite && ` The counter resets on ${data.resets_on} (UTC).`} Tiers are managed by
          the instance administrator.
        </p>
      </div>
    </section>
  );
}
