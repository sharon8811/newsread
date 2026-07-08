import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import React from "react";
import UsageChart, { niceCountTicks } from "@/components/UsageChart";
import type { UsageDay } from "@/lib/api";

// jsdom has no layout, so ResponsiveContainer measures 0×0 and renders
// nothing; hand the chart a fixed size instead.
vi.mock("recharts", async (importOriginal) => {
  const original = await importOriginal<typeof import("recharts")>();
  return {
    ...original,
    ResponsiveContainer: ({
      children,
    }: {
      children: React.ReactElement<{ width?: number; height?: number }>;
    }) => React.cloneElement(children, { width: 800, height: 220 }),
  };
});

function makeDays(count: number): UsageDay[] {
  return Array.from({ length: count }, (_, i) => ({
    day: `2026-06-${String(i + 1).padStart(2, "0")}`,
    calls: i,
    tokens: (i + 1) * 1000,
  }));
}

describe("niceCountTicks", () => {
  it("keeps at most five ticks with clean steps", () => {
    expect(niceCountTicks(0)).toEqual([0, 1]);
    expect(niceCountTicks(7)).toEqual([0, 2, 4, 6, 8]);
    expect(niceCountTicks(950)).toEqual([0, 500, 1000]);
    expect(niceCountTicks(12_400)).toEqual([0, 5000, 10_000, 15_000]);
  });

  it("tops out just above the peak", () => {
    const ticks = niceCountTicks(3_141);
    expect(ticks[ticks.length - 1]).toBeGreaterThanOrEqual(3_141);
  });
});

describe("<UsageChart>", () => {
  it("renders one bar per day for a week", () => {
    const { container } = render(<UsageChart days={makeDays(7)} range="week" />);
    expect(container.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(7);
  });

  it("folds a year of days into week buckets", () => {
    const { container } = render(<UsageChart days={makeDays(28)} range="year" />);
    expect(container.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(4);
  });
});
