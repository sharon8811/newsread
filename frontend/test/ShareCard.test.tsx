import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ShareCard from "@/components/ShareCard";
import { makeShare, makePublic } from "./fixtures";

const { pushMock, mutateMock } = vi.hoisted(() => ({ pushMock: vi.fn(), mutateMock: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }));
vi.mock("swr", () => ({ mutate: mutateMock }));

function okFetch() {
  return vi.fn().mockResolvedValue({ status: 204, ok: true, json: async () => null });
}

describe("<ShareCard>", () => {
  beforeEach(() => {
    pushMock.mockClear();
    mutateMock.mockClear();
  });

  it("shows sender info for received shares", () => {
    render(<ShareCard share={makeShare()} direction="received" />);
    expect(screen.getByText("Carol")).toBeInTheDocument();
    expect(screen.getByText(/shared this with you/)).toBeInTheDocument();
  });

  it("marks a new received share seen on open, then navigates", async () => {
    vi.stubGlobal("fetch", okFetch());
    render(<ShareCard share={makeShare({ seen_at: null })} direction="received" />);
    await userEvent.click(screen.getByText("A Great Article"));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/article/1"));
    expect(mutateMock).toHaveBeenCalledWith("/shares/received");
    expect(mutateMock).toHaveBeenCalledWith("/shares/unseen-count");
  });

  it("does not re-mark an already-seen share", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<ShareCard share={makeShare({ seen_at: "2024-01-02T00:00:00Z" })} direction="received" />);
    await userEvent.click(screen.getByText("A Great Article"));
    await waitFor(() => expect(pushMock).toHaveBeenCalled());
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows recipients for sent shares", () => {
    render(
      <ShareCard
        share={makeShare({ to_users: [makePublic({ id: 2, username: "bob", name: "Bob" }),
                                      makePublic({ id: 3, username: "cara", name: "Cara" })] })}
        direction="sent"
      />,
    );
    expect(screen.getByText("@bob")).toBeInTheDocument();
    expect(screen.getByText("@cara")).toBeInTheDocument();
  });

  it("renders a note when present", () => {
    render(<ShareCard share={makeShare({ note: "must read" })} direction="received" />);
    expect(screen.getByText("must read")).toBeInTheDocument();
  });

  it("handles a sent share with no recipients gracefully", () => {
    render(<ShareCard share={makeShare({ to_users: [] })} direction="sent" />);
    expect(screen.getByText("?")).toBeInTheDocument();
  });

  it("swallows a failing seen request but still navigates", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ status: 500, ok: false, json: async () => ({}) }));
    render(<ShareCard share={makeShare({ seen_at: null })} direction="received" />);
    await userEvent.click(screen.getByText("A Great Article"));
    await waitFor(() => expect(pushMock).toHaveBeenCalled());
  });
});
