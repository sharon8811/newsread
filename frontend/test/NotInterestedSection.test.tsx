import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NotInterestedSection from "@/components/NotInterestedSection";
import { makeDislikeRule } from "./fixtures";

const { swrMock, mutateMock } = vi.hoisted(() => ({ swrMock: vi.fn(), mutateMock: vi.fn() }));
vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));

describe("<NotInterestedSection>", () => {
  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
  });

  it("renders nothing while loading", () => {
    swrMock.mockReturnValue({ data: undefined });
    const { container } = render(<NotInterestedSection />);
    expect(container.firstChild).toBeNull();
  });

  it("shows the empty state", () => {
    swrMock.mockReturnValue({ data: [] });
    render(<NotInterestedSection />);
    expect(screen.getByText(/Nothing muted/)).toBeInTheDocument();
  });

  it("lists rules with kind, hit count and expiry", () => {
    swrMock.mockImplementation((key: unknown) =>
      key === "/interests/dislikes"
        ? {
            data: [
              makeDislikeRule(),
              makeDislikeRule({
                id: 2,
                kind: "story",
                label: "Big Event",
                hidden_count: 7,
                expires_at: new Date(Date.now() + 5 * 86_400_000).toISOString(),
              }),
            ],
          }
        : { data: undefined },
    );
    render(<NotInterestedSection />);
    expect(screen.getByText("crypto prices")).toBeInTheDocument();
    expect(screen.getByText("Big Event")).toBeInTheDocument();
    expect(screen.getByText("3 hidden")).toBeInTheDocument();
    expect(screen.getByText(/7 hidden · expires in 5d/)).toBeInTheDocument();
  });

  it("shows 'expires today' on the last day", () => {
    swrMock.mockImplementation((key: unknown) =>
      key === "/interests/dislikes"
        ? {
            data: [
              makeDislikeRule({
                kind: "story",
                expires_at: new Date(Date.now() + 3_600_000).toISOString(),
              }),
            ],
          }
        : { data: undefined },
    );
    render(<NotInterestedSection />);
    expect(screen.getByText(/expires today/)).toBeInTheDocument();
  });

  it("shows the empty note when an expanded rule has no recent hits", async () => {
    swrMock.mockImplementation((key: unknown) =>
      key === "/interests/dislikes"
        ? { data: [makeDislikeRule()] }
        : key === "/interests/dislikes/1/articles"
          ? { data: [] }
          : { data: undefined },
    );
    render(<NotInterestedSection />);
    await userEvent.click(screen.getByText("3 hidden"));
    expect(screen.getByText("Nothing hidden recently.")).toBeInTheDocument();
    // Clicking again collapses.
    await userEvent.click(screen.getByText("3 hidden"));
    expect(screen.queryByText("Nothing hidden recently.")).not.toBeInTheDocument();
  });

  it("expands a rule to show its recently hidden articles", async () => {
    swrMock.mockImplementation((key: unknown) =>
      key === "/interests/dislikes"
        ? { data: [makeDislikeRule()] }
        : key === "/interests/dislikes/1/articles"
          ? { data: [{ id: 9, title: "A hidden headline" }] }
          : { data: undefined },
    );
    render(<NotInterestedSection />);
    await userEvent.click(screen.getByText("3 hidden"));
    expect(screen.getByText("A hidden headline")).toBeInTheDocument();
  });

  it("deletes a rule and revalidates rules + article lists", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ status: 204, ok: true });
    vi.stubGlobal("fetch", fetchMock);
    swrMock.mockImplementation((key: unknown) =>
      key === "/interests/dislikes" ? { data: [makeDislikeRule()] } : { data: undefined },
    );
    render(<NotInterestedSection />);
    await userEvent.click(screen.getByTitle("Remove rule"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(String(fetchMock.mock.calls[0][0])).toContain("/interests/dislikes/1");
    expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
    await waitFor(() => expect(mutateMock).toHaveBeenCalledWith("/interests/dislikes"));
  });
});
