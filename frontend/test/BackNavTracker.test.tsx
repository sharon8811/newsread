import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import BackNavTracker from "@/components/BackNavTracker";
import { navigateBack, resetBackNav } from "@/lib/backNav";

const { navState } = vi.hoisted(() => ({
  navState: { pathname: "/", search: "" },
}));
vi.mock("next/navigation", () => ({
  usePathname: () => navState.pathname,
  useSearchParams: () => new URLSearchParams(navState.search),
}));

function makeRouter() {
  return { back: vi.fn(), push: vi.fn() };
}

describe("<BackNavTracker>", () => {
  beforeEach(() => {
    resetBackNav();
    navState.pathname = "/";
    navState.search = "";
  });

  it("arms history back once it observes a client-side navigation", () => {
    const { rerender } = render(<BackNavTracker />);
    navState.pathname = "/article/1";
    rerender(<BackNavTracker />);

    const router = makeRouter();
    navigateBack(router);
    expect(router.back).toHaveBeenCalled();
  });

  it("keeps back as a fallback push when the page loaded straight on a detail URL", () => {
    navState.pathname = "/article/1";
    render(<BackNavTracker />);

    const router = makeRouter();
    navigateBack(router);
    expect(router.push).toHaveBeenCalledWith("/");
  });

  it("persists the last list URL, including the feed query, as the fallback", () => {
    navState.search = "feed=9";
    const { rerender } = render(<BackNavTracker />);
    navState.pathname = "/article/1";
    navState.search = "";
    rerender(<BackNavTracker />);

    resetBackNav(); // simulate the JS context dying with the discarded tab
    const router = makeRouter();
    navigateBack(router);
    expect(router.push).toHaveBeenCalledWith("/?feed=9");
  });

  it("counts browser back traversals down via popstate", () => {
    const { rerender } = render(<BackNavTracker />);
    navState.pathname = "/article/1";
    rerender(<BackNavTracker />);

    window.dispatchEvent(new PopStateEvent("popstate"));
    navState.pathname = "/";
    rerender(<BackNavTracker />);

    const router = makeRouter();
    navigateBack(router);
    expect(router.push).toHaveBeenCalledWith("/");
  });

  it("stops listening for popstate after unmount", () => {
    const { rerender, unmount } = render(<BackNavTracker />);
    navState.pathname = "/article/1";
    rerender(<BackNavTracker />);
    unmount();

    // A pop noted by a leaked listener would unwind the next tracked
    // navigation (depth 1 → 0) instead of counting it (depth 1 → 2).
    window.dispatchEvent(new PopStateEvent("popstate"));
    navState.pathname = "/saved";
    render(<BackNavTracker />);

    const router = makeRouter();
    navigateBack(router);
    expect(router.back).toHaveBeenCalled();
  });
});
