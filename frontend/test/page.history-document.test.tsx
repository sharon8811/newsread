import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import HistoryDocumentPage from "@/app/(app)/history/documents/[id]/page";
import { resetBackNav, trackBackNav } from "@/lib/backNav";

const { swrMock, routerMock } = vi.hoisted(() => ({
  swrMock: vi.fn(),
  routerMock: { back: vi.fn(), push: vi.fn() },
}));
const { navigationState } = vi.hoisted(() => ({
  navigationState: { id: "12" },
}));

vi.mock("swr", () => ({ default: swrMock, mutate: vi.fn() }));
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: navigationState.id }),
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

let documentResult: {
  data?: typeof detail;
  error?: Error;
};
let contentResult: {
  data?: typeof content;
  error?: Error;
};

describe("HistoryDocumentPage", () => {
  beforeEach(() => {
    resetBackNav();
    navigationState.id = "12";
    documentResult = { data: detail };
    contentResult = { data: content };
    swrMock.mockImplementation((key: string) => {
      if (key === "/history/documents/12") return documentResult;
      if (key === "/history/documents/12/content") return contentResult;
      return { data: undefined };
    });
  });

  it("renders the immutable captured version and current-page link", async () => {
    render(<HistoryDocumentPage />);

    expect(screen.getByRole("heading", { name: "A saved page" })).toBeInTheDocument();
    expect(screen.getByText("Captured heading")).toBeInTheDocument();
    expect(screen.getByText("Captured paragraph.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open current page" })).toHaveAttribute(
      "href",
      "https://example.com/saved",
    );
    expect(screen.getByTestId("history-summary")).toBeInTheDocument();
    expect(screen.getAllByTestId("private-image")).toHaveLength(2);

    trackBackNav("/history", "");
    trackBackNav("/history/documents/12", "");
    await userEvent.click(screen.getByRole("button", { name: "← back" }));
    expect(routerMock.back).toHaveBeenCalled();
  });

  it("falls back to the history list when there is no usable history", async () => {
    render(<HistoryDocumentPage />);

    await userEvent.click(screen.getByRole("button", { name: "← back" }));
    expect(routerMock.back).not.toHaveBeenCalled();
    expect(routerMock.push).toHaveBeenCalledWith("/history");
  });

  it("does not mount Q&A until the user explicitly enables it", async () => {
    render(<HistoryDocumentPage />);

    expect(screen.queryByTestId("qa-panel")).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Ask about this saved page" }),
    );
    expect(screen.getByTestId("qa-panel")).toHaveTextContent("Saved page Q&A");
  });

  it.each(["bad-id", "0", "-1"])(
    "rejects an invalid document id without requesting content: %s",
    (id) => {
      navigationState.id = id;
      render(<HistoryDocumentPage />);

      expect(
        screen.getByText("This saved page is no longer available."),
      ).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Back to history" })).toHaveAttribute(
        "href",
        "/history",
      );
      expect(swrMock).toHaveBeenCalledWith(
        null,
        expect.any(Function),
      );
    },
  );

  it("renders the unavailable state when document metadata fails", () => {
    documentResult = { error: new Error("gone") };
    render(<HistoryDocumentPage />);

    expect(
      screen.getByText("This saved page is no longer available."),
    ).toBeInTheDocument();
  });

  it.each([
    [{ data: undefined }, { data: content }],
    [{ data: detail }, { data: undefined }],
  ])("renders loading placeholders while either document response is pending", (doc, body) => {
    documentResult = doc;
    contentResult = body;
    const { container } = render(<HistoryDocumentPage />);

    expect(container.querySelectorAll(".animate-pulse")).toHaveLength(3);
  });

  it("renders every captured block kind, version links, and singular visit labels", () => {
    documentResult = {
      data: {
        ...detail,
        lead_image_id: null,
        locations: [
          {
            ...detail.locations[0],
            title: " ",
            visit_count: 1,
            favicon_image_id: null,
          },
        ],
        other_versions: [
          {
            document_id: 10,
            first_seen_at: "2026-07-18T09:00:00Z",
            last_seen_at: "2026-07-19T09:00:00Z",
            is_current: false,
          },
          {
            document_id: 11,
            first_seen_at: "2026-07-19T09:00:00Z",
            last_seen_at: "2026-07-20T09:00:00Z",
            is_current: true,
          },
        ],
      },
    };
    contentResult = {
      data: {
        ...content,
        blocks: [
          { id: "b0001", kind: "heading", text: "Heading" },
          { id: "b0002", kind: "quote", text: "Quoted" },
          { id: "b0003", kind: "code", text: "const answer = 42;" },
          { id: "b0004", kind: "list_item", text: "Listed" },
          { id: "b0005", kind: "list_item", text: "Listed again" },
          { id: "b0006", kind: "paragraph", text: "Paragraph" },
          { id: "b0007", kind: "list_item", text: "Listed later" },
        ],
      },
    };
    const { container } = render(<HistoryDocumentPage />);

    // Adjacent list items share one list; a paragraph starts a new one.
    const lists = container.querySelectorAll(".reader ul");
    expect(lists).toHaveLength(2);
    expect(lists[0]!.querySelectorAll("li")).toHaveLength(2);
    expect(lists[1]!.querySelectorAll("li")).toHaveLength(1);

    expect(
      screen.getByRole("heading", { name: "https://example.com/saved" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/1 URL visit$/)).toBeInTheDocument();
    expect(screen.getByText("Quoted").tagName).toBe("BLOCKQUOTE");
    expect(screen.getByText("const answer = 42;").tagName).toBe("CODE");
    expect(screen.getByText("Listed").tagName).toBe("LI");
    expect(screen.getByText("Paragraph").tagName).toBe("P");
    expect(screen.getByRole("link", { name: /current$/ })).toHaveAttribute(
      "href",
      "/history/documents/11",
    );
    expect(screen.queryByTestId("private-image")).not.toBeInTheDocument();
  });

  it.each(["javascript:alert(1)", "not a url"])(
    "does not expose an unsafe current-page URL: %s",
    (url) => {
      documentResult = {
        data: {
          ...detail,
          locations: [{ ...detail.locations[0], url }],
        },
      };
      render(<HistoryDocumentPage />);

      expect(
        screen.queryByRole("link", { name: "Open current page" }),
      ).not.toBeInTheDocument();
    },
  );

  it("falls back cleanly when the document has no current page location", () => {
    documentResult = {
      data: {
        ...detail,
        locations: [],
      },
    };
    render(<HistoryDocumentPage />);

    expect(screen.getByText("Browser history")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Saved page" })).toBeInTheDocument();
    expect(screen.queryByText(/^Saved /, { selector: "p" })).not.toBeInTheDocument();
  });

  it("shows a decryption error instead of captured blocks", () => {
    contentResult = { error: new Error("decrypt failed") };
    render(<HistoryDocumentPage />);

    expect(
      screen.getByText("Could not decrypt the saved page text."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Saved page content" }),
    ).not.toBeInTheDocument();
  });
});
