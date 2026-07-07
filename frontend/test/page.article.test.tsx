import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ArticlePage from "@/app/(app)/article/[id]/page";
import { makeArticleDetail } from "./fixtures";

const { swrMock, mutateMock, routerMock, mutateListsMock } = vi.hoisted(() => ({
  swrMock: vi.fn(),
  mutateMock: vi.fn(),
  routerMock: { push: vi.fn(), back: vi.fn() },
  mutateListsMock: vi.fn(),
}));

vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "1" }),
  useRouter: () => routerMock,
}));
vi.mock("@/components/ArticleList", () => ({ mutateArticleLists: mutateListsMock }));
vi.mock("@/components/AiSummary", () => ({ default: () => <div data-testid="ai-summary" /> }));
vi.mock("@/components/EntityCard", () => ({ default: () => <div data-testid="entity-card" /> }));
vi.mock("@/components/QAPanel", () => ({ default: () => <div data-testid="qa-panel" /> }));
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
    vi.stubGlobal("fetch", okFetch());
  });

  it("shows the loading skeleton before data arrives", () => {
    swrMock.mockReturnValue({ data: undefined, error: undefined });
    const { container } = render(<ArticlePage />);
    expect(container.querySelector(".animate-pulse, [style]")).toBeTruthy();
    expect(screen.queryByText("A Great Article")).not.toBeInTheDocument();
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
    expect(screen.getByText("Discussion")).toBeInTheDocument();
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
    await userEvent.click(screen.getByText("Project"));
    expect(screen.getByTestId("project-picker")).toBeInTheDocument();
    await userEvent.click(screen.getByText("close-picker"));
    expect(screen.queryByTestId("project-picker")).not.toBeInTheDocument();
  });

  it("navigates back", async () => {
    swrMock.mockReturnValue({ data: makeArticleDetail({ is_read: true }), error: undefined });
    render(<ArticlePage />);
    await userEvent.click(screen.getByText("← back"));
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
});
