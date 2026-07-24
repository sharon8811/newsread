import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import HistoryDocumentPage from "@/app/(app)/history/documents/[id]/page";

const { swrMock, routerMock } = vi.hoisted(() => ({
  swrMock: vi.fn(),
  routerMock: { back: vi.fn() },
}));

vi.mock("swr", () => ({ default: swrMock, mutate: vi.fn() }));
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "12" }),
  useRouter: () => routerMock,
}));
vi.mock("@/components/HistoryDocumentSummary", () => ({
  default: () => <div data-testid="history-summary" />,
}));
vi.mock("@/components/PrivateHistoryImage", () => ({
  default: () => <div data-testid="private-image" />,
}));
vi.mock("@/components/QAPanel", () => ({
  default: ({ heading }: { heading: string }) => (
    <div data-testid="qa-panel">{heading}</div>
  ),
}));

const detail = {
  document_id: 12,
  text_excerpt: "Saved body",
  character_count: 400,
  extraction_version: "history-dom-v2",
  lead_image_id: 4,
  embedding_state: "ready",
  summary_state: "not_requested",
  locations: [
    {
      page_id: 8,
      url: "https://example.com/saved",
      title: "A saved page",
      hostname: "example.com",
      first_seen_at: "2026-07-20T09:00:00Z",
      last_seen_at: "2026-07-24T09:00:00Z",
      visit_count: 3,
      source_browsers: ["Chrome"],
      favicon_image_id: 5,
    },
  ],
  other_versions: [],
};

const content = {
  document_id: 12,
  content_type: "article",
  language: "en",
  blocks: [
    { id: "b0001", kind: "heading", text: "Captured heading" },
    { id: "b0002", kind: "paragraph", text: "Captured paragraph." },
  ],
};

describe("HistoryDocumentPage", () => {
  beforeEach(() => {
    swrMock.mockImplementation((key: string) => {
      if (key === "/history/documents/12") return { data: detail };
      if (key === "/history/documents/12/content") return { data: content };
      return { data: undefined };
    });
  });

  it("renders the immutable captured version and current-page link", () => {
    render(<HistoryDocumentPage />);

    expect(screen.getByRole("heading", { name: "A saved page" })).toBeInTheDocument();
    expect(screen.getByText("Captured heading")).toBeInTheDocument();
    expect(screen.getByText("Captured paragraph.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open current page" })).toHaveAttribute(
      "href",
      "https://example.com/saved",
    );
    expect(screen.getByTestId("history-summary")).toBeInTheDocument();
  });

  it("does not mount Q&A until the user explicitly enables it", async () => {
    render(<HistoryDocumentPage />);

    expect(screen.queryByTestId("qa-panel")).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Ask about this saved page" }),
    );
    expect(screen.getByTestId("qa-panel")).toHaveTextContent("Saved page Q&A");
  });
});
