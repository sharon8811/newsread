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
  const utils = render(
    <ArticleCard
      article={article}
      index={0}
      onToggleSaved={onToggleSaved}
      onShare={onShare}
      onAddToProject={onAddToProject}
      {...props}
    />,
  );
  return { article, onToggleSaved, onShare, onAddToProject, ...utils };
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

  it("external link does not trigger navigation", async () => {
    renderCard();
    await userEvent.click(screen.getByTitle("Open original"));
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("marks the saved state on the bookmark button", () => {
    renderCard({ is_saved: true });
    expect(screen.getByTitle("Unsave").className).toContain("active");
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
