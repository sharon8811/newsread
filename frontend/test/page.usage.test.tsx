import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import UsagePage from "@/app/(app)/usage/page";
import type { UsageEvent, UsageSummary } from "@/lib/api";

const { swrState } = vi.hoisted(() => ({
  swrState: {
    keys: [] as string[],
    summary: undefined as UsageSummary | undefined,
    events: undefined as UsageEvent[] | undefined,
  },
}));

vi.mock("swr", () => ({
  default: (key: string) => {
    swrState.keys.push(key);
    if (key.startsWith("/usage/summary")) return { data: swrState.summary };
    if (key.startsWith("/usage/events")) return { data: swrState.events };
    return { data: undefined };
  },
}));
vi.mock("@/components/UsageChart", () => ({
  default: ({ range }: { range: string }) => <div data-testid="chart">{range}</div>,
}));

function makeSummary(overrides: Partial<UsageSummary> = {}): UsageSummary {
  return {
    range: "week",
    configured: true,
    total_calls: 12,
    total_tokens: 34_500,
    prev_total_tokens: 23_000,
    error_count: 1,
    days: Array.from({ length: 7 }, (_, i) => ({
      day: `2026-07-0${i + 1}`,
      calls: i,
      tokens: i * 1000,
    })),
    by_feature: [
      { feature: "qa", calls: 8, tokens: 30_000 },
      { feature: "summary", calls: 4, tokens: 4_500 },
    ],
    by_model: [{ provider: "openai", model: "gpt-5", calls: 12, tokens: 34_500 }],
    ...overrides,
  };
}

function makeEvent(overrides: Partial<UsageEvent> = {}): UsageEvent {
  return {
    id: 1,
    feature: "qa",
    provider: "openai",
    model: "gpt-5",
    prompt_tokens: 900,
    completion_tokens: 100,
    duration_ms: 1200,
    status: "ok",
    error: null,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

describe("UsagePage", () => {
  beforeEach(() => {
    swrState.keys = [];
    swrState.summary = makeSummary();
    swrState.events = [makeEvent()];
  });

  it("shows a loading skeleton before data arrives", () => {
    swrState.summary = undefined;
    const { container } = render(<UsagePage />);
    expect(screen.getByText("AI usage")).toBeInTheDocument();
    expect(container.querySelectorAll(".fade-up")).toHaveLength(0);
  });

  it("renders totals, delta and breakdowns", () => {
    render(<UsagePage />);
    expect(screen.getByText("This week")).toBeInTheDocument();
    expect(screen.getByText("35k tokens")).toBeInTheDocument();
    // (34500 - 23000) / 23000 → +50%
    expect(screen.getByText(/▲ 50%/)).toBeInTheDocument();
    expect(screen.getByText("Calls")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("Failed calls")).toBeInTheDocument();
    expect(screen.getAllByText("Q&A").length).toBeGreaterThan(0);
    expect(screen.getByText("Summaries")).toBeInTheDocument();
    expect(screen.getByText("By model")).toBeInTheDocument();
    expect(screen.getByTestId("chart")).toHaveTextContent("week");
  });

  it("switches ranges and refetches", async () => {
    render(<UsagePage />);
    await userEvent.click(screen.getByRole("button", { name: "Month" }));
    expect(swrState.keys.some((k) => k === "/usage/summary?range=month")).toBe(true);
    expect(screen.getByText("Past 30 days")).toBeInTheDocument();
  });

  it("lists recent calls with token counts and errors", () => {
    swrState.events = [
      makeEvent({ id: 2, model: "gpt-5" }),
      makeEvent({ id: 1, status: "error", error: "rate limited", feature: "share" }),
    ];
    render(<UsagePage />);
    expect(screen.getByText("Recent calls")).toBeInTheDocument();
    expect(screen.getByText("1.0k tok")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(screen.getByText("Share messages")).toBeInTheDocument();
  });

  it("points key-less users at settings when there is no history", () => {
    swrState.summary = makeSummary({
      configured: false,
      total_calls: 0,
      total_tokens: 0,
      prev_total_tokens: 0,
      error_count: 0,
      days: [],
      by_feature: [],
      by_model: [],
    });
    render(<UsagePage />);
    expect(screen.getByText("No AI usage on your own key yet.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
  });

  it("shows an in-window empty state when the range has no calls", () => {
    swrState.summary = makeSummary({
      total_calls: 0,
      total_tokens: 0,
      by_feature: [],
      by_model: [],
    });
    render(<UsagePage />);
    expect(screen.getByText("Nothing in this window.")).toBeInTheDocument();
  });
});
