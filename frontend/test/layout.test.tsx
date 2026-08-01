import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import AppLayout from "@/app/(app)/layout";
import {
  clearReadingSessions,
  getReadingReturnAnchor,
  readingSessionKey,
  setReadingReturnAnchor,
} from "@/lib/readingSession";

const { pathnameState, replaceMock } = vi.hoisted(() => ({
  pathnameState: { value: "/" },
  replaceMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => pathnameState.value,
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: replaceMock }),
}));
const { authState } = vi.hoisted(() => ({
  authState: {
    user: { id: 1, username: "reader" } as unknown,
    ready: true,
    authed: true,
    suspended: false,
    logout: vi.fn(),
  },
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => authState }));
vi.mock("@/components/Sidebar", () => ({ default: () => <aside>Sidebar</aside> }));

describe("AppLayout reading return restoration", () => {
  beforeEach(() => {
    clearReadingSessions();
    pathnameState.value = "/";
    replaceMock.mockClear();
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      function (this: HTMLElement) {
        const scroller = document.querySelector("main") as HTMLElement | null;
        const top =
          this.tagName === "MAIN"
            ? 100
            : this.dataset.articleId
              ? 420 - (scroller?.scrollTop ?? 0)
              : 0;
        return {
          top,
          bottom: top + 80,
          left: 0,
          right: 800,
          width: 800,
          height: 80,
          x: 0,
          y: top,
          toJSON: () => ({}),
        };
      },
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("restores the latest article anchor after returning to a list route", () => {
    const key = readingSessionKey("unread");
    setReadingReturnAnchor(key, { articleId: 42, offset: 180 });

    const { container } = render(
      <AppLayout>
        <div data-article-id="42">Return row</div>
      </AppLayout>,
    );

    expect((container.querySelector("main") as HTMLElement).scrollTop).toBe(140);
    expect(getReadingReturnAnchor(key)).toBeNull();
  });

  it("does not consume the anchor while article detail is active", () => {
    pathnameState.value = "/article/42";
    const key = readingSessionKey("unread");
    setReadingReturnAnchor(key, { articleId: 42, offset: 180 });

    render(
      <AppLayout>
        <div data-article-id="42">Detail content</div>
      </AppLayout>,
    );

    expect(getReadingReturnAnchor(key)).toEqual({ articleId: 42, offset: 180 });
  });

  it("clears the article-return anchor on unrelated app navigation", () => {
    pathnameState.value = "/sent";
    const key = readingSessionKey("unread");
    setReadingReturnAnchor(key, { articleId: 42, offset: 180 });

    render(
      <AppLayout>
        <div>Sent content</div>
      </AppLayout>,
    );

    expect(getReadingReturnAnchor(key)).toBeNull();
  });
});

describe("AppLayout mobile bar", () => {
  beforeEach(() => {
    pathnameState.value = "/";
  });

  it("stands down on reading routes, which bring their own bar", () => {
    const { rerender } = render(<AppLayout>body</AppLayout>);
    expect(screen.queryByRole("link", { name: /NewsRead/ })).not.toBeInTheDocument();

    pathnameState.value = "/article/12";
    rerender(<AppLayout>body</AppLayout>);
    expect(screen.queryByRole("link", { name: /NewsRead/ })).not.toBeInTheDocument();
  });

  it("keeps its bar on every other route", () => {
    pathnameState.value = "/settings";
    render(<AppLayout>body</AppLayout>);
    expect(screen.getByRole("link", { name: /NewsRead/ })).toBeInTheDocument();
  });

  it("opens the nav drawer from that bar", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    pathnameState.value = "/settings";
    const { container } = render(<AppLayout>body</AppLayout>);
    const drawer = container.querySelector<HTMLElement>(".fixed.inset-y-0.left-0")!;
    expect(drawer).toHaveStyle({ transform: "translateX(-100%)" });
    await userEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(drawer).toHaveStyle({ transform: "translateX(0)" });
  });
});


describe("AppLayout suspended account", () => {
  beforeEach(() => {
    pathnameState.value = "/";
    authState.suspended = true;
  });
  afterEach(() => {
    authState.suspended = false;
  });

  it("replaces the app with the suspended screen", () => {
    render(<AppLayout>body</AppLayout>);
    expect(screen.getByText("Your account is suspended.")).toBeInTheDocument();
    expect(screen.queryByText("Sidebar")).not.toBeInTheDocument();
    expect(screen.queryByText("body")).not.toBeInTheDocument();
  });

  it("offers sign out", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    render(<AppLayout>body</AppLayout>);
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(authState.logout).toHaveBeenCalled();
  });
});
