import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import SentPage from "@/app/(app)/sent/page";
import SharedPage from "@/app/(app)/shared/page";
import { makeShare } from "./fixtures";

const { swrMock } = vi.hoisted(() => ({ swrMock: vi.fn() }));
vi.mock("swr", () => ({ default: swrMock, mutate: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/ShareCard", () => ({
  default: ({ direction }: { direction: string }) => (
    <div data-testid="share-card">{direction}</div>
  ),
}));

describe("SentPage", () => {
  beforeEach(() => swrMock.mockReset());

  it("shows the empty state when there are no shares", () => {
    swrMock.mockReturnValue({ data: [], isLoading: false });
    render(<SentPage />);
    expect(screen.getByText("You have not shared anything yet.")).toBeInTheDocument();
  });

  it("does not show the empty state while loading", () => {
    swrMock.mockReturnValue({ data: undefined, isLoading: true });
    render(<SentPage />);
    expect(screen.queryByText("You have not shared anything yet.")).not.toBeInTheDocument();
  });

  it("renders share cards", () => {
    swrMock.mockReturnValue({ data: [makeShare({ id: 1 }), makeShare({ id: 2 })], isLoading: false });
    render(<SentPage />);
    const cards = screen.getAllByTestId("share-card");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent("sent");
  });
});

describe("SharedPage", () => {
  beforeEach(() => swrMock.mockReset());

  it("shows the empty state", () => {
    swrMock.mockReturnValue({ data: [], isLoading: false });
    render(<SharedPage />);
    expect(screen.getByText("Nothing shared with you yet.")).toBeInTheDocument();
  });

  it("renders received share cards", () => {
    swrMock.mockReturnValue({ data: [makeShare()], isLoading: false });
    render(<SharedPage />);
    expect(screen.getByTestId("share-card")).toHaveTextContent("received");
  });

  it("handles undefined data (still loading) without empty state", () => {
    swrMock.mockReturnValue({ data: undefined, isLoading: true });
    render(<SharedPage />);
    expect(screen.queryByText("Nothing shared with you yet.")).not.toBeInTheDocument();
  });
});
