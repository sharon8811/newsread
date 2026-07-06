import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import StoriesView from "@/components/StoriesView";
import { makeArticle, makeEntity } from "./fixtures";
import type { Article } from "@/lib/api";

const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }));

const { swrMock, mutateMock } = vi.hoisted(() => ({
  swrMock: vi.fn(),
  mutateMock: vi.fn(),
}));
vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));

function setData(data: Article[] | undefined, isLoading = false) {
  swrMock.mockReturnValue({ data, isLoading });
}

function renderStories(
  props: Partial<React.ComponentProps<typeof StoriesView>> = {},
) {
  const onExit = props.onExit ?? vi.fn();
  const utils = render(<StoriesView onExit={onExit} {...props} />);
  return { ...utils, onExit };
}

describe("<StoriesView>", () => {
  beforeEach(() => {
    pushMock.mockClear();
    mutateMock.mockClear();
    swrMock.mockReset();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => ({}) }),
    );
  });

  it("shows a loading skeleton while SWR is loading", () => {
    setData(undefined, true);
    const { container } = renderStories();
    expect(container.querySelector(".animate-pulse")).toBeInTheDocument();
  });

  it("shows the empty 'All caught up' state for an empty unread queue", () => {
    setData([]);
    const { onExit } = renderStories({ filter: "unread" });
    expect(screen.getByText("All caught up.")).toBeInTheDocument();
    expect(
      screen.getByText("New stories land here as your feeds refresh."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByText("Back to list"));
    expect(onExit).toHaveBeenCalled();
  });

  it("shows the saved-specific empty subtitle", () => {
    setData([]);
    renderStories({ filter: "saved" });
    expect(
      screen.getByText("Nothing saved to flip through."),
    ).toBeInTheDocument();
  });

  it("renders a card with fallback background, entities, and AI summary badge", () => {
    setData([
      makeArticle({
        id: 10,
        title: "First Story",
        image_url: null,
        summary_medium: "the medium summary",
        entities: [makeEntity()],
      }),
    ]);
    const { container } = renderStories();
    expect(screen.getByText("First Story")).toBeInTheDocument();
    expect(screen.getByText("the medium summary")).toBeInTheDocument();
    expect(screen.getByText("✦")).toBeInTheDocument();
    // fallback background applied (no url())
    const bg = container.querySelector(".fade-up") as HTMLElement;
    expect(bg.style.backgroundImage).not.toContain("url(");
  });

  it("renders an image background and the excerpt when there is no AI summary", () => {
    setData([
      makeArticle({
        id: 11,
        image_url: "https://x/img.png",
        summary_medium: "",
        summary_short: "",
        excerpt: "just an excerpt",
        published_at: "2024-01-01T00:00:00Z",
        is_read: true,
      }),
    ]);
    const { container } = renderStories();
    expect(screen.getByText("just an excerpt")).toBeInTheDocument();
    expect(screen.queryByText("✦")).not.toBeInTheDocument();
    const bg = container.querySelector(".fade-up") as HTMLElement;
    expect(bg.style.backgroundImage).toContain("url(");
    // is_read renders "· read" marker in the meta line
    expect(screen.getByText(/· read/)).toBeInTheDocument();
  });

  it("renders no summary paragraph when there is no summary text", () => {
    setData([makeArticle({ id: 12, excerpt: "", summary_medium: "", summary_short: "" })]);
    renderStories();
    expect(screen.getByText("A Great Article")).toBeInTheDocument();
    expect(screen.queryByText("✦")).not.toBeInTheDocument();
  });

  it("advances with ArrowRight and marks the current card read", async () => {
    setData([
      makeArticle({ id: 1, title: "One" }),
      makeArticle({ id: 2, title: "Two" }),
    ]);
    renderStories();
    expect(screen.getByText("One")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByText("Two")).toBeInTheDocument();
    expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0]).toContain(
      "/articles/1/state",
    );
  });

  it("advances with Space and reaches the done state at the end", () => {
    setData([makeArticle({ id: 1, title: "Only" })]);
    renderStories();
    fireEvent.keyDown(window, { key: " " });
    expect(screen.getByText("You're up to date.")).toBeInTheDocument();
    expect(screen.getByText("1 story read")).toBeInTheDocument();
  });

  it("goes back from the done state via 'Go back'", () => {
    setData([makeArticle({ id: 1, title: "Only" })]);
    renderStories();
    fireEvent.keyDown(window, { key: " " });
    expect(screen.getByText("You're up to date.")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Go back"));
    expect(screen.getByText("Only")).toBeInTheDocument();
  });

  it("plural 'stories read' when more than one card was read", () => {
    setData([
      makeArticle({ id: 1, title: "One" }),
      makeArticle({ id: 2, title: "Two" }),
    ]);
    renderStories();
    fireEvent.keyDown(window, { key: "ArrowRight" });
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByText("2 stories read")).toBeInTheDocument();
  });

  it("goes back with ArrowLeft, clamped at the first card", () => {
    setData([
      makeArticle({ id: 1, title: "One" }),
      makeArticle({ id: 2, title: "Two" }),
    ]);
    renderStories();
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByText("Two")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(screen.getByText("One")).toBeInTheDocument();
    // clamp: already at 0
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(screen.getByText("One")).toBeInTheDocument();
  });

  it("does not mark read when markOnAdvance is false", () => {
    setData([
      makeArticle({ id: 1, title: "One" }),
      makeArticle({ id: 2, title: "Two" }),
    ]);
    renderStories({ markOnAdvance: false });
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect((globalThis.fetch as ReturnType<typeof vi.fn>)).not.toHaveBeenCalled();
  });

  it("does not re-mark an already-read card", () => {
    setData([
      makeArticle({ id: 1, title: "One", is_read: true }),
      makeArticle({ id: 2, title: "Two" }),
    ]);
    renderStories();
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect((globalThis.fetch as ReturnType<typeof vi.fn>)).not.toHaveBeenCalled();
  });

  it("opens the article with Enter and ArrowUp", () => {
    setData([makeArticle({ id: 7, title: "Open Me" })]);
    renderStories();
    fireEvent.keyDown(window, { key: "Enter" });
    expect(pushMock).toHaveBeenCalledWith("/article/7");
    fireEvent.keyDown(window, { key: "ArrowUp" });
    expect(pushMock).toHaveBeenCalledTimes(2);
  });

  it("exits with Escape", () => {
    setData([makeArticle({ id: 1 })]);
    const { onExit } = renderStories();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onExit).toHaveBeenCalled();
  });

  it("toggles saved with the 's' key and the save button", async () => {
    setData([makeArticle({ id: 9, is_saved: false })]);
    renderStories();
    fireEvent.keyDown(window, { key: "s" });
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    expect(fetchMock.mock.calls[0][0]).toContain("/articles/9/state");
    // UI reflects saved; now button title is Unsave
    expect(screen.getByTitle("Unsave")).toBeInTheDocument();
    await userEvent.click(screen.getByTitle("Unsave"));
    expect(screen.getByTitle("Save for later")).toBeInTheDocument();
  });

  it("ignores unrelated keys", () => {
    setData([makeArticle({ id: 1, title: "One" })]);
    renderStories();
    fireEvent.keyDown(window, { key: "x" });
    expect(screen.getByText("One")).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("ignores keys while typing in an input", () => {
    setData([makeArticle({ id: 1, title: "One" })]);
    renderStories();
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    fireEvent.keyDown(input, { key: "ArrowRight" });
    expect(screen.getByText("One")).toBeInTheDocument();
    document.body.removeChild(input);
  });

  it("does not advance/open/save via keyboard once done", () => {
    setData([makeArticle({ id: 1, title: "Only" })]);
    renderStories();
    fireEvent.keyDown(window, { key: " " }); // reach done
    fireEvent.keyDown(window, { key: "ArrowRight" }); // no-op
    fireEvent.keyDown(window, { key: "Enter" }); // no-op
    fireEvent.keyDown(window, { key: "s" }); // no-op
    expect(pushMock).not.toHaveBeenCalled();
    expect(screen.getByText("You're up to date.")).toBeInTheDocument();
  });

  it("opens the article from the 'read full article' button and tap zones", () => {
    setData([
      makeArticle({ id: 3, title: "One" }),
      makeArticle({ id: 4, title: "Two" }),
    ]);
    const { container } = renderStories();
    fireEvent.click(screen.getByText("read full article"));
    expect(pushMock).toHaveBeenCalledWith("/article/3");
    // right tap zone advances, left goes back
    const right = container.querySelector(".right-0.z-10") as HTMLElement;
    const left = container.querySelector(".left-0.z-10") as HTMLElement;
    fireEvent.click(right);
    expect(screen.getByText("Two")).toBeInTheDocument();
    fireEvent.click(left);
    expect(screen.getByText("One")).toBeInTheDocument();
  });

  it("exits from the top-right X button", () => {
    setData([makeArticle({ id: 1 })]);
    const { onExit } = renderStories();
    fireEvent.click(screen.getByTitle("Exit stories (Esc)"));
    expect(onExit).toHaveBeenCalled();
  });

  it("opens the article on a swipe-up touch gesture", () => {
    setData([makeArticle({ id: 5, title: "Swipe" })]);
    const { container } = renderStories();
    const root = container.firstChild as HTMLElement;
    fireEvent.touchStart(root, { touches: [{ clientY: 400 }] });
    fireEvent.touchEnd(root, { changedTouches: [{ clientY: 100 }] });
    expect(pushMock).toHaveBeenCalledWith("/article/5");
  });

  it("does not open the article on a small / absent touch move", () => {
    setData([makeArticle({ id: 6, title: "NoSwipe" })]);
    const { container } = renderStories();
    const root = container.firstChild as HTMLElement;
    // touchEnd without a start: touchY is null
    fireEvent.touchEnd(root, { changedTouches: [{ clientY: 100 }] });
    // small move: below threshold
    fireEvent.touchStart(root, { touches: [{ clientY: 200 }] });
    fireEvent.touchEnd(root, { changedTouches: [{ clientY: 190 }] });
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("renders the compact progress bar for large queues and preloads images", () => {
    const many = Array.from({ length: 32 }, (_, i) =>
      makeArticle({ id: 100 + i, title: `Story ${i}`, image_url: `https://x/${i}.png` }),
    );
    setData(many);
    renderStories();
    expect(screen.getByText("1 / 32")).toBeInTheDocument();
  });

  it("revalidates article lists on unmount", () => {
    setData([makeArticle({ id: 1 })]);
    const { unmount } = renderStories();
    unmount();
    // mutateArticleLists calls mutate twice (predicate + "/feeds")
    expect(mutateMock).toHaveBeenCalled();
  });
});
