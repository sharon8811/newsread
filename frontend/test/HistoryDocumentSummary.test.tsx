import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
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
});
