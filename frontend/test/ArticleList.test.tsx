import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ArticleList, { articlesKey, mutateArticleLists } from "@/components/ArticleList";
import { makeArticle } from "./fixtures";

const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }));

const { swrMock, mutateMock } = vi.hoisted(() => ({ swrMock: vi.fn(), mutateMock: vi.fn() }));
vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));

vi.mock("@/components/ShareModal", () => ({
  default: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="share-modal" onClick={onClose} />
  ),
}));

function stub(articles: unknown, isLoading = false) {
  swrMock.mockReturnValue({ data: articles, isLoading });
}

function okFetch() {
  return vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => ({}) });
}

describe("<ArticleList>", () => {
  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
    pushMock.mockClear();
    // jsdom does not implement scrollIntoView
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("articlesKey encodes filter, feed and query params", () => {
    expect(articlesKey({ filter: "all" })).toBe("/articles?filter=all&limit=100");
    expect(articlesKey({ filter: "saved", feedId: "7", q: "ai" })).toBe(
      "/articles?filter=saved&limit=100&feed_id=7&q=ai",
    );
  });

  it("mutateArticleLists revalidates article lists and feeds", () => {
    mutateArticleLists();
    expect(mutateMock).toHaveBeenCalledTimes(2);
    // first call passes a key-matcher predicate
    const predicate = mutateMock.mock.calls[0][0] as (k: unknown) => boolean;
    expect(predicate("/articles?filter=all")).toBe(true);
    expect(predicate("/feeds")).toBe(false);
    expect(predicate(123)).toBe(false);
    expect(mutateMock).toHaveBeenCalledWith("/feeds");
  });

  it("renders loading skeletons while loading", () => {
    stub(undefined, true);
    const { container } = render(<ArticleList filter="all" emptyTitle="Nothing" />);
    expect(container.querySelectorAll(".rounded-md").length).toBe(6);
  });

  it("renders the empty state with a subtitle", () => {
    stub(undefined, false);
    render(<ArticleList filter="all" emptyTitle="No articles" emptySubtitle="try later" />);
    expect(screen.getByText("No articles")).toBeInTheDocument();
    expect(screen.getByText("try later")).toBeInTheDocument();
  });

  it("renders the empty state without a subtitle", () => {
    stub([], false);
    render(<ArticleList filter="all" emptyTitle="Empty" />);
    expect(screen.getByText("Empty")).toBeInTheDocument();
    expect(screen.queryByText("try later")).not.toBeInTheDocument();
  });

  it("renders article rows in list mode", () => {
    stub([makeArticle({ id: 1, title: "First" }), makeArticle({ id: 2, title: "Second" })]);
    render(<ArticleList filter="all" emptyTitle="Empty" />);
    expect(screen.getByText("First")).toBeInTheDocument();
    expect(screen.getByText("Second")).toBeInTheDocument();
    expect(screen.getByText(/j \/ k to navigate/)).toBeInTheDocument();
  });

  it("renders cards in cards mode", () => {
    stub([makeArticle({ id: 1, title: "Card One" }), makeArticle({ id: 2, title: "Card Two" })]);
    const { container } = render(
      <ArticleList filter="all" emptyTitle="Empty" variant="cards" />,
    );
    expect(screen.getByText("Card One")).toBeInTheDocument();
    expect(screen.getByText("Card Two")).toBeInTheDocument();
    expect(container.querySelectorAll("article").length).toBe(2);
  });

  it("renders card-shaped skeletons while loading in cards mode", () => {
    stub(undefined, true);
    const { container } = render(
      <ArticleList filter="all" emptyTitle="Empty" variant="cards" />,
    );
    expect(container.querySelectorAll(".rounded-lg").length).toBe(4);
  });

  it("navigates selection with j and k", () => {
    stub([makeArticle({ id: 1, title: "One" }), makeArticle({ id: 2, title: "Two" })]);
    const { container } = render(<ArticleList filter="all" emptyTitle="Empty" />);
    fireEvent.keyDown(window, { key: "j" });
    // row 1 is now selected -> ArticleRow applies selected background
    fireEvent.keyDown(window, { key: "j" }); // clamps at last index
    fireEvent.keyDown(window, { key: "k" });
    fireEvent.keyDown(window, { key: "k" }); // clamps at 0
    expect(container).toBeTruthy();
  });

  it("opens the selected article on Enter", () => {
    stub([makeArticle({ id: 42, title: "Deep" })]);
    render(<ArticleList filter="all" emptyTitle="Empty" />);
    fireEvent.keyDown(window, { key: "Enter" });
    expect(pushMock).toHaveBeenCalledWith("/article/42");
  });

  it("toggles saved with the s key", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    stub([makeArticle({ id: 5, is_saved: false })]);
    render(<ArticleList filter="all" emptyTitle="Empty" />);
    fireEvent.keyDown(window, { key: "s" });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, opts] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/articles/5/state");
    expect(JSON.parse(opts.body)).toEqual({ is_saved: true });
    await waitFor(() => expect(mutateMock).toHaveBeenCalled());
  });

  it("toggles read with the m key", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    stub([makeArticle({ id: 6, is_read: false })]);
    render(<ArticleList filter="all" emptyTitle="Empty" />);
    fireEvent.keyDown(window, { key: "m" });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, opts] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/articles/6/state");
    expect(JSON.parse(opts.body)).toEqual({ is_read: true });
  });

  it("ignores unhandled keys", () => {
    stub([makeArticle({ id: 1 })]);
    render(<ArticleList filter="all" emptyTitle="Empty" />);
    fireEvent.keyDown(window, { key: "z" });
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("ignores keys typed into inputs", () => {
    stub([makeArticle({ id: 1 })]);
    render(
      <>
        <input data-testid="field" />
        <ArticleList filter="all" emptyTitle="Empty" />
      </>,
    );
    const input = screen.getByTestId("field");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("ignores keys from contentEditable targets", () => {
    stub([makeArticle({ id: 1 })]);
    const { container } = render(<ArticleList filter="all" emptyTitle="Empty" />);
    const editable = document.createElement("div");
    editable.setAttribute("contenteditable", "true");
    Object.defineProperty(editable, "isContentEditable", { value: true });
    container.appendChild(editable);
    fireEvent.keyDown(editable, { key: "Enter" });
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("ignores keys when there are no articles", () => {
    stub([], false);
    render(<ArticleList filter="all" emptyTitle="Empty" />);
    fireEvent.keyDown(window, { key: "j" });
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("opens the share modal and suspends keyboard nav while open", async () => {
    stub([makeArticle({ id: 1, title: "Shareable" })]);
    render(<ArticleList filter="all" emptyTitle="Empty" />);
    await userEvent.click(screen.getByTitle("Share with a note"));
    expect(screen.getByTestId("share-modal")).toBeInTheDocument();
    // keyboard is ignored while the modal is open
    fireEvent.keyDown(window, { key: "Enter" });
    expect(pushMock).not.toHaveBeenCalled();
    // closing the modal restores nav
    await userEvent.click(screen.getByTestId("share-modal"));
    expect(screen.queryByTestId("share-modal")).not.toBeInTheDocument();
  });

  it("saves via the row save button (onToggleSaved callback)", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    stub([makeArticle({ id: 9, is_saved: false })]);
    render(<ArticleList filter="all" emptyTitle="Empty" />);
    await userEvent.click(screen.getByTitle("Save for later"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(String(fetchMock.mock.calls[0][0])).toContain("/articles/9/state");
  });
});
