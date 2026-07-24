import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import HistoryPage from "@/app/(app)/history/page";

const {
  swrMock,
  globalMutateMock,
  localMutateMock,
  notFoundMock,
  toastErrorMock,
  state,
} =
  vi.hoisted(() => ({
    swrMock: vi.fn(),
    globalMutateMock: vi.fn(),
    localMutateMock: vi.fn(),
    notFoundMock: vi.fn(() => {
      throw new Error("NEXT_NOT_FOUND");
    }),
    toastErrorMock: vi.fn(),
    state: {
      enabled: true,
      summary: {
        active_connection_count: 1,
        total_connection_count: 1,
        history_count: 1,
        has_active_connection: true,
        has_history: true,
      },
      pages: [
        {
          id: 41,
          url: "https://example.com/story",
          title: "<img src=x onerror=alert(1)>",
          hostname: "example.com",
          text_excerpt: "A safe plain-text excerpt.",
          first_visited_at: "2026-07-23T09:00:00Z",
          last_visited_at: "2026-07-24T09:00:00Z",
          visit_count: 3,
          captured_at: "2026-07-24T09:00:00Z",
          source_browsers: ["Work Chrome"],
        },
      ] as unknown[],
      historyError: undefined as Error | undefined,
      historyLoading: false,
      nextCursor: null as string | null,
    },
  }));

vi.mock("swr", () => ({ default: swrMock, mutate: globalMutateMock }));
vi.mock("next/navigation", () => ({ notFound: notFoundMock }));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: toastErrorMock },
}));

function installSWR() {
  swrMock.mockImplementation((key: string | null) => {
    if (key === "/config") {
      return { data: { browser_history_enabled: state.enabled } };
    }
    if (key === "/history/summary") return { data: state.summary };
    if (typeof key === "string" && (key === "/history" || key.startsWith("/history?"))) {
      return {
        data:
          state.pages === undefined
            ? undefined
            : { items: state.pages, nextCursor: state.nextCursor },
        isLoading: state.historyLoading,
        error: state.historyError,
        mutate: localMutateMock,
      };
    }
    return { data: undefined, isLoading: false, mutate: localMutateMock };
  });
}

