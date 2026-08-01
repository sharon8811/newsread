import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdminUsersPage from "@/app/(app)/admin/users/page";
import type { AdminUserRow } from "@/lib/api";
import { makeUser } from "./fixtures";

const { swrState, notFoundMock, authState, mutateMock, toastMock } = vi.hoisted(() => ({
  swrState: { keys: [] as string[], page: undefined as unknown },
  notFoundMock: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
  authState: { user: null as unknown },
  mutateMock: vi.fn(),
  toastMock: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("swr", () => ({
  default: (key: string) => {
    swrState.keys.push(key);
    if (key === "/admin/tiers")
      return {
        data: [
          { key: "free", name: "Free", price_cents: 0, monthly_article_allowance: 100 },
          { key: "paid", name: "Paid", price_cents: 500, monthly_article_allowance: 1000 },
          { key: "team", name: "Team", price_cents: 900, monthly_article_allowance: 5000 },
        ],
      };
    return { data: swrState.page };
  },
  mutate: mutateMock,
}));
vi.mock("next/navigation", () => ({ notFound: notFoundMock }));
vi.mock("@/lib/auth", () => ({ useAuth: () => authState }));
vi.mock("sonner", () => ({ toast: toastMock, Toaster: () => null }));

function makeRow(over: Partial<AdminUserRow> = {}): AdminUserRow {
  return {
    id: 2,
    email: "reader@example.com",
    username: "reader",
    name: "Reader",
    role: "user",
    status: "active",
    created_at: "2026-07-01T10:00:00Z",
    tier_key: "free",
    tier_name: "Free",
    tier_assigned: false,
    quota_allowance: 100,
    quota_used: 37,
    last_active_day: "2026-08-01",
    subscription_count: 4,
    articles_read: 120,
    reading_seconds: 3600,
    llm_tokens: 45_000,
    llm_tokens_system: 30_000,
    ...over,
  };
}

function okFetch(body: unknown = {}) {
  const mock = vi.fn().mockResolvedValue({
    status: 200,
    ok: true,
    statusText: "OK",
    json: async () => body,
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

function lastCall(fetchMock: ReturnType<typeof vi.fn>): { url: string; init: RequestInit } {
  const [url, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
  return { url: String(url), init: init as RequestInit };
}

describe("AdminUsersPage", () => {
  beforeEach(() => {
    swrState.keys = [];
    swrState.page = { total: 1, users: [makeRow()] };
    authState.user = makeUser({ id: 1, username: "boss", role: "owner" });
    notFoundMock.mockClear();
    mutateMock.mockClear();
    toastMock.success.mockClear();
    toastMock.error.mockClear();
  });

  it("renders account rows with badges and aggregates", () => {
    render(<AdminUsersPage />);
    expect(screen.getByText("Reader")).toBeInTheDocument();
    expect(screen.getByText(/@reader · reader@example.com/)).toBeInTheDocument();
    expect(screen.getByText(/Free · 37\/100/)).toBeInTheDocument();
    expect(screen.getByText(/4 feeds · 120 read/)).toBeInTheDocument();
    expect(screen.getByText("1 account")).toBeInTheDocument();
  });

  it("changes a role from the owner-only select", async () => {
    const fetchMock = okFetch(makeRow({ role: "admin" }));
    render(<AdminUsersPage />);
    await userEvent.selectOptions(screen.getByLabelText("Role for reader"), "admin");
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const { url, init } = lastCall(fetchMock);
    expect(url).toContain("/admin/users/2/role");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({ role: "admin" });
    await waitFor(() => expect(toastMock.success).toHaveBeenCalled());
    expect(mutateMock).toHaveBeenCalled();
  });

  it("hides the role select from admins and for yourself", () => {
    authState.user = makeUser({ id: 1, username: "boss", role: "admin" });
    render(<AdminUsersPage />);
    expect(screen.queryByLabelText("Role for reader")).toBeNull();

    authState.user = makeUser({ id: 2, username: "reader", role: "owner" });
    render(<AdminUsersPage />);
    expect(screen.queryByLabelText("Role for reader")).toBeNull();
  });

  it("suspends after an explicit confirmation", async () => {
    const fetchMock = okFetch(makeRow({ status: "suspended" }));
    render(<AdminUsersPage />);
    await userEvent.click(screen.getByRole("button", { name: "Suspend" }));
    expect(fetchMock).not.toHaveBeenCalled(); // armed, not fired
    await userEvent.click(screen.getByRole("button", { name: "Really suspend?" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const { url, init } = lastCall(fetchMock);
    expect(url).toContain("/admin/users/2/status");
    expect(JSON.parse(String(init.body))).toEqual({ status: "suspended" });
  });

  it("reactivates a suspended account without ceremony", async () => {
    swrState.page = { total: 1, users: [makeRow({ status: "suspended" })] };
    const fetchMock = okFetch(makeRow());
    render(<AdminUsersPage />);
    await userEvent.click(screen.getByRole("button", { name: "Reactivate" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(String(lastCall(fetchMock).init.body))).toEqual({ status: "active" });
  });

  it("admins cannot touch another admin's status", () => {
    authState.user = makeUser({ id: 1, username: "boss", role: "admin" });
    swrState.page = { total: 1, users: [makeRow({ role: "admin", username: "peer" })] };
    render(<AdminUsersPage />);
    expect(screen.queryByRole("button", { name: "Suspend" })).toBeNull();
  });

  it("assigns and clears tiers", async () => {
    const fetchMock = okFetch(makeRow({ tier_key: "paid", tier_assigned: true }));
    render(<AdminUsersPage />);
    await userEvent.selectOptions(screen.getByLabelText("Tier for reader"), "paid");
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(String(lastCall(fetchMock).init.body))).toEqual({ tier: "paid" });

    swrState.page = {
      total: 1,
      users: [makeRow({ tier_key: "paid", tier_assigned: true })],
    };
    render(<AdminUsersPage />);
    await userEvent.selectOptions(screen.getAllByLabelText("Tier for reader")[1], "default");
    await waitFor(() =>
      expect(JSON.parse(String(lastCall(fetchMock).init.body))).toEqual({ tier: null }),
    );
  });

  it("surfaces API refusals as error toasts", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      status: 409,
      ok: false,
      statusText: "Conflict",
      json: async () => ({ detail: "cannot demote the only owner; promote another owner first" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AdminUsersPage />);
    await userEvent.selectOptions(screen.getByLabelText("Role for reader"), "admin");
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    expect(String(toastMock.error.mock.calls[0][0])).toContain("only owner");
  });

  it("filters and search feed the request key", async () => {
    render(<AdminUsersPage />);
    await userEvent.selectOptions(screen.getByLabelText("Filter by role"), "admin");
    expect(swrState.keys.some((k) => k.includes("role=admin"))).toBe(true);
    await userEvent.selectOptions(screen.getByLabelText("Filter by status"), "suspended");
    expect(swrState.keys.some((k) => k.includes("status=suspended"))).toBe(true);
    await userEvent.selectOptions(screen.getByLabelText("Filter by tier"), "paid");
    expect(swrState.keys.some((k) => k.includes("tier=paid"))).toBe(true);
    await userEvent.selectOptions(screen.getByLabelText("Sort users"), "username");
    expect(swrState.keys.some((k) => k.includes("sort=username"))).toBe(true);
    await userEvent.type(screen.getByLabelText("Search users"), "zeta");
    await waitFor(() => expect(swrState.keys.some((k) => k.includes("query=zeta"))).toBe(true));
  });

  it("paginates with a bounded offset", async () => {
    swrState.page = {
      total: 30,
      users: Array.from({ length: 25 }, (_, i) => makeRow({ id: i + 10, username: `u${i}` })),
    };
    render(<AdminUsersPage />);
    expect(screen.getByText("1–25 of 30")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(swrState.keys.some((k) => k.includes("offset=25"))).toBe(true);
  });

  it("shows the empty state when nothing matches", () => {
    swrState.page = { total: 0, users: [] };
    render(<AdminUsersPage />);
    expect(screen.getByText("No accounts match")).toBeInTheDocument();
  });

  it("hides behind notFound for regular users", () => {
    authState.user = makeUser({ id: 5, role: "user" });
    expect(() => render(<AdminUsersPage />)).toThrow("NEXT_NOT_FOUND");
  });
});


describe("configured tiers feed the pickers", () => {
  beforeEach(() => {
    swrState.page = { total: 1, users: [makeRow()] };
    authState.user = makeUser({ id: 1, username: "boss", role: "owner" });
  });

  it("offers every configured tier, not a hard-coded list", () => {
    render(<AdminUsersPage />);
    const assign = screen.getByLabelText("Tier for reader");
    expect(Array.from(assign.querySelectorAll("option")).map((o) => o.value)).toEqual([
      "default",
      "free",
      "paid",
      "team",
    ]);
    const filter = screen.getByLabelText("Filter by tier");
    expect(Array.from(filter.querySelectorAll("option")).map((o) => o.value)).toEqual([
      "",
      "free",
      "paid",
      "team",
    ]);
  });

  it("keeps showing an assigned tier whose row was deleted", () => {
    swrState.page = {
      total: 1,
      users: [makeRow({ tier_key: "legacy", tier_name: "Legacy", tier_assigned: true })],
    };
    render(<AdminUsersPage />);
    const assign = screen.getByLabelText("Tier for reader") as HTMLSelectElement;
    expect(assign.value).toBe("legacy");
    expect(Array.from(assign.querySelectorAll("option")).some((o) => o.value === "legacy")).toBe(
      true,
    );
  });
});
