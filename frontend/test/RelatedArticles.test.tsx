import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RelatedArticles from "@/components/RelatedArticles";
import { makeArticleDetail, makeRelatedArticle, makeSynthesis } from "./fixtures";
import type { CoverageSynthesis, RelatedArticle } from "@/lib/api";

const { swrMock, routerMock } = vi.hoisted(() => ({
  swrMock: vi.fn(),
  routerMock: { push: vi.fn() },
}));
vi.mock("swr", () => ({ default: swrMock }));
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }));

function stubSWR(related: RelatedArticle[] | undefined, configured = true) {
  swrMock.mockImplementation((key: unknown) =>
    key === "/ai/status" ? { data: { configured } } : { data: related },
  );
}

function jsonFetch(body: CoverageSynthesis) {
  return vi.fn().mockResolvedValue({
    status: 200,
    ok: true,
    headers: { get: () => "application/json" },
    json: async () => body,
  });
}

describe("<RelatedArticles>", () => {
  beforeEach(() => {
    swrMock.mockReset();
    routerMock.push.mockClear();
  });

  it("renders nothing while loading or when empty", () => {
    stubSWR(undefined);
    const { container, rerender } = render(
      <RelatedArticles article={makeArticleDetail()} />,
    );
    expect(container.firstChild).toBeNull();
    stubSWR([]);
    rerender(<RelatedArticles article={makeArticleDetail()} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders rows with tier tag, unread dot and meta", () => {
    stubSWR([
      makeRelatedArticle({ tier: "same_story" }),
      makeRelatedArticle({ id: 8, title: "Topical", is_read: true }),
    ]);
    const { container } = render(<RelatedArticles article={makeArticleDetail()} />);
    expect(screen.getByText("Related coverage")).toBeInTheDocument();
    expect(screen.getByText("SAME STORY")).toBeInTheDocument();
    expect(screen.getByText("Topical")).toBeInTheDocument();
    expect(screen.getAllByText(/Other Feed/).length).toBe(2);
    // one unread dot: the second row is read
    expect(container.querySelectorAll(".dot-unread").length).toBe(1);
  });

  it("navigates on row click", async () => {
    stubSWR([makeRelatedArticle({ id: 42 })]);
    render(<RelatedArticles article={makeArticleDetail()} />);
    await userEvent.click(screen.getByText("Related headline"));
    expect(routerMock.push).toHaveBeenCalledWith("/article/42");
  });

  it("hides the synthesize button when AI is unconfigured", () => {
    stubSWR([makeRelatedArticle()], false);
    render(<RelatedArticles article={makeArticleDetail()} />);
    expect(screen.queryByText("Synthesize coverage")).not.toBeInTheDocument();
    expect(screen.getByText("Related headline")).toBeInTheDocument();
  });

  it("synthesizes on click and renders overview, timeline and perspectives", async () => {
    stubSWR([makeRelatedArticle()]);
    const fetchMock = jsonFetch(makeSynthesis());
    vi.stubGlobal("fetch", fetchMock);
    render(<RelatedArticles article={makeArticleDetail()} />);

    await userEvent.click(screen.getByText("Synthesize coverage"));
    expect(await screen.findByText(/The overall picture/)).toBeInTheDocument();
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/articles/1/related-synthesis");
    expect(init.method).toBe("POST");
    expect(screen.getByText("Timeline")).toBeInTheDocument();
    expect(screen.getByText("May 1")).toBeInTheDocument();
    expect(screen.getByText("it started [1]")).toBeInTheDocument();
    expect(screen.getByText("Perspectives")).toBeInTheDocument();
    expect(screen.getByText(/frames it differently/)).toBeInTheDocument();
    expect(screen.getByText(/\[2\] Related headline/)).toBeInTheDocument();
    // Button hides once a synthesis is shown.
    expect(screen.queryByText("Synthesize coverage")).not.toBeInTheDocument();
  });

  it("falls back to raw timeline markdown when unparsed", async () => {
    stubSWR([makeRelatedArticle()]);
    vi.stubGlobal(
      "fetch",
      jsonFetch(makeSynthesis({ timeline: null, timeline_raw: "things unfolded slowly" })),
    );
    render(<RelatedArticles article={makeArticleDetail()} />);
    await userEvent.click(screen.getByText("Synthesize coverage"));
    expect(await screen.findByText("things unfolded slowly")).toBeInTheDocument();
  });

  it("omits timeline and perspectives blocks when absent", async () => {
    stubSWR([makeRelatedArticle()]);
    vi.stubGlobal(
      "fetch",
      jsonFetch(makeSynthesis({ timeline: null, timeline_raw: null, perspectives: null })),
    );
    render(<RelatedArticles article={makeArticleDetail()} />);
    await userEvent.click(screen.getByText("Synthesize coverage"));
    await screen.findByText(/The overall picture/);
    expect(screen.queryByText("Timeline")).not.toBeInTheDocument();
    expect(screen.queryByText("Perspectives")).not.toBeInTheDocument();
  });

  it("shows the busy skeleton while the LLM call is in flight", async () => {
    stubSWR([makeRelatedArticle()]);
    let resolve!: (v: unknown) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(new Promise((r) => (resolve = r))),
    );
    render(<RelatedArticles article={makeArticleDetail()} />);
    await userEvent.click(screen.getByText("Synthesize coverage"));
    expect(screen.getByText("Reading the coverage…")).toBeInTheDocument();
    resolve({
      status: 200,
      ok: true,
      headers: { get: () => "application/json" },
      json: async () => makeSynthesis(),
    });
    await screen.findByText(/The overall picture/);
  });

  it("shows an error with retry when the call fails", async () => {
    stubSWR([makeRelatedArticle()]);
    const fetchMock = vi.fn().mockResolvedValue({
      status: 502,
      ok: false,
      headers: { get: () => "application/json" },
      json: async () => ({ detail: "The LLM request failed" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<RelatedArticles article={makeArticleDetail()} />);
    await userEvent.click(screen.getByText("Synthesize coverage"));
    expect(await screen.findByText("The LLM request failed")).toBeInTheDocument();
    await userEvent.click(screen.getByText("Try again"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });
});