describe("HistoryPage", () => {
  beforeEach(() => {
    state.enabled = true;
    state.summary = {
      active_connection_count: 1,
      total_connection_count: 1,
      history_count: 1,
      has_active_connection: true,
      has_history: true,
    };
    state.pages = [
      {
        id: 41,
        url: "https://example.com/story",
        title: "<img src=x onerror=alert(1)>",
        hostname: "example.com",
        text_excerpt: "A safe plain-text excerpt.",
        first_visited_at: "2026-07-23T09:00:00Z",
        last_visited_at: "2026-07-24T09:00:00Z",
        visit_count: 3,
        captured_at: "2026-07-24T09:00:00Z",
        source_browsers: ["Work Chrome"],
      },
    ];
    state.historyError = undefined;
    state.historyLoading = false;
    state.nextCursor = null;
    installSWR();
  });

  it("renders captured content as text and opens safe URLs defensively", () => {
    render(<HistoryPage />);
    const title = screen.getByText("<img src=x onerror=alert(1)>");
    expect(title.closest("a")).toHaveAttribute("href", "https://example.com/story");
    expect(title.closest("a")).toHaveAttribute("target", "_blank");
    expect(title.closest("a")).toHaveAttribute("rel", "noopener noreferrer");
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText("A safe plain-text excerpt.")).toBeInTheDocument();
    expect(screen.getByText("Work Chrome")).toBeInTheDocument();
  });

  it("renders singular visits, blank-title fallback, and no source browser", () => {
    state.summary.history_count = 2;
    state.pages = [
      {
        ...(state.pages[0] as Record<string, unknown>),
        title: "",
        visit_count: 1,
        source_browsers: [],
      },
    ];
    render(<HistoryPage />);
    expect(screen.getByText("2 saved pages")).toBeInTheDocument();
    expect(screen.getByText("1 visit")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "https://example.com/story" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Work Chrome")).not.toBeInTheDocument();
  });

  it("does not turn a non-http captured URL into a link", () => {
    state.pages = [
      {
        ...(state.pages[0] as Record<string, unknown>),
        url: "javascript:alert(1)",
        title: "Unsafe URL",
      },
    ];
    render(<HistoryPage />);
    expect(screen.getByText("Unsafe URL").closest("a")).toBeNull();
  });

  it("renders a malformed captured URL without creating a link", () => {
    state.pages = [
      {
        ...(state.pages[0] as Record<string, unknown>),
        url: "not a valid URL",
        title: "Malformed URL",
      },
    ];
    render(<HistoryPage />);
    expect(screen.getByText("Malformed URL").closest("a")).toBeNull();
  });

  it("shows pairing setup when the account has no connection or history", () => {
    state.summary = {
      active_connection_count: 0,
      total_connection_count: 0,
      history_count: 0,
      has_active_connection: false,
      has_history: false,
    };
    render(<HistoryPage />);
    expect(screen.getByText("Pair your first browser")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Set up browser history" })).toHaveAttribute(
      "href",
      "/settings#browser-history",
    );
  });

  it("shows a synced-empty state when a browser is paired without saved pages", () => {
    state.summary = {
      active_connection_count: 1,
      total_connection_count: 1,
      history_count: 0,
      has_active_connection: true,
      has_history: false,
    };
    render(<HistoryPage />);
    expect(screen.getByText("No pages synced yet.")).toBeInTheDocument();
    expect(screen.getByText(/Keep the paired extension running/)).toBeInTheDocument();
  });

  it("distinguishes an empty filtered result from an empty history", async () => {
    state.pages = [];
    render(<HistoryPage />);
    await userEvent.type(
      screen.getByLabelText("Search browser history"),
      "no match",
    );
    expect(
      await screen.findByText("Nothing matched those filters."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Try a broader search or clear one of the filters."),
    ).toBeInTheDocument();
  });

  it("shows a retry action when history results fail to load", async () => {
    state.historyError = new Error("unavailable");
    render(<HistoryPage />);
    expect(
      screen.getByText("Could not load these history results."),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(localMutateMock).toHaveBeenCalled();
  });

  it("shows placeholders while history results are loading", () => {
    state.pages = undefined as unknown as unknown[];
    state.historyLoading = true;
    const { container } = render(<HistoryPage />);
    expect(container.querySelectorAll(".animate-pulse")).toHaveLength(3);
  });

  it("moves through cursor pages and resets pagination when filters change", async () => {
    state.nextCursor = "next-page";
    render(<HistoryPage />);

    expect(screen.getByText("Page 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Page 2")).toBeInTheDocument();
    await waitFor(() =>
      expect(
        swrMock.mock.calls.some((call) =>
          String(call[0]).includes("cursor=next-page"),
        ),
      ).toBe(true),
    );

    await userEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(screen.getByText("Page 1")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await userEvent.type(
      screen.getByLabelText("Search browser history"),
      "postgres",
    );
    expect(screen.getByText("Page 1")).toBeInTheDocument();
  });

  it("keys search, domain, date, and sort filters through the API", async () => {
    render(<HistoryPage />);
    expect(screen.getByLabelText("Sort history")).toHaveValue("recent");
    await userEvent.type(screen.getByLabelText("Search browser history"), "100%");
    await userEvent.type(screen.getByLabelText("Filter by domain"), "example.com");
    await userEvent.type(screen.getByLabelText("Visited after"), "2026-07-01");
    await userEvent.type(screen.getByLabelText("Visited before"), "2026-07-31");

    await waitFor(() => {
      const keys = swrMock.mock.calls.map((call) => String(call[0]));
      expect(
        keys.some(
          (key) =>
            key.includes("q=100%25") &&
            key.includes("hostname=example.com") &&
            key.includes("date_from=2026-07-01") &&
            key.includes("date_to=2026-07-31") &&
            key.includes("sort=relevance"),
        ),
      ).toBe(true);
    });

    await userEvent.selectOptions(screen.getByLabelText("Sort history"), "recent");
    await waitFor(() => {
      const keys = swrMock.mock.calls.map((call) => String(call[0]));
      expect(
        keys.some(
          (key) =>
            key.includes("q=100%25") &&
            key.includes("date_to=2026-07-31") &&
            !key.includes("sort="),
        ),
      ).toBe(true);
    });

    await userEvent.selectOptions(
      screen.getByLabelText("Sort history"),
      "relevance",
    );
    await waitFor(() =>
      expect(
        swrMock.mock.calls.some((call) =>
          String(call[0]).includes("sort=relevance"),
        ),
      ).toBe(true),
    );
  });

  it("holds invalid hostname filters client-side instead of requesting a 422", async () => {
    render(<HistoryPage />);
    await userEvent.type(screen.getByLabelText("Filter by domain"), "bad host");

    expect(
      await screen.findByText("Enter a full domain such as example.com."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Finish entering the domain to filter history."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Filter by domain")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(
      swrMock.mock.calls.some((call) =>
        String(call[0]).includes("hostname=bad"),
      ),
    ).toBe(false);

    await userEvent.clear(screen.getByLabelText("Filter by domain"));
    await userEvent.type(screen.getByLabelText("Filter by domain"), "8.8.8.8");
    expect(
      await screen.findByText("Enter a full domain such as example.com."),
    ).toBeInTheDocument();

    await userEvent.clear(screen.getByLabelText("Filter by domain"));
    await userEvent.type(screen.getByLabelText("Filter by domain"), "%");
    expect(
      await screen.findByText("Finish entering the domain to filter history."),
    ).toBeInTheDocument();

    await userEvent.clear(screen.getByLabelText("Filter by domain"));
    await userEvent.type(
      screen.getByLabelText("Filter by domain"),
      "example.com",
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Filter by domain")).toHaveAttribute(
        "aria-invalid",
        "false",
      ),
    );
    fireEvent.change(screen.getByLabelText("Filter by domain"), {
      target: { value: "[" },
    });
    await waitFor(() =>
      expect(screen.getByLabelText("Filter by domain")).toHaveAttribute(
        "aria-invalid",
        "true",
      ),
    );
  });

  it("deletes one history item only after confirmation", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({ deleted_count: 1, sync_revision: 2 }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<HistoryPage />);
    const button = screen.getByLabelText(/Delete <img/);
    await userEvent.click(button);
    expect(fetchMock).not.toHaveBeenCalled();
    await userEvent.click(button);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][0]).toContain("/history/41");
    expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
    expect(globalMutateMock).toHaveBeenCalledWith("/history/summary");
  });

  it("excludes a domain and requests deletion of its existing history", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      status: 201,
      ok: true,
      json: async () => ({
        id: 8,
        hostname: "example.com",
        match_subdomains: true,
        mode: "exclude",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<HistoryPage />);
    const button = screen.getByLabelText("Exclude example.com and delete its history");
    await userEvent.click(button);
    await userEvent.click(button);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      hostname: "example.com",
      match_subdomains: true,
      mode: "exclude",
      delete_existing: true,
    });
  });

  it("reports delete and exclude request failures", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error("Delete blocked"))
      .mockRejectedValueOnce("offline");
    vi.stubGlobal("fetch", fetchMock);
    render(<HistoryPage />);

    const deleteButton = screen.getByLabelText(/Delete <img/);
    await userEvent.click(deleteButton);
    await userEvent.click(deleteButton);
    await waitFor(() =>
      expect(toastErrorMock).toHaveBeenCalledWith("Delete blocked"),
    );

    const excludeButton = screen.getByLabelText(
      "Exclude example.com and delete its history",
    );
    await userEvent.click(excludeButton);
    await userEvent.click(excludeButton);
    await waitFor(() =>
      expect(toastErrorMock).toHaveBeenCalledWith(
        "Could not exclude the domain",
      ),
    );
  });

  it("routes feature-disabled deployments to not found", () => {
    state.enabled = false;
    expect(() => render(<HistoryPage />)).toThrow("NEXT_NOT_FOUND");
    expect(notFoundMock).toHaveBeenCalled();
  });
});
