import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ActivityPage from "@/app/(app)/activity/page";
import type { ActivitySummary } from "@/lib/api";

const { swrState } = vi.hoisted(() => ({
  swrState: {
    key: null as string | null,
    data: undefined as ActivitySummary | undefined,
  },
}));

vi.mock("swr", () => ({
  default: (key: string) => {
    swrState.key = key;
    return { data: swrState.data };
  },
}));
vi.mock("@/components/ActivityChart", () => ({
  default: ({ range }: { range: string }) => <div data-testid="chart">{range}</div>,
}));

function makeSummary(overrides: Partial<ActivitySummary> = {}): ActivitySummary {
  return {
    range: "week",
    total_seconds: 2 * 3600 + 14 * 60,
    prev_total_seconds: 3600,
    days: Array.from({ length: 7 }, (_, i) => ({
      day: `2026-07-0${i + 1}`,
      seconds: i * 60,
    })),
    streak_days: 5,
    top_feeds: [
      { feed_id: 1, title: "The Verge", seconds: 5040 },
      { feed_id: 2, title: "Ars Technica", seconds: 1860 },
    ],
    top_articles: [
      { article_id: 42, title: "A long read", feed_title: "The Verge", seconds: 1080 },
    ],
    ...overrides,
  };
}

describe("ActivityPage", () => {
  beforeEach(() => {
    swrState.key = null;
    swrState.data = makeSummary();
  });

  it("shows a loading skeleton before data arrives", () => {
    swrState.data = undefined;
    const { container } = render(<ActivityPage />);
    expect(screen.getByText("Activity")).toBeInTheDocument();
    expect(container.querySelectorAll(".fade-up")).toHaveLength(0);
  });

  it("renders the hero total, delta, average and streak", () => {
    render(<ActivityPage />);
    expect(screen.getByText("This week")).toBeInTheDocument();
    expect(screen.getByText("2h 14m")).toBeInTheDocument();
    // (8040 - 3600) / 3600 → +123%
    expect(screen.getByText(/▲ 123%/)).toBeInTheDocument();
    expect(screen.getByText("vs last week")).toBeInTheDocument();
    expect(screen.getByText("Daily average")).toBeInTheDocument();
    expect(screen.getByText("19m")).toBeInTheDocument(); // 8040s / 7d
    expect(screen.getByText("5 days")).toBeInTheDocument();
  });

  it("requests the summary with the local day", () => {
    render(<ActivityPage />);
    expect(swrState.key).toMatch(/^\/activity\/summary\?range=week&today=\d{4}-\d{2}-\d{2}$/);
  });

  it("switches ranges and refetches", async () => {
    render(<ActivityPage />);
    await userEvent.click(screen.getByText("Month"));
    expect(swrState.key).toContain("range=month");
    expect(screen.getByText("Past 30 days")).toBeInTheDocument();
    expect(screen.getByText("vs previous 30 days")).toBeInTheDocument();
    await userEvent.click(screen.getByText("Year"));
    expect(swrState.key).toContain("range=year");
    expect(screen.getByText(/Reading time per week/)).toBeInTheDocument();
  });

  it("shows a falling delta with a down arrow", () => {
    swrState.data = makeSummary({ total_seconds: 1800, prev_total_seconds: 3600 });
    render(<ActivityPage />);
    expect(screen.getByText(/▼ 50%/)).toBeInTheDocument();
  });

  it("omits the delta when there is no previous data", () => {
    swrState.data = makeSummary({ prev_total_seconds: 0 });
    render(<ActivityPage />);
    expect(screen.queryByText(/vs last week/)).not.toBeInTheDocument();
  });

  it("uses the singular for a one-day streak", () => {
    swrState.data = makeSummary({ streak_days: 1 });
    render(<ActivityPage />);
    expect(screen.getByText("1 day")).toBeInTheDocument();
  });

  it("lists top sources with durations and links top articles", () => {
    render(<ActivityPage />);
    expect(screen.getByText("Top sources")).toBeInTheDocument();
    // Appears as a top source and as the top article's feed subtitle.
    expect(screen.getAllByText("The Verge")).toHaveLength(2);
    expect(screen.getByText("1h 24m")).toBeInTheDocument(); // 5040s
    expect(screen.getByText("Most read articles")).toBeInTheDocument();
    expect(screen.getByText("A long read").closest("a")).toHaveAttribute(
      "href",
      "/article/42",
    );
  });

  it("hides the top lists when they are empty", () => {
    swrState.data = makeSummary({ top_feeds: [], top_articles: [] });
    render(<ActivityPage />);
    expect(screen.queryByText("Top sources")).not.toBeInTheDocument();
    expect(screen.queryByText("Most read articles")).not.toBeInTheDocument();
    expect(screen.getByTestId("chart")).toBeInTheDocument();
  });

  it("shows the empty state for a brand-new reader", () => {
    swrState.data = makeSummary({
      total_seconds: 0,
      prev_total_seconds: 0,
      streak_days: 0,
      top_feeds: [],
      top_articles: [],
      days: [],
    });
    render(<ActivityPage />);
    expect(screen.getByText("Nothing on the clock yet.")).toBeInTheDocument();
    expect(screen.queryByTestId("chart")).not.toBeInTheDocument();
  });
});
