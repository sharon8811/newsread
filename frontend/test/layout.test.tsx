import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
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
  useRouter: () => ({ replace: replaceMock }),
}));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: { id: 1, username: "reader" }, ready: true }),
}));
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
});
