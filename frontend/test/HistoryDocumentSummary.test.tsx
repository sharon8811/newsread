import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import HistoryDocumentSummary from "@/components/HistoryDocumentSummary";

const { swrMock, localMutateMock } = vi.hoisted(() => ({
  swrMock: vi.fn(),
  localMutateMock: vi.fn(),
}));

vi.mock("swr", () => ({ default: swrMock, mutate: vi.fn() }));

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
  });

  it("previews a citation before exposing its highlighted source link", async () => {
    swrMock.mockReturnValue({
      data: {
        state: "ready",
        markdown: "A supported statement [1].",
        citations: [
          {
            label: 1,
            block_id: "paragraph-1",
            quote: "A precise source passage.",
            prefix: "Before",
            suffix: "After",
            source_document_id: 12,
            source_page_id: 4,
            url: "https://example.com/article#section",
          },
        ],
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
  });
});
