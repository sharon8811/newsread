import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NotInterestedModal from "@/components/NotInterestedModal";
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
  return { rule: { id, kind: "article", hidden_count: hidden }, preview: [] };
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

  it("hides the article on mount and revalidates the lists", async () => {
    swrMock.mockReturnValue({ data: undefined });
    render(<NotInterestedModal article={makeArticle()} onClose={() => {}} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/interests/dislikes");
    expect(JSON.parse(String(init.body))).toEqual({ kind: "article", article_id: 1 });
    await waitFor(() => expect(mutateMock).toHaveBeenCalled());
    expect(screen.getByText("Suggesting topics…")).toBeInTheDocument();
  });

  it("falls back to the article's own entity badges before options load", () => {
    swrMock.mockReturnValue({ data: undefined });
    render(
      <NotInterestedModal
        article={makeArticle({
          entities: [
            makeEntity({ badge: { label: "acme/widget" } }),
            makeEntity({ id: 6, key: "raw/key", badge: {} }),
          ],
        })}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("acme/widget")).toBeInTheDocument(); // badge label
    expect(screen.getByText("raw/key")).toBeInTheDocument(); // key fallback
  });

  it("renders story, entity and topic chips from the options", async () => {
    swrMock.mockReturnValue({ data: makeDislikeOptions() });
    render(<NotInterestedModal article={makeArticle()} onClose={() => {}} />);
    expect(screen.getByText("This story (2 weeks)")).toBeInTheDocument();
    expect(screen.getByText("acme/widget")).toBeInTheDocument();
    expect(screen.getByText("crypto prices")).toBeInTheDocument();
    expect(screen.getByText("celebrity gossip")).toBeInTheDocument();
  });

  it("hides the story chip when unavailable and shows the empty note", () => {
    swrMock.mockReturnValue({
      data: makeDislikeOptions({ story_available: false, entities: [], topics: [] }),
    });
    render(<NotInterestedModal article={makeArticle()} onClose={() => {}} />);
    expect(screen.queryByText("This story (2 weeks)")).not.toBeInTheDocument();
    expect(screen.getByText("No broader suggestions for this one.")).toBeInTheDocument();
  });

  it("posts the right body for a topic chip and shows the extra-hits caption", async () => {
    swrMock.mockReturnValue({ data: makeDislikeOptions() });
    render(<NotInterestedModal article={makeArticle()} onClose={() => {}} />);
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
    render(<NotInterestedModal article={makeArticle()} onClose={() => {}} />);
    await userEvent.click(screen.getByText("acme/widget"));
    await userEvent.click(screen.getByText("This story (2 weeks)"));
    await waitFor(() => {
      const bodies = fetchMock.mock.calls.map(([, init]) => String(init?.body ?? ""));
      expect(bodies.some((b) => b.includes('"kind":"entity"') && b.includes('"entity_id":5'))).toBe(true);
      expect(bodies.some((b) => b.includes('"kind":"story"'))).toBe(true);
    });
  });

  it("undo deletes every created rule and closes", async () => {
    swrMock.mockReturnValue({ data: makeDislikeOptions() });
    const onClose = vi.fn();
    render(<NotInterestedModal article={makeArticle()} onClose={onClose} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled()); // mount rule
    await userEvent.click(screen.getByText("crypto prices"));
    await screen.findByText("+3 recent");
    await userEvent.click(screen.getByText("Undo"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const deletes = fetchMock.mock.calls.filter(([, init]) => init?.method === "DELETE");
    expect(deletes.length).toBe(2);
    expect(deletes.map(([url]) => String(url)).join()).toContain("/interests/dislikes/");
  });

  it("closes on Done and on Escape", async () => {
    swrMock.mockReturnValue({ data: undefined });
    const onClose = vi.fn();
    render(<NotInterestedModal article={makeArticle()} onClose={onClose} />);
    await userEvent.click(screen.getByText("Done"));
    expect(onClose).toHaveBeenCalledTimes(1);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("surfaces an error when hiding fails", async () => {
    swrMock.mockReturnValue({ data: undefined });
    fetchMock.mockResolvedValue({
      status: 500,
      ok: false,
      headers: { get: () => "application/json" },
      json: async () => ({ detail: "boom" }),
    });
    render(<NotInterestedModal article={makeArticle()} onClose={() => {}} />);
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
  });
});
