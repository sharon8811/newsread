import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import HistoryDocumentSummary from "@/components/HistoryDocumentSummary";

const { swrMock, localMutateMock } = vi.hoisted(() => ({
  swrMock: vi.fn(),
  localMutateMock: vi.fn(),
}));

vi.mock("swr", () => ({ default: swrMock, mutate: vi.fn() }));

const citation = {
  label: 1,
  block_id: "paragraph-1",
  quote: "A precise source passage.",
  prefix: "Before",
  suffix: "After",
  source_document_id: 12,
  source_page_id: 4,
  url: "https://example.com/article#section",
};

describe("HistoryDocumentSummary", () => {
  beforeEach(() => {
    swrMock.mockReturnValue({
      data: { state: "not_requested", citations: [] },
      mutate: localMutateMock,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ state: "queued", citations: [] }),
      }),
    );
  });

  it("does not request a summary before the explicit click", async () => {
    render(<HistoryDocumentSummary documentId={12} />);

    expect(fetch).not.toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole("button", { name: "Summarize saved page" }),
    );
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/history/documents/12/summarize"),
      expect.objectContaining({ method: "POST" }),
    );
    expect(localMutateMock).toHaveBeenCalledWith(
      { state: "queued", citations: [] },
      { revalidate: false },
    );
  });

  it("previews a citation before exposing its highlighted source link", async () => {
    swrMock.mockReturnValue({
      data: {
        state: "ready",
        markdown: "A supported statement [1].",
        citations: [citation],
      },
      mutate: localMutateMock,
    });
    render(<HistoryDocumentSummary documentId={12} />);

    expect(
      screen.queryByRole("link", { name: "Open highlighted source" }),
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "[1]" }));

    expect(
      within(screen.getByLabelText("Citation 1")).getByText(
        "“A precise source passage.”",
      ),
    ).toBeInTheDocument();
    const link = screen.getByRole("link", {
      name: "Open highlighted source",
    });
    expect(link).toHaveAttribute(
      "href",
      "https://example.com/article#section:~:text=Before-,A%20precise%20source%20passage.,-After",
    );
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link.getAttribute("data-newsread-citation")).toContain(
      '"quote":"A precise source passage."',
    );

    await userEvent.click(
      screen.getByRole("button", { name: "Close citation preview" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /A precise source passage/ }),
    );
    expect(screen.getByLabelText("Citation 1")).toBeInTheDocument();
  });

  it("renders ordinary markdown links and leaves unmatched citation labels inert", () => {
    swrMock.mockReturnValue({
      data: {
        state: "ready",
        markdown: "Read [the source](https://example.org) and missing [9].",
        citations: [],
      },
      mutate: localMutateMock,
    });
    render(<HistoryDocumentSummary documentId={12} />);

    expect(screen.getByRole("link", { name: "the source" })).toHaveAttribute(
      "href",
      "https://example.org",
    );
    expect(screen.queryByRole("button", { name: "[9]" })).not.toBeInTheDocument();
  });

  it("regenerates a ready summary with the force flag and displays its model", async () => {
    swrMock.mockReturnValue({
      data: {
        state: "ready",
        markdown: "Existing summary.",
        model: "gpt-test",
        citations: [],
      },
      mutate: localMutateMock,
    });
    render(<HistoryDocumentSummary documentId={12} />);

    expect(screen.getByText("gpt-test")).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Regenerate summary" }),
    );
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/history/documents/12/summarize?force=true"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it.each(["queued", "running"])(
    "shows progress while the summary is %s",
    (state) => {
      swrMock.mockReturnValue({
        data: { state, citations: [] },
        mutate: localMutateMock,
      });
      const { container } = render(<HistoryDocumentSummary documentId={12} />);

      expect(
        screen.getByText("Summarizing the saved version…"),
      ).toBeInTheDocument();
      expect(container.querySelectorAll(".animate-pulse")).toHaveLength(3);
    },
  );

  it("shows progress immediately while an explicit request is pending", async () => {
    let resolveRequest: ((value: unknown) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise((resolve) => {
            resolveRequest = resolve;
          }),
      ),
    );
    render(<HistoryDocumentSummary documentId={12} />);

    await userEvent.click(
      screen.getByRole("button", { name: "Summarize saved page" }),
    );
    expect(
      screen.getByText("Summarizing the saved version…"),
    ).toBeInTheDocument();

    resolveRequest?.({
      ok: true,
      status: 200,
      json: async () => ({ state: "queued", citations: [] }),
    });
    await waitFor(() => expect(localMutateMock).toHaveBeenCalled());
  });

  it("explains when captured content is too short to summarize", () => {
    swrMock.mockReturnValue({
      data: { state: "too_short", citations: [] },
      mutate: localMutateMock,
    });
    render(<HistoryDocumentSummary documentId={12} />);

    expect(
      screen.getByText(/already short, so it does not need a summary/),
    ).toBeInTheDocument();
  });

  it("renders the retry state for a load failure", async () => {
    swrMock.mockReturnValue({
      data: undefined,
      error: new Error("load failed"),
      mutate: localMutateMock,
    });
    render(<HistoryDocumentSummary documentId={12} />);

    expect(
      screen.getByText("Could not generate the saved-page summary."),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/history/documents/12/summarize"),
      expect.any(Object),
    );
  });

  it.each([
    ["invalid_model_output", "The model returned an invalid cited summary."],
    ["provider_error", "Could not generate the saved-page summary."],
  ])("renders a useful stored error for %s", (errorCode, message) => {
    swrMock.mockReturnValue({
      data: { state: "error", error_code: errorCode, citations: [] },
      mutate: localMutateMock,
    });
    render(<HistoryDocumentSummary documentId={12} />);

    expect(screen.getByText(message)).toBeInTheDocument();
  });

  it.each([
    [new Error("Provider unavailable"), "Provider unavailable"],
    ["unknown failure", "Could not generate the summary"],
  ])("renders an explicit request failure", async (failure, message) => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(failure));
    render(<HistoryDocumentSummary documentId={12} />);

    await userEvent.click(
      screen.getByRole("button", { name: "Summarize saved page" }),
    );
    expect(await screen.findByText(message)).toBeInTheDocument();
  });

  it("shows a citation without an open action and lets the user close it", async () => {
    swrMock.mockReturnValue({
      data: {
        state: "ready",
        markdown: "A supported statement [1].",
        citations: [{ ...citation, url: null }],
      },
      mutate: localMutateMock,
    });
    render(<HistoryDocumentSummary documentId={12} />);

    await userEvent.click(screen.getByRole("button", { name: "[1]" }));
    expect(
      screen.getByText(
        "This saved version no longer has an active page location.",
      ),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Close citation preview" }),
    );
    expect(screen.queryByLabelText("Citation 1")).not.toBeInTheDocument();
  });

  it("keeps the lazy prompt when a ready response has no markdown", () => {
    swrMock.mockReturnValue({
      data: { state: "ready", markdown: "", citations: undefined },
      mutate: localMutateMock,
    });
    render(<HistoryDocumentSummary documentId={12} />);

    expect(
      screen.getByRole("button", { name: "Summarize saved page" }),
    ).toBeInTheDocument();
  });

  it("renders ready markdown when the server omits the citations array", () => {
    swrMock.mockReturnValue({
      data: { state: "ready", markdown: "A summary without citations." },
      mutate: localMutateMock,
    });
    render(<HistoryDocumentSummary documentId={12} />);

    expect(screen.getByText("A summary without citations.")).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("clears a selected preview when refreshed citations no longer contain it", async () => {
    let summary = {
      state: "ready",
      markdown: "A supported statement [1].",
      citations: [citation],
    };
    swrMock.mockImplementation(() => ({
      data: summary,
      mutate: localMutateMock,
    }));
    const { rerender } = render(<HistoryDocumentSummary documentId={12} />);

    await userEvent.click(screen.getByRole("button", { name: "[1]" }));
    expect(screen.getByLabelText("Citation 1")).toBeInTheDocument();

    summary = {
      state: "ready",
      markdown: "The refreshed summary has no citations.",
      citations: [],
    };
    rerender(<HistoryDocumentSummary documentId={12} />);
    expect(screen.queryByLabelText("Citation 1")).not.toBeInTheDocument();
  });
});
