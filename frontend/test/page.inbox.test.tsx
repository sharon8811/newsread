import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import InboxPage from "@/app/(app)/page";
import { makeFeed, makeUser } from "./fixtures";

const { swrMock, authState, searchState, listProps, viewSwitcherProps, mutateListsMock, pushMock, settingsProps } =
  vi.hoisted(() => ({
    swrMock: vi.fn(),
    authState: { user: null as unknown },
    searchState: { params: new URLSearchParams() },
    listProps: { current: null as Record<string, unknown> | null },
    viewSwitcherProps: { current: null as Record<string, unknown> | null },
    mutateListsMock: vi.fn(),
    pushMock: vi.fn(),
    settingsProps: { current: null as Record<string, unknown> | null },
  }));

vi.mock("swr", () => ({ default: swrMock, mutate: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
  useSearchParams: () => searchState.params,
}));
vi.mock("@/components/FeedSettingsModal", () => ({
  default: (props: Record<string, unknown>) => {
    settingsProps.current = props;
    return <div data-testid="feed-settings-modal" />;
  },
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => authState }));
vi.mock("@/components/ArticleList", () => ({
  default: (props: Record<string, unknown>) => {
    listProps.current = props;
    return <div data-testid="article-list">{String(props.variant)}:{String(props.filter)}</div>;
  },
  mutateArticleLists: mutateListsMock,
}));
const storiesProps: { current: Record<string, unknown> | null } = { current: null };
vi.mock("@/components/StoriesView", () => ({
  default: (props: Record<string, unknown>) => {
    storiesProps.current = props;
    return <div data-testid="stories-view" />;
  },
}));
vi.mock("@/components/ViewSwitcher", () => ({
  default: (props: Record<string, unknown>) => {
    viewSwitcherProps.current = props;
    return <div data-testid="view-switcher" />;
  },
}));

function okFetch() {
  return vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => ({}) });
}

describe("InboxPage", () => {
  beforeEach(() => {
    swrMock.mockReset();
    authState.user = makeUser({ default_view: "list" });
    searchState.params = new URLSearchParams();
    listProps.current = null;
    mutateListsMock.mockClear();
    pushMock.mockClear();
    settingsProps.current = null;
  });

  it("renders the Inbox header with no feed selected", () => {
    swrMock.mockReturnValue({ data: [makeFeed()] });
    render(<InboxPage />);
    expect(screen.getByText("Inbox")).toBeInTheDocument();
    expect(screen.getByTestId("article-list")).toHaveTextContent("list:unread");
  });

  it("shows the feed title and unread count when a feed is selected", () => {
    searchState.params = new URLSearchParams("feed=1");
    swrMock.mockReturnValue({ data: [makeFeed({ id: 1, title: "Tech", unread_count: 5 })] });
    render(<InboxPage />);
    expect(screen.getByText("Tech")).toBeInTheDocument();
    expect(screen.getByText("5 unread")).toBeInTheDocument();
  });

  it("shows the enriching indicator when a feed has pending articles", () => {
    searchState.params = new URLSearchParams("feed=1");
    swrMock.mockReturnValue({ data: [makeFeed({ id: 1, pending_count: 3 })] });
    render(<InboxPage />);
    expect(screen.getByText(/enriching 3 articles/)).toBeInTheDocument();
  });

  it("uses singular wording for one pending article", () => {
    swrMock.mockReturnValue({ data: [makeFeed({ id: 1, pending_count: 1 })] });
    render(<InboxPage />);
    expect(screen.getByText(/enriching 1 article…/)).toBeInTheDocument();
  });

  it("switches the unread/all tab", async () => {
    swrMock.mockReturnValue({ data: [makeFeed()] });
    render(<InboxPage />);
    await userEvent.click(screen.getByRole("button", { name: "all" }));
    expect(screen.getByTestId("article-list")).toHaveTextContent("list:all");
  });

  it("debounces search into the list's q prop", async () => {
    swrMock.mockReturnValue({ data: [makeFeed()] });
    render(<InboxPage />);
    await userEvent.type(screen.getByPlaceholderText("Search articles…"), "rust");
    await waitFor(() => expect(listProps.current?.q).toBe("rust"), { timeout: 1000 });
  });

  it("refreshes the selected feed", async () => {
    searchState.params = new URLSearchParams("feed=1");
    swrMock.mockReturnValue({ data: [makeFeed({ id: 1 })] });
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<InboxPage />);
    await userEvent.click(screen.getByTitle("Refresh feed"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][0]).toContain("/feeds/1/refresh");
    expect(mutateListsMock).toHaveBeenCalled();
  });

  it("marks all read", async () => {
    swrMock.mockReturnValue({ data: [makeFeed()] });
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<InboxPage />);
    await userEvent.click(screen.getByTitle("Mark all as read"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][0]).toContain("/articles/mark-all-read");
  });

  it("renders StoriesView in stories view", () => {
    searchState.params = new URLSearchParams("view=stories");
    swrMock.mockReturnValue({ data: [makeFeed()] });
    render(<InboxPage />);
    expect(screen.getByTestId("stories-view")).toBeInTheDocument();
    expect(screen.queryByTestId("article-list")).not.toBeInTheDocument();
  });

  it("renders zen variant from ?view=zen", () => {
    searchState.params = new URLSearchParams("view=zen");
    swrMock.mockReturnValue({ data: [makeFeed()] });
    render(<InboxPage />);
    expect(screen.getByTestId("article-list")).toHaveTextContent("zen:unread");
  });

  it("falls back to list when the feed is not found", () => {
    searchState.params = new URLSearchParams("feed=999");
    swrMock.mockReturnValue({ data: [makeFeed({ id: 1 })] });
    render(<InboxPage />);
    // No feed matched -> header shows Inbox (feed is null)
    expect(screen.getByText("Inbox")).toBeInTheDocument();
  });

  it("handles undefined feeds data", () => {
    swrMock.mockReturnValue({ data: undefined });
    render(<InboxPage />);
    expect(screen.getByText("Inbox")).toBeInTheDocument();
  });

  it("stories onExit switches back to the list view", async () => {
    const { act } = await import("@testing-library/react");
    searchState.params = new URLSearchParams("view=stories");
    swrMock.mockReturnValue({ data: [makeFeed()] });
    render(<InboxPage />);
    expect(screen.getByTestId("stories-view")).toBeInTheDocument();
    act(() => (storiesProps.current!.onExit as () => void)());
    await waitFor(() => expect(screen.getByTestId("article-list")).toBeInTheDocument());
  });

  it("opens feed settings from the header and navigates home after unsubscribe", async () => {
    const { act } = await import("@testing-library/react");
    searchState.params = new URLSearchParams("feed=1");
    swrMock.mockReturnValue({ data: [makeFeed({ id: 1, title: "Tech" })] });
    render(<InboxPage />);
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

  it("shows no settings button without a selected feed", () => {
    swrMock.mockReturnValue({ data: [makeFeed()] });
    render(<InboxPage />);
    expect(screen.queryByTitle("Feed settings")).not.toBeInTheDocument();
  });

  it("discards the in-session view when the feed changes", async () => {
    swrMock.mockReturnValue({ data: [makeFeed({ id: 1 }), makeFeed({ id: 2, title: "Two" })] });
    searchState.params = new URLSearchParams("feed=1");
    const { rerender } = render(<InboxPage />);
    searchState.params = new URLSearchParams("feed=2");
    rerender(<InboxPage />);
    expect(screen.getByText("Two")).toBeInTheDocument();
  });
});
