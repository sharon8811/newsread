import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Sidebar from "@/components/Sidebar";
import { makeFeed, makeUser } from "./fixtures";

const { pushMock, pathState, searchState } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  pathState: { value: "/" },
  searchState: { feed: null as string | null },
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
  usePathname: () => pathState.value,
  useSearchParams: () => ({
    get: (k: string) => (k === "feed" ? searchState.feed : null),
  }),
}));

const { swrMock, mutateMock } = vi.hoisted(() => ({
  swrMock: vi.fn(),
  mutateMock: vi.fn(),
}));
vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));

const { authState } = vi.hoisted(() => ({
  authState: { user: null as unknown, logout: vi.fn() },
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => authState }));

const { settingsProps } = vi.hoisted(() => ({
  settingsProps: { current: null as Record<string, unknown> | null },
}));
vi.mock("@/components/FeedSettingsModal", () => ({
  default: (props: Record<string, unknown>) => {
    settingsProps.current = props;
    return <div data-testid="feed-settings-modal" />;
  },
}));

type SwrData = {
  feeds?: unknown;
  unseen?: unknown;
  projects?: unknown;
  ai?: unknown;
  config?: unknown;
  history?: unknown;
};
function setSwr({
  feeds,
  unseen,
  projects,
  ai,
  config = { browser_history_enabled: false },
  history,
}: SwrData) {
  swrMock.mockImplementation((key: string) => {
    if (key === "/feeds") return { data: feeds };
    if (key === "/shares/unseen-count") return { data: unseen };
    if (key === "/projects") return { data: projects };
    if (key === "/ai/settings") return { data: ai };
    if (key === "/config") return { data: config };
    if (key === "/history/summary") return { data: history };
    return { data: undefined };
  });
}

function okFetch(body: unknown = {}) {
  return vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => body });
}

