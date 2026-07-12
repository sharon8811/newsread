import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import * as icons from "@/components/icons";

const ALL = [
  "InboxIcon", "BookmarkIcon", "ShareIcon", "UsersIcon", "ExternalIcon",
  "CommentIcon", "PlusIcon", "RefreshIcon", "CheckIcon", "CheckAllIcon",
  "XIcon", "EyeOffIcon", "SearchIcon", "LogoutIcon", "RssIcon", "SparkleIcon", "ListIcon",
  "StoriesIcon", "CardsIcon", "ChevronUpIcon", "TrashIcon", "MenuIcon",
] as const;

describe("icons", () => {
  it.each(ALL)("%s renders an svg", (name) => {
    const Icon = (icons as Record<string, React.FC<{ size?: number; className?: string }>>)[name];
    const { container } = render(<Icon size={20} className="cls" />);
    const svg = container.querySelector("svg");
    expect(svg).toBeInTheDocument();
    expect(svg).toHaveAttribute("width", "20");
    expect(svg).toHaveClass("cls");
  });

  it("defaults size to 16", () => {
    const { container } = render(<icons.RssIcon />);
    expect(container.querySelector("svg")).toHaveAttribute("width", "16");
  });

  it("BookmarkIcon fills when filled", () => {
    const { container } = render(<icons.BookmarkIcon filled />);
    expect(container.querySelector("svg")).toHaveAttribute("fill", "currentColor");
  });

  it("BookmarkIcon is unfilled by default", () => {
    const { container } = render(<icons.BookmarkIcon />);
    expect(container.querySelector("svg")).toHaveAttribute("fill", "none");
  });
});
