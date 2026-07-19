import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ArticleCard from "@/components/ArticleCard";
import { makeArticle } from "./fixtures";

const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }));

function renderCard(over: Parameters<typeof makeArticle>[0] = {}, props = {}) {
  const article = makeArticle(over);
  const onToggleSaved = vi.fn();
  const onShare = vi.fn();
  const onAddToProject = vi.fn();
  const onNotInterested = vi.fn();
  const utils = render(
    <ArticleCard
      article={article}
      index={0}
      onToggleSaved={onToggleSaved}
      onShare={onShare}
      onAddToProject={onAddToProject}
      onNotInterested={onNotInterested}
      {...props}
    />,
  );
  return { article, onToggleSaved, onShare, onAddToProject, onNotInterested, ...utils };
}

describe("<ArticleCard>", () => {
  beforeEach(() => pushMock.mockClear());

  it("navigates on click", async () => {
    renderCard();
    await userEvent.click(screen.getByText("A Great Article"));
    expect(pushMock).toHaveBeenCalledWith("/article/1");
  });

  it("shows the domain and time in the meta line", () => {
    renderCard();
    expect(screen.getByText(/site\.example/)).toBeInTheDocument();
  });

  it("omits the time when unpublished", () => {
    renderCard({ published_at: null });
    expect(screen.getByText("site.example")).toBeInTheDocument();
  });

  it("prefers the AI short summary with the sparkle marker", () => {
    renderCard({ summary_short: "the gist" });
    expect(screen.getByText(/the gist/)).toBeInTheDocument();
    expect(screen.getByText("✦")).toBeInTheDocument();
  });

  it("falls back to the excerpt without a sparkle", () => {
    renderCard();
    expect(screen.getByText(/an excerpt/)).toBeInTheDocument();
    expect(screen.queryByText("✦")).not.toBeInTheDocument();
  });

  it("renders the image when present", () => {
    const { container } = renderCard({ image_url: "https://img.example/a.jpg" });
    const img = container.querySelector("img");
    expect(img).toHaveAttribute("src", "https://img.example/a.jpg");
    fireEvent.error(img!);
    expect(img!.style.display).toBe("none");
  });

  it("renders no media frame without an image", () => {
    const { container } = renderCard();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".shimmer")).toBeNull();
  });

  it("reserves no media frame while merely enriching (image not yet available)", () => {
    const { container } = renderCard({ enriching: true });
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".shimmer")).toBeNull();
  });

  it("shows the author, falling back to the feed title", () => {
    renderCard();
    expect(screen.getByText("Reporter")).toBeInTheDocument();
    renderCard({ id: 2, author: null });
    expect(screen.getByText("Tech Feed")).toBeInTheDocument();
  });

  it("save button toggles without navigating", async () => {
    const { article, onToggleSaved } = renderCard();
    await userEvent.click(screen.getByTitle("Save for later"));
    expect(onToggleSaved).toHaveBeenCalledWith(article);
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("keeps card actions visible on touch-sized screens", () => {
    renderCard();
    expect(screen.getByTitle("Save for later").parentElement).toHaveClass(
      "opacity-100",
      "sm:opacity-0",
      "sm:group-hover:opacity-100",
    );
  });

  it("share button opens the share flow without navigating", async () => {
    const { article, onShare } = renderCard();
    await userEvent.click(screen.getByTitle("Share with a note"));
    expect(onShare).toHaveBeenCalledWith(article);
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("project button opens the picker without navigating", async () => {
    const { article, onAddToProject } = renderCard();
    await userEvent.click(screen.getByTitle("Add to project"));
    expect(onAddToProject).toHaveBeenCalledWith(article);
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("not-interested button fires without navigating", async () => {
    const { article, onNotInterested } = renderCard();
    await userEvent.click(screen.getByTitle("Not interested"));
    expect(onNotInterested).toHaveBeenCalledWith(article);
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("external link does not trigger navigation", async () => {
    renderCard();
    await userEvent.click(screen.getByTitle("Open original"));
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("marks the saved state on the bookmark button", () => {
    renderCard({ is_saved: true });
    expect(screen.getByTitle("Unsave").className).toContain("active");
  });

  it("shows an explicit unread/read label in reading mode", () => {
    const first = renderCard({ is_read: false }, { showReadState: true });
    expect(screen.getByLabelText("Unread")).toHaveTextContent("Unread");
    first.unmount();

    renderCard({ is_read: true }, { showReadState: true });
    expect(screen.getByLabelText("Read")).toHaveTextContent("Read");
  });

  it("toggles read state without opening the card", async () => {
    const onToggleRead = vi.fn();
    const { article } = renderCard(
      { is_read: false },
      { showReadState: true, onToggleRead },
    );
    await userEvent.click(screen.getByTitle("Mark as read"));
    expect(onToggleRead).toHaveBeenCalledWith(article);
    expect(pushMock).not.toHaveBeenCalled();
    expect(screen.getByTitle("Mark as read")).toHaveClass(
      "min-h-11",
      "min-w-11",
    );
  });

  it("offers Mark as unread for a read card", () => {
    renderCard(
      { is_read: true },
      { showReadState: true, onToggleRead: vi.fn() },
    );
    expect(screen.getByTitle("Mark as unread")).toHaveClass("active");
  });
});

describe("<ArticleCard> generating illustration", () => {
  it("shows a shimmering generating placeholder while pending", () => {
    const { container } = renderCard({ image_pending: true });
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".shimmer")).not.toBeNull();
    expect(screen.getByRole("status")).toHaveAccessibleName("Generating illustration");
  });

  it("prefers the finished image over the pending state", () => {
    const { container } = renderCard({
      image_pending: true,
      image_url: "https://img.example/a.jpg",
    });
    expect(container.querySelector("img")).toHaveAttribute("src", "https://img.example/a.jpg");
    expect(container.querySelector(".shimmer")).toBeNull();
  });
});
