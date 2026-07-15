"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { buildChartData, type ChartPoint } from "@/components/ActivityChart";
import { type ActivityRange, type UsageDay } from "@/lib/api";
import { humanCount } from "@/lib/format";

// Reuses ActivityChart's day-bucketing (week/month days, year folds into
// weeks) by mapping tokens onto its `seconds` slot; only formatting differs.
function chartData(days: UsageDay[], range: ActivityRange): ChartPoint[] {
  return buildChartData(
    days.map((d) => ({ day: d.day, seconds: d.tokens })),
    range,
  );
}

// Clean 1/2/5-style token steps, at most four intervals, top tick just above
// the peak; steps never drop below 1 so tiny charts keep integer ticks.
export function niceCountTicks(max: number): number[] {
  const rawStep = Math.max(1, max) / 4;
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const step = Math.max(
    1,
    [1, 2, 5, 10].map((m) => m * magnitude).find((s) => s >= rawStep) ?? magnitude * 10,
  );
  const top = step * Math.max(1, Math.ceil(max / step));
  const ticks = [];
  for (let t = 0; t <= top; t += step) ticks.push(t);
  return ticks;
}

function UsageTip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload: ChartPoint }>;
}) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div
      className="rounded-md border px-3 py-2 text-body-sm"
      style={{
        background: "var(--bg-raised)",
        borderColor: "var(--line)",
        boxShadow: "0 8px 24px rgba(0, 0, 0, 0.12)",
      }}
    >
      <p style={{ color: "var(--ink-dim)" }}>{point.label}</p>
      <p className="font-mono-nr mt-0.5 font-semibold" style={{ color: "var(--ink)" }}>
        {humanCount(point.seconds)} tokens
      </p>
    </div>
  );
}

const tickStyle = { fontSize: 10.5, fill: "var(--ink-faint)", fontFamily: "var(--mono)" };

export default function UsageChart({
  days,
  range,
}: {
  days: UsageDay[];
  range: ActivityRange;
}) {
  const data = chartData(days, range);
  const ticks = niceCountTicks(Math.max(...data.map((p) => p.seconds), 0));
  return (
    <div className="h-[220px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 0, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--line-soft)" />
          <XAxis
            dataKey="tick"
            interval={0}
            tickLine={false}
            axisLine={{ stroke: "var(--line)" }}
            tick={tickStyle}
          />
          <YAxis
            tickFormatter={(t: number) => humanCount(t)}
            tickLine={false}
            axisLine={false}
            width={46}
            ticks={ticks}
            domain={[0, ticks[ticks.length - 1]]}
            tick={tickStyle}
          />
          <Tooltip content={<UsageTip />} cursor={{ fill: "var(--bg-hover)" }} />
          <Bar
            dataKey="seconds"
            fill="var(--accent)"
            radius={[4, 4, 0, 0]}
            maxBarSize={24}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
