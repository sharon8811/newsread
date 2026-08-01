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
import { niceCountTicks } from "@/components/UsageChart";
import { type ActivityRange } from "@/lib/api";
import { humanCount } from "@/lib/format";

// One generic count-per-day chart for the admin trends: whichever metric is
// selected rides ActivityChart's day bucketing (week/month days, year folds
// into weeks) through the `seconds` slot, exactly like UsageChart.
function AdminTip({
  active,
  payload,
  unit,
}: {
  active?: boolean;
  payload?: Array<{ payload: ChartPoint }>;
  unit: string;
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
        {humanCount(point.seconds)} {unit}
      </p>
    </div>
  );
}

const tickStyle = { fontSize: 10.5, fill: "var(--ink-faint)", fontFamily: "var(--mono)" };

export default function AdminTrendChart({
  points,
  range,
  unit,
}: {
  points: Array<{ day: string; value: number }>;
  range: ActivityRange;
  unit: string;
}) {
  const data = buildChartData(
    points.map((p) => ({ day: p.day, seconds: p.value })),
    range,
  );
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
          <Tooltip content={<AdminTip unit={unit} />} cursor={{ fill: "var(--bg-hover)" }} />
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
