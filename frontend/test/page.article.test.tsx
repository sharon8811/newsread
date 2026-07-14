import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ArticlePage from "@/app/(app)/article/[id]/page";
import { makeArticleDetail } from "./fixtures";

const { swrMock, mutateMock, routerMock, mutateListsMock, paramsState } = vi.hoisted(() => ({
  swrMock: vi.fn(),
  mutateMock: vi.fn(),
  routerMock: { push: vi.fn(), back: vi.fn() },
  mutateListsMock: vi.fn(),
  paramsState: { id: "1" },
}));

vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: paramsState.id }),
  useRouter: () => routerMock,
}));
vi.mock("@/components/ArticleList", () => ({ mutateArticleLists: mutateListsMock }));
vi.mock("@/components/AiSummary", () => ({ default: () => <div data-testid="ai-summary" /> }));
vi.mock("@/components/EntityCard", () => ({ default: () => <div data-testid="entity-card" /> }));
vi.mock("@/components/QAPanel", () => ({ default: () => <div data-testid="qa-panel" /> }));
vi.mock("@/components/ArticleAssistantDrawer", () => ({
  default: ({
    onClose,
    onScopeChange,
    scope,
  }: {
    onClose: () => void;
    onScopeChange: (scope: "article" | "discussion") => void;
    scope: string;
  }) => (
    <div data-testid="assistant-drawer">
      <span>{scope}</span>
      <button onClick={() => onScopeChange("discussion")}>switch-discussion</button>
      <button onClick={onClose}>close-assistant</button>
    </div>
  ),
}));
vi.mock("@/components/RelatedArticles", () => ({
  default: () => <div data-testid="related-articles" />,
}));
vi.mock("@/components/ShareModal", () => ({
  default: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="share-modal">
      <button onClick={onClose}>close-modal</button>
    </div>
  ),
}));
vi.mock("@/components/ProjectPickerModal", () => ({
  default: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="project-picker">
      <button onClick={onClose}>close-picker</button>
    </div>
  ),
}));

function okFetch() {
  return vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => ({}) });
}

