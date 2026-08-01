import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  isDetailPath,
  navigateBack,
  noteHistoryPop,
  resetBackNav,
  trackBackNav,
} from "@/lib/backNav";

function makeRouter() {
  return { back: vi.fn(), push: vi.fn() };
}

describe("backNav", () => {
  beforeEach(() => {
    resetBackNav();
  });

  it("classifies detail paths", () => {
    expect(isDetailPath("/article/12")).toBe(true);
    expect(isDetailPath("/entity/7")).toBe(true);
    expect(isDetailPath("/history/documents/3")).toBe(true);
    expect(isDetailPath("/")).toBe(false);
    expect(isDetailPath("/history")).toBe(false);
    expect(isDetailPath("/saved")).toBe(false);
  });

  it("pushes the default fallback in a fresh context with no stored origin", () => {
    const router = makeRouter();
    navigateBack(router);
    expect(router.push).toHaveBeenCalledWith("/");
    expect(router.back).not.toHaveBeenCalled();
  });

  it("honors a caller-supplied default fallback", () => {
    const router = makeRouter();
    navigateBack(router, "/history");
    expect(router.push).toHaveBeenCalledWith("/history");
  });

  it("prefers the persisted originating list over the default", () => {
    // A restored tab: sessionStorage survived, module state did not.
    trackBackNav("/", "feed=3");
    resetBackNav();
    const router = makeRouter();
    navigateBack(router, "/history");
    expect(router.push).toHaveBeenCalledWith("/?feed=3");
  });

  it("uses real history back once a client-side navigation happened", () => {
    trackBackNav("/", "");
    trackBackNav("/article/1", "");
    const router = makeRouter();
    navigateBack(router);
    expect(router.back).toHaveBeenCalled();
    expect(router.push).not.toHaveBeenCalled();
  });

  it("treats the first observed URL as a baseline, not a navigation", () => {
    trackBackNav("/article/1", "");
    const router = makeRouter();
    navigateBack(router);
    expect(router.push).toHaveBeenCalledWith("/");
  });

  it("ignores repeat commits of the same URL (StrictMode double effects)", () => {
    trackBackNav("/", "");
    trackBackNav("/", "");
    const router = makeRouter();
    navigateBack(router);
    expect(router.push).toHaveBeenCalledWith("/");
  });

  it("unwinds depth on history pops so a drained stack falls back", () => {
    trackBackNav("/", "");
    trackBackNav("/article/1", "");
    noteHistoryPop();
    trackBackNav("/", "");
    const router = makeRouter();
    navigateBack(router);
    expect(router.push).toHaveBeenCalledWith("/");
  });

  it("never counts depth below zero", () => {
    trackBackNav("/article/1", "");
    noteHistoryPop();
    trackBackNav("/article/2", "");
    // The pop consumed the (nonexistent) entry: depth stays 0.
    const router = makeRouter();
    navigateBack(router);
    expect(router.push).toHaveBeenCalledWith("/");
  });

  it("does not record detail pages as the fallback origin", () => {
    trackBackNav("/saved", "");
    trackBackNav("/article/1", "");
    resetBackNav();
    const router = makeRouter();
    navigateBack(router);
    expect(router.push).toHaveBeenCalledWith("/saved");
  });

  it("survives storage writes failing", () => {
    const setItem = vi.spyOn(sessionStorage, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    expect(() => trackBackNav("/", "")).not.toThrow();
    setItem.mockRestore();
  });

  it("survives storage reads failing", () => {
    const getItem = vi.spyOn(sessionStorage, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    const router = makeRouter();
    navigateBack(router);
    expect(router.push).toHaveBeenCalledWith("/");
    getItem.mockRestore();
  });
});
