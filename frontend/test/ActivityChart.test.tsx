import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import React from "react";
import ActivityChart, { buildChartData, ChartTip, niceTicks } from "@/components/ActivityChart";
import type { ActivityDay } from "@/lib/api";

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

function makeDays(count: number, start = new Date(2026, 5, 1)): ActivityDay[] {
  return Array.from({ length: count }, (_, i) => {
    const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
      d.getDate(),
    ).padStart(2, "0")}`;
    return { day, seconds: (i + 1) * 60 };
  });
}

describe("buildChartData", () => {
  it("labels week points with weekdays", () => {
    const points = buildChartData(makeDays(7), "week"); // 2026-06-01 is a Monday
    expect(points).toHaveLength(7);
    expect(points[0].tick).toBe("Mon");
    expect(points[0].label).toBe("Monday, Jun 1");
    expect(points[0].seconds).toBe(60);
  });

  it("shows a month tick every fifth day only", () => {
    const points = buildChartData(makeDays(30), "month");
    expect(points).toHaveLength(30);
    expect(points[0].tick).toBe("Jun 1");
    expect(points[1].tick).toBe("");
    expect(points[5].tick).toBe("Jun 6");
    expect(points[2].label).toBe("Jun 3");
  });

  it("folds a year into week buckets with month-boundary ticks", () => {
    const points = buildChartData(makeDays(28), "year");
    expect(points).toHaveLength(4);
    // 1+2+...+7 minutes for the first week
    expect(points[0].seconds).toBe(28 * 60);
    expect(points[0].tick).toBe("Jun");
    expect(points[1].tick).toBe(""); // same month → no repeated tick
    expect(points[0].label).toBe("Week of Jun 1");
    const crossing = buildChartData(makeDays(42), "year");
    expect(crossing[4].tick).toBe(""); // week starting Jun 29 still belongs to Jun
    expect(crossing[4].label).toBe("Week of Jun 29");
    expect(crossing[5].tick).toBe("Jul"); // first week starting in July gets the tick
  });
});

describe("niceTicks", () => {
  it("picks clean minute steps topping out just above the max", () => {
    expect(niceTicks(23 * 60)).toEqual([0, 600, 1200, 1800]); // 10m steps
    expect(niceTicks(9 * 60)).toEqual([0, 300, 600]); // 5m steps
  });

  it("handles an all-zero series", () => {
    expect(niceTicks(0)).toEqual([0, 60]);
  });

  it("scales up to hours", () => {
    expect(niceTicks(7 * 3600)).toEqual([0, 7200, 14400, 21600, 28800]); // 2h steps
  });
});

describe("ChartTip", () => {
  const point = { key: "2026-06-01", tick: "Mon", label: "Monday, Jun 1", seconds: 120 };

  it("renders nothing when inactive", () => {
    const { container } = render(<ChartTip active={false} payload={[{ payload: point }]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing without a payload", () => {
    const { container } = render(<ChartTip active payload={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the day label and duration", () => {
    const { getByText } = render(<ChartTip active payload={[{ payload: point }]} />);
    expect(getByText("Monday, Jun 1")).toBeInTheDocument();
    expect(getByText("2m")).toBeInTheDocument();
  });
});

describe("<ActivityChart>", () => {
  it("renders one bar per day", () => {
    const { container } = render(<ActivityChart days={makeDays(7)} range="week" />);
    expect(container.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(7);
  });

  it("renders weekly bars for the year range", () => {
    const { container } = render(<ActivityChart days={makeDays(28)} range="year" />);
    expect(container.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(4);
  });
});
