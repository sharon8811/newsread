import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NotInterestedModal from "@/components/NotInterestedModal";
import type { Article, DislikeRuleCreated } from "@/lib/api";
import { makeArticle, makeDislikeOptions, makeEntity } from "./fixtures";

const { swrMock, mutateMock } = vi.hoisted(() => ({ swrMock: vi.fn(), mutateMock: vi.fn() }));
vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));

function jsonResponse(body: unknown) {
  return {
    status: 200,
    ok: true,
    headers: { get: () => "application/json" },
    json: async () => body,
  };
}

function created(id: number, hidden = 1) {
  return {
    rule: { id, kind: "article", hidden_count: hidden },
    preview: [],
  } as unknown as DislikeRuleCreated;
}

// The article-hide POST now happens in the click handler that opens the modal
// (see the ArticleList tests); the modal receives that request in flight.
function renderModal({
  article = makeArticle(),
  hide = Promise.resolve(created(99)),
  onClose = () => {},
}: {
  article?: Article;
  hide?: Promise<DislikeRuleCreated>;
  onClose?: () => void;
} = {}) {
  return render(<NotInterestedModal article={article} hide={hide} onClose={onClose} />);
}

describe("<NotInterestedModal>", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let nextRuleId: number;

  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
    nextRuleId = 1;
    fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (init?.method === "DELETE") return { status: 204, ok: true };
      const hidden = String(init?.body ?? "").includes('"topic"') ? 4 : 1;
      return jsonResponse(created(nextRuleId++, hidden));
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  it("shows the suggestion placeholder while options load", () => {
    swrMock.mockReturnValue({ data: undefined });
    renderModal();
    expect(screen.getByText("Suggesting topics…")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toHaveClass("p-4", "sm:p-6");
  });

  it("falls back to the article's own entity badges before options load", () => {
    swrMock.mockReturnValue({ data: undefined });
    renderModal({
      article: makeArticle({
        entities: [
          makeEntity({ badge: { label: "acme/widget" } }),
          makeEntity({ id: 6, key: "raw/key", badge: {} }),
        ],
      }),
    });
    expect(screen.getByText("acme/widget")).toBeInTheDocument(); // badge label
    expect(screen.getByText("raw/key")).toBeInTheDocument(); // key fallback
  });

  it("renders story, entity and topic chips from the options", async () => {
    swrMock.mockReturnValue({ data: makeDislikeOptions() });
    renderModal();
    expect(screen.getByText("This story (2 weeks)")).toBeInTheDocument();
    expect(screen.getByText("acme/widget")).toBeInTheDocument();
    expect(screen.getByText("crypto prices")).toBeInTheDocument();
    expect(screen.getByText("celebrity gossip")).toBeInTheDocument();
  });

  it("hides the story chip when unavailable and shows the empty note", () => {
    swrMock.mockReturnValue({
      data: makeDislikeOptions({ story_available: false, entities: [], topics: [] }),
    });
    renderModal();
    expect(screen.queryByText("This story (2 weeks)")).not.toBeInTheDocument();
    expect(screen.getByText("No broader suggestions for this one.")).toBeInTheDocument();
  });

  it("posts the right body for a topic chip and shows the extra-hits caption", async () => {
    swrMock.mockReturnValue({ data: makeDislikeOptions() });
    renderModal();
    await userEvent.click(screen.getByText("crypto prices"));
    await waitFor(() => {
      const bodies = fetchMock.mock.calls.map(([, init]) => String(init?.body ?? ""));
      expect(bodies.some((b) => b.includes('"phrase":"crypto prices"'))).toBe(true);
    });
    // hidden_count 4 -> "+3 recent" (the dismissed article itself is not news)
    expect(await screen.findByText("+3 recent")).toBeInTheDocument();
  });

  it("posts entity and story bodies for their chips", async () => {
    swrMock.mockReturnValue({ data: makeDislikeOptions() });
    renderModal();
    await userEvent.click(screen.getByText("acme/widget"));
    await userEvent.click(screen.getByText("This story (2 weeks)"));
    await waitFor(() => {
      const bodies = fetchMock.mock.calls.map(([, init]) => String(init?.body ?? ""));
      expect(bodies.some((b) => b.includes('"kind":"entity"') && b.includes('"entity_id":5'))).toBe(true);
      expect(bodies.some((b) => b.includes('"kind":"story"'))).toBe(true);
    });
  });

  it("undo deletes the pre-created hide rule and every chip rule, then closes", async () => {
    swrMock.mockReturnValue({ data: makeDislikeOptions() });
    const onClose = vi.fn();
    renderModal({ hide: Promise.resolve(created(99)), onClose });
    await userEvent.click(screen.getByText("crypto prices"));
    await screen.findByText("+3 recent");
    await userEvent.click(screen.getByText("Undo"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const deletes = fetchMock.mock.calls.filter(([, init]) => init?.method === "DELETE");
    expect(deletes.length).toBe(2);
    const urls = deletes.map(([url]) => String(url)).join();
    expect(urls).toContain("/interests/dislikes/99"); // the click-handler rule
  });

  it("closes on Done and on Escape", async () => {
    swrMock.mockReturnValue({ data: undefined });
    const onClose = vi.fn();
    renderModal({ onClose });
    await userEvent.click(screen.getByText("Done"));
    expect(onClose).toHaveBeenCalledTimes(1);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("surfaces an error when the hide request failed", async () => {
    swrMock.mockReturnValue({ data: undefined });
    const hide = Promise.reject(new Error("boom"));
    hide.catch(() => {}); // pre-attach so the rejection is never unhandled
    renderModal({ hide });
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
  });
});