describe("<Sidebar>", () => {
  beforeEach(() => {
    pushMock.mockClear();
    mutateMock.mockClear();
    pathState.value = "/";
    searchState.feed = null;
    authState.user = makeUser();
    authState.logout = vi.fn();
    setSwr({ feeds: [], unseen: { count: 0 } });
  });

  it("renders nav links and the user card", () => {
    render(<Sidebar />);
    expect(screen.getByText("Inbox")).toBeInTheDocument();
    expect(screen.getByText("Shared with me")).toBeInTheDocument();
    expect(screen.getByText("Sent")).toBeInTheDocument();
    expect(screen.getByText("Saved")).toBeInTheDocument();
    expect(screen.getByText("Imported").closest("a")).toHaveAttribute("href", "/imported");
    expect(screen.getByText("Projects").closest("a")).toHaveAttribute("href", "/projects");
    expect(screen.getByText("Activity").closest("a")).toHaveAttribute("href", "/activity");
    expect(screen.getByText("Alice")).toBeInTheDocument();
    expect(screen.getByText("@alice")).toBeInTheDocument();
    // avatar initial
    expect(screen.getByText("A")).toBeInTheDocument();
    // empty feeds message
    expect(screen.getByText(/No feeds yet/)).toBeInTheDocument();
  });

  it("shows the total unread badge and per-feed unread counts", () => {
    setSwr({
      feeds: [
        makeFeed({ id: 1, title: "Feed One", unread_count: 3 }),
        makeFeed({ id: 2, title: "Feed Two", unread_count: 2 }),
      ],
      unseen: { count: 5 },
    });
    render(<Sidebar />);
    // total unread = 5 (Inbox badge) and shared unseen = 5
    expect(screen.getAllByText("5").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Feed One")).toBeInTheDocument();
    expect(screen.getByText("Feed Two")).toBeInTheDocument();
    // per-feed unread counts
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("marks a feed active when the feed query param matches", () => {
    searchState.feed = "1";
    setSwr({
      feeds: [makeFeed({ id: 1, title: "Active Feed", unread_count: 0 })],
      unseen: { count: 0 },
    });
    render(<Sidebar />);
    const link = screen.getByText("Active Feed").closest("a")!;
    expect(link).toHaveStyle({ background: "var(--bg-hover)" });
    // unread_count 0 -> no count span rendered inside
  });

  it("marks Shared active when on the /shared path", () => {
    pathState.value = "/shared";
    render(<Sidebar />);
    const link = screen.getByText("Shared with me").closest("a")!;
    expect(link).toHaveStyle({ background: "var(--bg-hover)" });
  });

  it("sums project unseen counts into the Projects badge", async () => {
    const { makeProject } = await import("./fixtures");
    setSwr({
      feeds: [],
      unseen: { count: 0 },
      projects: [
        makeProject({ id: 1, unseen_count: 2 }),
        makeProject({ id: 2, unseen_count: 5 }),
      ],
    });
    render(<Sidebar />);
    const link = screen.getByText("Projects").closest("a")!;
    expect(link.textContent).toContain("7");
  });

  it("marks Projects active on any /projects path", () => {
    pathState.value = "/projects/4";
    render(<Sidebar />);
    const link = screen.getByText("Projects").closest("a")!;
    expect(link).toHaveStyle({ background: "var(--bg-hover)" });
  });

  it("shows History only when enabled and the user has a connection or history", () => {
    setSwr({
      feeds: [],
      unseen: { count: 0 },
      config: { browser_history_enabled: true },
      history: {
        has_active_connection: true,
        has_history: false,
        active_connection_count: 1,
        total_connection_count: 1,
        history_count: 0,
      },
    });
    const { rerender } = render(<Sidebar />);
    expect(screen.getByText("History").closest("a")).toHaveAttribute("href", "/history");

    setSwr({
      feeds: [],
      unseen: { count: 0 },
      config: { browser_history_enabled: false },
      history: { has_active_connection: true, has_history: true },
    });
    rerender(<Sidebar />);
    expect(screen.queryByText("History")).not.toBeInTheDocument();
  });

  it("toggles the add-feed form open and closed", async () => {
    render(<Sidebar />);
    expect(screen.queryByPlaceholderText(/example.com\/feed/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByTitle("Add feed"));
    expect(screen.getByPlaceholderText(/example.com\/feed/)).toBeInTheDocument();
    await userEvent.click(screen.getByTitle("Add feed"));
    expect(screen.queryByPlaceholderText(/example.com\/feed/)).not.toBeInTheDocument();
  });

  it("does nothing when submitting an empty url", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<Sidebar />);
    await userEvent.click(screen.getByTitle("Add feed"));
    await userEvent.click(screen.getByRole("button", { name: "Subscribe" }));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("adds a feed and navigates to it on success", async () => {
    const fetchMock = okFetch(makeFeed({ id: 42, title: "New Feed" }));
    vi.stubGlobal("fetch", fetchMock);
    render(<Sidebar />);
    await userEvent.click(screen.getByTitle("Add feed"));
    await userEvent.type(
      screen.getByPlaceholderText(/example.com\/feed/),
      "https://new.example/rss",
    );
    await userEvent.click(screen.getByRole("button", { name: "Subscribe" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][0]).toContain("/feeds");
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/?feed=42"));
    expect(mutateMock).toHaveBeenCalledWith("/feeds");
    // form closed after success
    await waitFor(() =>
      expect(screen.queryByPlaceholderText(/example.com\/feed/)).not.toBeInTheDocument(),
    );
  });

  it("shows an error message when adding a feed fails (ApiError)", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ status: 400, ok: false, json: async () => ({ detail: "Bad feed URL" }) });
    vi.stubGlobal("fetch", fetchMock);
    render(<Sidebar />);
    await userEvent.click(screen.getByTitle("Add feed"));
    await userEvent.type(
      screen.getByPlaceholderText(/example.com\/feed/),
      "https://bad.example/rss",
    );
    await userEvent.click(screen.getByRole("button", { name: "Subscribe" }));
    await waitFor(() => expect(screen.getByText("Bad feed URL")).toBeInTheDocument());
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("falls back to a generic message on a non-Error rejection", async () => {
    const fetchMock = vi.fn().mockRejectedValue("boom");
    vi.stubGlobal("fetch", fetchMock);
    render(<Sidebar />);
    await userEvent.click(screen.getByTitle("Add feed"));
    await userEvent.type(
      screen.getByPlaceholderText(/example.com\/feed/),
      "https://x.example/rss",
    );
    await userEvent.click(screen.getByRole("button", { name: "Subscribe" }));
    await waitFor(() => expect(screen.getByText("Could not add feed")).toBeInTheDocument());
  });

  it("opens feed settings and navigates home when the active feed unsubscribes", async () => {
    const { act } = await import("@testing-library/react");
    searchState.feed = "7";
    setSwr({
      feeds: [makeFeed({ id: 7, title: "Doomed Feed", unread_count: 0 })],
      unseen: { count: 0 },
    });
    render(<Sidebar />);
    expect(screen.queryByTestId("feed-settings-modal")).not.toBeInTheDocument();
    await userEvent.click(screen.getByTitle("Feed settings"));
    expect(screen.getByTestId("feed-settings-modal")).toBeInTheDocument();
    act(() => (settingsProps.current!.onUnsubscribed as () => void)());
    expect(pushMock).toHaveBeenCalledWith("/");
    act(() => (settingsProps.current!.onClose as () => void)());
    await waitFor(() =>
      expect(screen.queryByTestId("feed-settings-modal")).not.toBeInTheDocument(),
    );
  });

  it("does not navigate when a non-active feed unsubscribes", async () => {
    const { act } = await import("@testing-library/react");
    searchState.feed = null;
    setSwr({
      feeds: [makeFeed({ id: 9, title: "Other Feed", unread_count: 1 })],
      unseen: { count: 0 },
    });
    render(<Sidebar />);
    await userEvent.click(screen.getByTitle("Feed settings"));
    act(() => (settingsProps.current!.onUnsubscribed as () => void)());
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("mutes: dims the feed, hides its count, and excludes it from the Inbox total", () => {
    setSwr({
      feeds: [
        makeFeed({ id: 1, title: "Loud", unread_count: 4 }),
        makeFeed({ id: 2, title: "Quiet", unread_count: 9, is_muted: true }),
      ],
      unseen: { count: 0 },
    });
    render(<Sidebar />);
    // "4" renders twice: the Inbox badge (muted excluded) and Loud's own count.
    expect(screen.getAllByText("4")).toHaveLength(2);
    expect(screen.queryByText("9")).not.toBeInTheDocument();
    expect(screen.queryByText("13")).not.toBeInTheDocument();
    expect(screen.getByTitle("Muted")).toBeInTheDocument();
    expect(screen.getByText("Quiet")).toHaveStyle({ color: "var(--ink-faint)" });
  });

  it("logs out and redirects to /login", async () => {
    render(<Sidebar />);
    await userEvent.click(screen.getByTitle("Sign out"));
    expect(authState.logout).toHaveBeenCalled();
    expect(pushMock).toHaveBeenCalledWith("/login");
  });

  it("shows a fallback avatar and no name when the user is absent", () => {
    authState.user = null;
    render(<Sidebar />);
    expect(screen.getByText("?")).toBeInTheDocument();
  });

  it("handles undefined SWR data gracefully (loading state)", () => {
    setSwr({ feeds: undefined, unseen: undefined });
    render(<Sidebar />);
    // Inbox renders with no unread badge (totalUnread falls back to 0)
    expect(screen.getByText("Inbox")).toBeInTheDocument();
    // no empty-feeds message either, since feeds?.length is undefined
    expect(screen.queryByText(/No feeds yet/)).not.toBeInTheDocument();
  });

  it("does not show the empty-feeds message while the add form is open", async () => {
    render(<Sidebar />);
    await userEvent.click(screen.getByTitle("Add feed"));
    expect(screen.queryByText(/No feeds yet/)).not.toBeInTheDocument();
  });
});


describe("<Sidebar> AI usage link", () => {
  beforeEach(() => {
    pathState.value = "/";
    searchState.feed = null;
    authState.user = makeUser();
    authState.logout = vi.fn();
  });

  it("is hidden without an own AI key", () => {
    setSwr({ feeds: [], unseen: { count: 0 }, ai: { configured: false } });
    render(<Sidebar />);
    expect(screen.queryByText("AI usage")).not.toBeInTheDocument();
  });

  it("appears once a key is configured", () => {
    setSwr({ feeds: [], unseen: { count: 0 }, ai: { configured: true } });
    render(<Sidebar />);
    const link = screen.getByText("AI usage").closest("a");
    expect(link).toHaveAttribute("href", "/usage");
  });
});
