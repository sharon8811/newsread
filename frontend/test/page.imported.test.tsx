import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ImportedPage from "@/app/(app)/imported/page";
import { makeUser } from "./fixtures";

const { authState, importFeedState, listProps, modalProps } = vi.hoisted(() => ({
  authState: { user: null as unknown },
  importFeedState: { data: undefined as { feed_id: number } | undefined },
  listProps: { current: null as Record<string, unknown> | null },
  modalProps: { current: null as Record<string, unknown> | null },
}));

vi.mock("@/lib/auth", () => ({ useAuth: () => authState }));
vi.mock("@/lib/queries", () => ({ useImportFeed: () => importFeedState }));
vi.mock("@/components/ArticleList", () => ({
  default: (props: Record<string, unknown>) => {
    listProps.current = props;
    return (
      <div data-testid="article-list">
        {String(props.variant)}:{String(props.filter)}:{String(props.feedId)}
      </div>
    );
  },
}));
vi.mock("@/components/ImportUrlModal", () => ({
  default: (props: Record<string, unknown>) => {
    modalProps.current = props;
    return <div data-testid="import-modal" />;
  },
}));

describe("ImportedPage", () => {
  beforeEach(() => {
    authState.user = makeUser({ default_view: "cards" });
    importFeedState.data = { feed_id: 9 };
    listProps.current = null;
    modalProps.current = null;
  });

  it("renders the header and scopes the list to the import feed", () => {
    render(<ImportedPage />);
    expect(screen.getByText("Imported")).toBeInTheDocument();
    expect(screen.getByTestId("article-list")).toHaveTextContent("cards:all:9");
  });

  it("waits for the import feed id before rendering the list", () => {
    importFeedState.data = undefined;
    render(<ImportedPage />);
    expect(screen.queryByTestId("article-list")).not.toBeInTheDocument();
  });

  it("uses the list variant for a list default view", () => {
    authState.user = makeUser({ default_view: "list" });
    render(<ImportedPage />);
    expect(screen.getByTestId("article-list")).toHaveTextContent("list:all:9");
  });

  it("maps a stories default view to cards", () => {
    authState.user = makeUser({ default_view: "stories" });
    render(<ImportedPage />);
    expect(screen.getByTestId("article-list")).toHaveTextContent("cards:all:9");
  });

  it("opens and closes the add-link modal", async () => {
    render(<ImportedPage />);
    expect(screen.queryByTestId("import-modal")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Add link/ }));
    expect(screen.getByTestId("import-modal")).toBeInTheDocument();
    (modalProps.current!.onClose as () => void)();
    await waitFor(() =>
      expect(screen.queryByTestId("import-modal")).not.toBeInTheDocument(),
    );
  });

  it("debounces search into the list q prop and swaps the empty copy", async () => {
    render(<ImportedPage />);
    expect(listProps.current?.emptyTitle).toBe("Nothing imported yet.");
    await userEvent.type(
      screen.getByPlaceholderText("Search imported articles…"),
      "rust",
    );
    await waitFor(() => expect(listProps.current?.q).toBe("rust"), { timeout: 1000 });
    expect(listProps.current?.emptyTitle).toBe("Nothing matches your search.");
    expect(listProps.current?.emptySubtitle).toBeUndefined();
  });
});
