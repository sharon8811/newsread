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
import { type ActivityDay, type ActivityRange } from "@/lib/api";
import { formatDuration } from "@/lib/format";

export type ChartPoint = {
  key: string;
  tick: string; // sparse axis label; "" hides it
  label: string; // full label for the tooltip
  seconds: number;
};

// "YYYY-MM-DD" parsed as a LOCAL date — new Date(string) would read it as UTC
// midnight and shift the day in negative-offset timezones.
function parseDay(day: string): Date {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d);
}

const fmt = (date: Date, opts: Intl.DateTimeFormatOptions) =>
  date.toLocaleDateString("en-US", opts);

/** Shapes the dense daily series for the chart: week/month plot days (month
 * with sparse ticks); year folds days into week buckets so bars stay readable. */
export function buildChartData(days: ActivityDay[], range: ActivityRange): ChartPoint[] {
  if (range === "year") {
    const weeks: ChartPoint[] = [];
    for (let i = 0; i < days.length; i += 7) {
      const chunk = days.slice(i, i + 7);
      const start = parseDay(chunk[0].day);
      const prev = i > 0 ? parseDay(days[i - 7].day) : null;
      weeks.push({
        key: chunk[0].day,
        tick: prev === null || prev.getMonth() !== start.getMonth() ? fmt(start, { month: "short" }) : "",
        label: `Week of ${fmt(start, { month: "short", day: "numeric" })}`,
        seconds: chunk.reduce((sum, d) => sum + d.seconds, 0),
      });
    }
    return weeks;
  }
  return days.map((d, i) => {
    const date = parseDay(d.day);
    return {
      key: d.day,
      tick:
        range === "week"
          ? fmt(date, { weekday: "short" })
          : i % 5 === 0
            ? fmt(date, { month: "short", day: "numeric" })
            : "",
      label:
        range === "week"
          ? fmt(date, { weekday: "long", month: "short", day: "numeric" })
          : fmt(date, { month: "short", day: "numeric" }),
      seconds: d.seconds,
    };
  });
}

// Recharts left alone picks awkward tick values (8m, 23m); give the y-axis
// clean minute steps with the top tick just above the tallest bar.
export function niceTicks(maxSeconds: number): number[] {
  const STEP_MINUTES = [1, 2, 5, 10, 15, 30, 60, 120, 240, 480];
  const maxMinutes = Math.max(1, Math.ceil(maxSeconds / 60));
  const step =
    STEP_MINUTES.find((s) => Math.ceil(maxMinutes / s) <= 4) ??
    Math.ceil(maxMinutes / 4);
  const top = step * Math.ceil(maxMinutes / step);
  const ticks = [];
  for (let m = 0; m <= top; m += step) ticks.push(m * 60);
  return ticks;
}

export function ChartTip({
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
        {formatDuration(point.seconds)}
      </p>
    </div>
  );
}

const tickStyle = { fontSize: 10.5, fill: "var(--ink-faint)", fontFamily: "var(--mono)" };

export default function ActivityChart({
  days,
  range,
}: {
  days: ActivityDay[];
  range: ActivityRange;
}) {
  const data = buildChartData(days, range);
  const ticks = niceTicks(Math.max(...data.map((p) => p.seconds), 0));
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
            tickFormatter={(s: number) => formatDuration(s)}
            tickLine={false}
            axisLine={false}
            width={46}
            ticks={ticks}
            domain={[0, ticks[ticks.length - 1]]}
            tick={tickStyle}
          />
          <Tooltip content={<ChartTip />} cursor={{ fill: "var(--bg-hover)" }} />
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
