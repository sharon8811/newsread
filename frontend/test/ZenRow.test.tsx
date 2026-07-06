import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ZenRow from "@/components/ZenRow";
import { makeArticle } from "./fixtures";

const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }));

describe("<ZenRow>", () => {
  beforeEach(() => pushMock.mockClear());

  it("navigates on click", async () => {
    render(<ZenRow article={makeArticle()} index={0} selected={false} revealed={false} />);
    await userEvent.click(screen.getByText("A Great Article"));
    expect(pushMock).toHaveBeenCalledWith("/article/1");
  });

  it("shows the domain and time", () => {
    render(<ZenRow article={makeArticle()} index={0} selected={false} revealed={false} />);
    expect(screen.getByText(/site\.example/)).toBeInTheDocument();
  });

  it("omits time when unpublished", () => {
    render(<ZenRow article={makeArticle({ published_at: null })} index={0} selected={false} revealed={false} />);
    expect(screen.getByText("site.example")).toBeInTheDocument();
  });

  it("renders the short summary when present", () => {
    render(<ZenRow article={makeArticle({ summary_short: "the gist" })} index={0} selected revealed />);
    expect(screen.getByText("the gist")).toBeInTheDocument();
  });

  it("has no summary paragraph without a short summary", () => {
    const { container } = render(<ZenRow article={makeArticle()} index={0} selected={false} revealed={false} />);
    expect(container.querySelector("p")).toBeNull();
  });
});
