import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SavedPage from "@/app/(app)/saved/page";
import { makeUser } from "./fixtures";

const { authState, searchState, listProps } = vi.hoisted(() => ({
  authState: { user: null as unknown },
  searchState: { params: new URLSearchParams() },
  listProps: { current: null as Record<string, unknown> | null },
}));

vi.mock("next/navigation", () => ({ useSearchParams: () => searchState.params }));
vi.mock("@/lib/auth", () => ({ useAuth: () => authState }));
vi.mock("@/components/ArticleList", () => ({
  default: (props: Record<string, unknown>) => {
    listProps.current = props;
    return <div data-testid="article-list">{String(props.variant)}:{String(props.filter)}</div>;
  },
  mutateArticleLists: vi.fn(),
}));
const storiesProps: { current: Record<string, unknown> | null } = { current: null };
vi.mock("@/components/StoriesView", () => ({
  default: (props: Record<string, unknown>) => {
    storiesProps.current = props;
    return <div data-testid="stories-view">{String(props.filter)}</div>;
  },
}));
vi.mock("@/components/ViewSwitcher", () => ({ default: () => <div data-testid="view-switcher" /> }));

describe("SavedPage", () => {
  beforeEach(() => {
    authState.user = makeUser({ default_view: "list" });
    searchState.params = new URLSearchParams();
    listProps.current = null;
  });

  it("renders the Saved header and the saved list", () => {
    render(<SavedPage />);
    expect(screen.getByText("Saved")).toBeInTheDocument();
    expect(screen.getByTestId("article-list")).toHaveTextContent("list:saved");
  });

  it("uses the zen variant when the default view is zen", () => {
    authState.user = makeUser({ default_view: "zen" });
    render(<SavedPage />);
    expect(screen.getByTestId("article-list")).toHaveTextContent("zen:saved");
  });

  it("honors the ?view=stories deep link", () => {
    searchState.params = new URLSearchParams("view=stories");
    render(<SavedPage />);
    expect(screen.getByTestId("stories-view")).toHaveTextContent("saved");
    expect(screen.queryByTestId("article-list")).not.toBeInTheDocument();
  });

  it("ignores an invalid ?view value", () => {
    searchState.params = new URLSearchParams("view=bogus");
    render(<SavedPage />);
    expect(screen.getByTestId("article-list")).toHaveTextContent("list:saved");
  });

  it("debounces search into the list q prop", async () => {
    render(<SavedPage />);
    await userEvent.type(screen.getByPlaceholderText("Search saved articles…"), "vim");
    await waitFor(() => expect(listProps.current?.q).toBe("vim"), { timeout: 1000 });
  });

  it("falls back to list when the user has no default view", () => {
    authState.user = null;
    render(<SavedPage />);
    expect(screen.getByTestId("article-list")).toHaveTextContent("list:saved");
  });

  it("stories onExit switches back to the list view", async () => {
    const { act } = await import("@testing-library/react");
    searchState.params = new URLSearchParams("view=stories");
    render(<SavedPage />);
    expect(screen.getByTestId("stories-view")).toBeInTheDocument();
    act(() => (storiesProps.current!.onExit as () => void)());
    await waitFor(() => expect(screen.getByTestId("article-list")).toBeInTheDocument());
  });
});
