import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdminPage from "@/app/(app)/admin/page";
import type { AdminOverview, AdminTrends } from "@/lib/api";
import { makeUser } from "./fixtures";

const { swrState, notFoundMock, authState } = vi.hoisted(() => ({
  swrState: {
    keys: [] as string[],
    overview: undefined as unknown,
    trends: undefined as unknown,
  },
  notFoundMock: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
  authState: { user: null as unknown },
}));

vi.mock("swr", () => ({
  default: (key: string) => {
    swrState.keys.push(key);
    if (key === "/admin/overview") return { data: swrState.overview };
    if (key.startsWith("/admin/trends")) return { data: swrState.trends };
    return { data: undefined };
  },
}));
vi.mock("next/navigation", () => ({ notFound: notFoundMock }));
vi.mock("@/lib/auth", () => ({ useAuth: () => authState }));
vi.mock("@/components/AdminTrendChart", () => ({
  default: ({ points, unit }: { points: Array<{ value: number }>; unit: string }) => (
    <div data-testid="trend-chart">
      {unit}:{points.reduce((sum, p) => sum + p.value, 0)}
    </div>
  ),
}));

function makeOverview(over: Partial<AdminOverview> = {}): AdminOverview {
  return {
    users_total: 12,
    users_new_7d: 3,
    users_suspended: 1,
    active_today: 5,
    active_7d: 8,
    active_30d: 10,
    subscriptions_total: 40,
    articles_total: 900,
    articles_ingested_24h: 60,
    articles_summarized_24h: 50,
    articles_skipped_24h: 4,
    articles_failed_24h: 2,
    llm_calls_7d: 300,
    llm_tokens_7d: 1_500_000,
    llm_errors_7d: 7,
    llm_tokens_7d_user: 500_000,
    llm_tokens_7d_system: 1_000_000,
    ...over,
  };
}

function makeTrends(over: Partial<AdminTrends> = {}): AdminTrends {
  return {
    range: "month",
    days: Array.from({ length: 30 }, (_, i) => ({
      day: `2026-07-${String(i + 1).padStart(2, "0")}`,
      new_users: 1,
      active_users: 2,
      new_subscriptions: 0,
      articles_ingested: 3,
      articles_summarized: 2,
      articles_skipped: 0,
      articles_failed: 0,
      articles_read: 4,
      reading_seconds: 60,
      llm_calls: 5,
      llm_tokens: 1000,
      llm_errors: 0,
    })),
    llm_by_feature: [{ feature: "summary", calls: 120, tokens: 24_000 }],
    llm_by_model: [{ provider: "system", model: "gpt-5-mini", calls: 120, tokens: 24_000 }],
    llm_tokens_user: 4_000,
    llm_tokens_system: 20_000,
    ...over,
  };
}

describe("AdminPage", () => {
  beforeEach(() => {
    swrState.keys = [];
    swrState.overview = makeOverview();
    swrState.trends = makeTrends();
    authState.user = makeUser({ role: "admin" });
    notFoundMock.mockClear();
  });

  it("renders the overview tiles", () => {
    render(<AdminPage />);
    expect(screen.getByText("Instance admin")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument(); // users_total
    expect(screen.getByText(/\+3 this week · 1 suspended/)).toBeInTheDocument();
    expect(screen.getByText(/8 this week · 10 this month/)).toBeInTheDocument();
    expect(screen.getByText(/50 summarized · 4 skipped · 2 failed/)).toBeInTheDocument();
    expect(screen.getByText(/1\.0M system · 500k user keys/)).toBeInTheDocument();
    expect(screen.getByText(/7 failed/)).toBeInTheDocument();
  });

  it("shows a skeleton before the overview arrives", () => {
    swrState.overview = undefined;
    const { container } = render(<AdminPage />);
    expect(container.querySelectorAll(".fade-up")).toHaveLength(0);
  });

  it("charts the selected metric and switches metrics", async () => {
    render(<AdminPage />);
    // Default metric: active users (2 × 30 days).
    expect(screen.getByTestId("trend-chart")).toHaveTextContent("users:60");
    await userEvent.selectOptions(screen.getByLabelText("Trend metric"), "llm_tokens");
    expect(screen.getByTestId("trend-chart")).toHaveTextContent("tokens:30000");
  });

  it("requests trends for the selected range", async () => {
    render(<AdminPage />);
    expect(swrState.keys).toContain("/admin/trends?range=month");
    await userEvent.click(screen.getByRole("button", { name: "Week" }));
    expect(swrState.keys).toContain("/admin/trends?range=week");
  });

  it("renders the LLM breakdowns and billing split", () => {
    render(<AdminPage />);
    expect(screen.getByText("Summaries")).toBeInTheDocument(); // feature label
    expect(screen.getByText("gpt-5-mini")).toBeInTheDocument();
    expect(screen.getByText(/20k system-key tokens · 4\.0k on user keys/)).toBeInTheDocument();
  });

  it("links to user management", () => {
    render(<AdminPage />);
    expect(screen.getByRole("link", { name: "Manage users" })).toHaveAttribute(
      "href",
      "/admin/users",
    );
  });

  it("hides behind notFound for regular users", () => {
    authState.user = makeUser({ role: "user" });
    expect(() => render(<AdminPage />)).toThrow("NEXT_NOT_FOUND");
    expect(notFoundMock).toHaveBeenCalled();
  });

  it("waits for the user before deciding authorization", () => {
    authState.user = null;
    render(<AdminPage />);
    expect(notFoundMock).not.toHaveBeenCalled();
  });
});
