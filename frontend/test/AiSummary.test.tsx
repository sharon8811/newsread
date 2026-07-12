import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AiSummary from "@/components/AiSummary";
import { makeArticleDetail } from "./fixtures";

const { swrMock, mutateMock } = vi.hoisted(() => ({ swrMock: vi.fn(), mutateMock: vi.fn() }));
vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));

function stubStatus(configured: boolean) {
  swrMock.mockReturnValue({ data: { configured, model: "m", search: false, search_provider: null } });
}

function okFetch() {
  return vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => ({}) });
}

describe("<AiSummary>", () => {
  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
  });

  it("renders nothing when AI is not configured", () => {
    stubStatus(false);
    const { container } = render(<AiSummary article={makeArticleDetail()} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows an existing summary", () => {
    stubStatus(true);
    vi.stubGlobal("fetch", okFetch());
    render(<AiSummary article={makeArticleDetail({ summary: "the summary text", summary_model: "gpt" })} />);
    expect(screen.getByText("the summary text")).toBeInTheDocument();
    expect(screen.getByText("AI Summary")).toBeInTheDocument();
  });

  it("renders markdown lists, converting legacy '•' bullets too", () => {
    stubStatus(true);
    vi.stubGlobal("fetch", okFetch());
    render(
      <AiSummary
        article={makeArticleDetail({
          summary: "Core takeaway.\n\n- **First** point\n• legacy point",
        })}
      />,
    );
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("First point");
    expect(items[1]).toHaveTextContent("legacy point");
    expect(screen.getByText("First").tagName).toBe("STRONG");
  });

  it("renders markdown tables", () => {
    stubStatus(true);
    vi.stubGlobal("fetch", okFetch());
    render(
      <AiSummary
        article={makeArticleDetail({
          summary:
            "Intro.\n\n| State | Definition |\n| --- | --- |\n| Virginia | monetary only |",
        })}
      />,
    );
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("Virginia")).toBeInTheDocument();
  });

  it("auto-generates when there is no summary yet", async () => {
    stubStatus(true);
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<AiSummary article={makeArticleDetail({ summary: "" })} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][0]).toContain("/articles/1/summarize");
    await waitFor(() => expect(mutateMock).toHaveBeenCalledWith("/articles/1"));
  });

  it("regenerates on the refresh button (force=true)", async () => {
    stubStatus(true);
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<AiSummary article={makeArticleDetail({ summary: "old summary" })} />);
    await userEvent.click(screen.getByTitle("Regenerate summary"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("force=true"))).toBe(true);
  });

  it("shows an error and retries", async () => {
    stubStatus(true);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ status: 502, ok: false, json: async () => ({ detail: "LLM failed" }) })
      .mockResolvedValue({ status: 200, ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    render(<AiSummary article={makeArticleDetail({ summary: "" })} />);
    await waitFor(() => expect(screen.getByText("LLM failed")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Try again"));
    await waitFor(() => expect(mutateMock).toHaveBeenCalled());
  });
});