describe("ArticlePage", () => {
  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
    routerMock.push.mockClear();
    routerMock.back.mockClear();
    mutateListsMock.mockClear();
    paramsState.id = "1";
    vi.stubGlobal("fetch", okFetch());
  });

  it("shows the loading skeleton before data arrives", () => {
    swrMock.mockReturnValue({ data: undefined, error: undefined });
    const { container } = render(<ArticlePage />);
    expect(container.querySelector(".animate-pulse, [style]")).toBeTruthy();
    expect(screen.queryByText("A Great Article")).not.toBeInTheDocument();
  });

  it("opens article detail at the top of the app scroller", () => {
    swrMock.mockReturnValue({ data: makeArticleDetail({ is_read: true }), error: undefined });
    const { container, rerender } = render(
      <main style={{ overflow: "auto" }}>
        <ArticlePage />
      </main>,
    );
    const scroller = container.querySelector("main") as HTMLElement;
    scroller.scrollTop = 500;

    paramsState.id = "2";
    rerender(
      <main style={{ overflow: "auto" }}>
        <ArticlePage />
      </main>,
    );
    expect(scroller.scrollTop).toBe(0);
  });

  it("shows an error state and navigates home", async () => {
    swrMock.mockReturnValue({ data: undefined, error: new Error("nope") });
    render(<ArticlePage />);
    expect(screen.getByText("This article is out of reach.")).toBeInTheDocument();
    await userEvent.click(screen.getByText("Back to inbox"));
    expect(routerMock.push).toHaveBeenCalledWith("/");
  });

  it("renders the article and marks it read on view", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    swrMock.mockReturnValue({ data: makeArticleDetail({ is_read: false }), error: undefined });
    render(<ArticlePage />);
    expect(screen.getByText("A Great Article")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][0]).toContain("/articles/1/state");
    await waitFor(() => expect(mutateListsMock).toHaveBeenCalled());
  });

  it("renders the related-articles section stub", () => {
    swrMock.mockReturnValue({ data: makeArticleDetail({ is_read: true }), error: undefined });
    render(<ArticlePage />);
    expect(screen.getByTestId("related-articles")).toBeInTheDocument();
  });

  it("re-marks read after navigating to another article in place", async () => {
    // Related links change the id WITHOUT remounting the page — the
    // mark-read once-guard must re-arm (regression for the ref-reset effect).
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    swrMock.mockImplementation((key: unknown) =>
      key === `/articles/${paramsState.id}`
        ? {
            data: makeArticleDetail({
              id: Number(paramsState.id),
              is_read: false,
              title: `Article ${paramsState.id}`,
            }),
            error: undefined,
          }
        : { data: undefined, error: undefined },
    );
    const { rerender } = render(<ArticlePage />);
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/articles/1/state"))).toBe(true),
    );

    paramsState.id = "2";
    rerender(<ArticlePage />);
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/articles/2/state"))).toBe(true),
    );
  });

  it("does not re-mark an already-read article", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    swrMock.mockReturnValue({ data: makeArticleDetail({ is_read: true }), error: undefined });
    render(<ArticlePage />);
    await new Promise((r) => setTimeout(r, 50));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("toggles saved state", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    swrMock.mockReturnValue({ data: makeArticleDetail({ is_read: true, is_saved: false }), error: undefined });
    render(<ArticlePage />);
    await userEvent.click(screen.getByTitle("Save for later"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body).toEqual({ is_saved: true });
  });

  it("shows a discussion link when comments_url is present", () => {
    swrMock.mockReturnValue({
      data: makeArticleDetail({ is_read: true, comments_url: "https://news.example/c" }),
      error: undefined,
    });
    render(<ArticlePage />);
    expect(screen.getByText("Open discussion")).toBeInTheDocument();
  });

  it("opens and closes the share modal", async () => {
    swrMock.mockReturnValue({ data: makeArticleDetail({ is_read: true }), error: undefined });
    render(<ArticlePage />);
    await userEvent.click(screen.getByText("Share"));
    expect(screen.getByTestId("share-modal")).toBeInTheDocument();
    await userEvent.click(screen.getByText("close-modal"));
    expect(screen.queryByTestId("share-modal")).not.toBeInTheDocument();
  });

  it("opens and closes the project picker", async () => {
    swrMock.mockReturnValue({ data: makeArticleDetail({ is_read: true }), error: undefined });
    render(<ArticlePage />);
    await userEvent.click(screen.getByText("Add to project"));
    expect(screen.getByTestId("project-picker")).toBeInTheDocument();
    await userEvent.click(screen.getByText("close-picker"));
    expect(screen.queryByTestId("project-picker")).not.toBeInTheDocument();
  });

  it("opens and closes the single reading assistant", async () => {
    swrMock.mockReturnValue({ data: makeArticleDetail({ is_read: true }), error: undefined });
    render(<ArticlePage />);
    await userEvent.click(screen.getByText("Ask"));
    expect(screen.getByTestId("assistant-drawer")).toBeInTheDocument();
    await userEvent.click(screen.getByText("switch-discussion"));
    expect(screen.getByText("discussion")).toBeInTheDocument();
    await userEvent.click(screen.getByText("close-assistant"));
    expect(screen.queryByTestId("assistant-drawer")).not.toBeInTheDocument();
  });

  it("navigates back", async () => {
    swrMock.mockReturnValue({ data: makeArticleDetail({ is_read: true }), error: undefined });
    render(<ArticlePage />);
    const back = screen.getByText("← back");
    fireEvent.mouseEnter(back);
    expect(back).toHaveStyle({ color: "var(--ink)" });
    fireEvent.mouseLeave(back);
    expect(back).toHaveStyle({ color: "var(--ink-faint)" });
    await userEvent.click(back);
    expect(routerMock.back).toHaveBeenCalled();
  });

  it("renders a placeholder when there is no content_html", () => {
    swrMock.mockReturnValue({
      data: makeArticleDetail({ is_read: true, content_html: "" }),
      error: undefined,
    });
    render(<ArticlePage />);
    expect(screen.getByText(/only provides a headline/)).toBeInTheDocument();
  });

  it("renders content html when present", () => {
    swrMock.mockReturnValue({
      data: makeArticleDetail({ is_read: true, content_html: "<p>body text here</p>" }),
      error: undefined,
    });
    const { container } = render(<ArticlePage />);
    expect(container.querySelector(".reader")?.innerHTML).toContain("body text here");
  });

  it("shows Unsave for a saved article", () => {
    swrMock.mockReturnValue({ data: makeArticleDetail({ is_read: true, is_saved: true }), error: undefined });
    render(<ArticlePage />);
    expect(screen.getByTitle("Unsave")).toBeInTheDocument();
  });

  it("shows a generating placeholder while an illustration renders", () => {
    swrMock.mockReturnValue({
      data: makeArticleDetail({ is_read: true, image_url: null, image_pending: true }),
      error: undefined,
    });
    const { container } = render(<ArticlePage />);
    // Announced to screen readers as a live status, not just painted.
    expect(screen.getByRole("status")).toHaveTextContent(/generating illustration/);
    expect(container.querySelector(".shimmer")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it("renders the illustration once it is ready", () => {
    swrMock.mockReturnValue({
      data: makeArticleDetail({
        is_read: true,
        image_url: "/api/articles/1/generated-image",
      }),
      error: undefined,
    });
    const { container } = render(<ArticlePage />);
    const image = container.querySelector("img")!;
    expect(image).toBeInTheDocument();
    fireEvent.error(image);
    expect(image).toHaveStyle({ display: "none" });
    expect(screen.queryByText(/generating illustration/)).not.toBeInTheDocument();
    expect(container.querySelector(".shimmer")).toBeNull();
  });

  it("renders no illustration hero for an imageless article", () => {
    swrMock.mockReturnValue({
      data: makeArticleDetail({ is_read: true, image_url: null, image_pending: false }),
      error: undefined,
    });
    const { container } = render(<ArticlePage />);
    expect(screen.queryByText(/generating illustration/)).not.toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });
});
