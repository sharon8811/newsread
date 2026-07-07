import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ArticleRow from "@/components/ArticleRow";
import { makeArticle, makeEntity } from "./fixtures";

const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }));

describe("<ArticleRow>", () => {
  beforeEach(() => pushMock.mockClear());

  const noop = () => {};

  it("navigates to the article on click", async () => {
    render(<ArticleRow article={makeArticle()} index={0} onToggleSaved={noop} onShare={noop} onAddToProject={noop} />);
    await userEvent.click(screen.getByText("A Great Article"));
    expect(pushMock).toHaveBeenCalledWith("/article/1");
  });

  it("shows the excerpt when there is no summary", () => {
    render(<ArticleRow article={makeArticle({ excerpt: "the excerpt" })} index={0}
      onToggleSaved={noop} onShare={noop} onAddToProject={noop} />);
    expect(screen.getByText("the excerpt")).toBeInTheDocument();
  });

  it("shows a read-more toggle and expands the medium summary", async () => {
    render(<ArticleRow article={makeArticle({ summary_short: "one liner", summary_medium: "the longer one" })}
      index={0} onToggleSaved={noop} onShare={noop} onAddToProject={noop} />);
    expect(screen.queryByText("the longer one")).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("+ read more"));
    expect(screen.getByText("the longer one")).toBeInTheDocument();
    await userEvent.click(screen.getByText("− show less"));
    expect(screen.queryByText("the longer one")).not.toBeInTheDocument();
  });

  it("does not navigate when toggling read-more", async () => {
    render(<ArticleRow article={makeArticle({ summary_medium: "more" })} index={0}
      onToggleSaved={noop} onShare={noop} onAddToProject={noop} />);
    await userEvent.click(screen.getByText("+ read more"));
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("calls onToggleSaved without navigating", async () => {
    const onToggleSaved = vi.fn();
    render(<ArticleRow article={makeArticle()} index={0} onToggleSaved={onToggleSaved} onShare={noop} onAddToProject={noop} />);
    await userEvent.click(screen.getByTitle("Save for later"));
    expect(onToggleSaved).toHaveBeenCalled();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("shows Unsave when already saved", () => {
    render(<ArticleRow article={makeArticle({ is_saved: true })} index={0} onToggleSaved={noop} onShare={noop} onAddToProject={noop} />);
    expect(screen.getByTitle("Unsave")).toBeInTheDocument();
  });

  it("calls onShare", async () => {
    const onShare = vi.fn();
    render(<ArticleRow article={makeArticle()} index={0} onToggleSaved={noop} onShare={onShare} onAddToProject={noop} />);
    await userEvent.click(screen.getByTitle("Share with a note"));
    expect(onShare).toHaveBeenCalled();
  });

  it("calls onAddToProject", async () => {
    const onAddToProject = vi.fn();
    render(<ArticleRow article={makeArticle()} index={0} onToggleSaved={noop} onShare={noop} onAddToProject={onAddToProject} />);
    await userEvent.click(screen.getByTitle("Add to project"));
    expect(onAddToProject).toHaveBeenCalled();
  });

  it("renders entity badges when present", () => {
    render(<ArticleRow article={makeArticle({ entities: [makeEntity()] })} index={0}
      onToggleSaved={noop} onShare={noop} onAddToProject={noop} />);
    expect(screen.getByText("★ 1.2k")).toBeInTheDocument();
  });

  it("renders an image when present", () => {
    const { container } = render(<ArticleRow article={makeArticle({ image_url: "https://x/i.png" })} index={0}
      onToggleSaved={noop} onShare={noop} onAddToProject={noop} />);
    expect(container.querySelector("img")).toHaveAttribute("src", "https://x/i.png");
  });

  it("hides the broken image on error", async () => {
    const { container } = render(<ArticleRow article={makeArticle({ image_url: "https://x/broken.png" })} index={0}
      onToggleSaved={noop} onShare={noop} onAddToProject={noop} />);
    const img = container.querySelector("img") as HTMLImageElement;
    img.dispatchEvent(new Event("error"));
    expect(img.style.display).toBe("none");
  });

  it("reserves an image frame while enriching", () => {
    const { container } = render(<ArticleRow article={makeArticle({ enriching: true })} index={0}
      onToggleSaved={noop} onShare={noop} onAddToProject={noop} />);
    expect(container.querySelector(".shimmer")).toBeInTheDocument();
  });

  it("applies hover background styles for a selected row", async () => {
    render(<ArticleRow article={makeArticle()} selected index={0} onToggleSaved={noop} onShare={noop} onAddToProject={noop} />);
    const row = screen.getByText("A Great Article").closest("[data-row-index]") as HTMLElement;
    row.dispatchEvent(new MouseEvent("mouseenter"));
    row.dispatchEvent(new MouseEvent("mouseleave"));
    expect(row).toBeInTheDocument();
  });

  it("resets to transparent on mouseleave when not selected", async () => {
    render(<ArticleRow article={makeArticle()} index={0} onToggleSaved={noop} onShare={noop} onAddToProject={noop} />);
    const row = screen.getByText("A Great Article").closest("[data-row-index]") as HTMLElement;
    row.dispatchEvent(new MouseEvent("mouseenter"));
    row.dispatchEvent(new MouseEvent("mouseleave"));
    expect(row.style.background).toContain("transparent");
  });

  it("opening the original does not navigate to the article", async () => {
    render(<ArticleRow article={makeArticle()} index={0} onToggleSaved={noop} onShare={noop} onAddToProject={noop} />);
    await userEvent.click(screen.getByTitle("Open original"));
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("shows the author in the meta line", () => {
    render(<ArticleRow article={makeArticle({ author: "Jane Reporter" })} index={0}
      onToggleSaved={noop} onShare={noop} onAddToProject={noop} />);
    expect(screen.getByText(/Jane Reporter/)).toBeInTheDocument();
  });
});
