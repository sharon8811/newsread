import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import {
  FLUSH_INTERVAL_S,
  IDLE_AFTER_MS,
  localDay,
  useReadingTimer,
} from "@/lib/useReadingTimer";
import { setToken } from "@/lib/api";

function mockFetch() {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", {
    value: state,
    configurable: true,
  });
  document.dispatchEvent(new Event("visibilitychange"));
}

const wake = () => window.dispatchEvent(new Event("pointermove"));

function heartbeatSeconds(fetchMock: ReturnType<typeof vi.fn>): number[] {
  return fetchMock.mock.calls.map(
    (call) => JSON.parse((call[1] as RequestInit).body as string).seconds,
  );
}

describe("localDay", () => {
  it("formats a date in local time as YYYY-MM-DD", () => {
    expect(localDay(new Date(2026, 0, 5, 23, 59))).toBe("2026-01-05");
  });
});

describe("useReadingTimer", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setVisibility("visible");
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("flushes a heartbeat after the flush interval", () => {
    const fetchMock = mockFetch();
    setToken("tok");
    renderHook(() => useReadingTimer(7));

    vi.advanceTimersByTime(FLUSH_INTERVAL_S * 1000);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/activity/heartbeat");
    expect((init as RequestInit).keepalive).toBe(true);
    expect(
      (init as RequestInit).headers as Record<string, string>,
    ).toMatchObject({ Authorization: "Bearer tok" });
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      article_id: 7,
      seconds: FLUSH_INTERVAL_S,
      source: "web",
      day: localDay(),
    });
  });

  it("does nothing without an article id", () => {
    const fetchMock = mockFetch();
    renderHook(() => useReadingTimer(undefined));
    vi.advanceTimersByTime(FLUSH_INTERVAL_S * 1000);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("flushes on hide and does not count while hidden", () => {
    const fetchMock = mockFetch();
    const { unmount } = renderHook(() => useReadingTimer(7));

    vi.advanceTimersByTime(10_000);
    setVisibility("hidden");
    expect(heartbeatSeconds(fetchMock)).toEqual([10]);

    vi.advanceTimersByTime(60_000); // hidden: nothing accrues
    setVisibility("visible");
    wake();
    vi.advanceTimersByTime(5_000);
    unmount();

    expect(heartbeatSeconds(fetchMock)).toEqual([10, 5]);
  });

  it("does not count while the window is unfocused", () => {
    const fetchMock = mockFetch();
    vi.spyOn(document, "hasFocus").mockReturnValue(false);
    renderHook(() => useReadingTimer(7));
    vi.advanceTimersByTime(FLUSH_INTERVAL_S * 2000);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("pauses when idle and resumes on input", () => {
    const fetchMock = mockFetch();
    const { unmount } = renderHook(() => useReadingTimer(7));

    // No input after mount: counting stops once the idle threshold passes,
    // no matter how long the tab stays visible and focused.
    vi.advanceTimersByTime(IDLE_AFTER_MS + 120_000);
    wake();
    vi.advanceTimersByTime(5_000);
    unmount();

    const total = heartbeatSeconds(fetchMock).reduce((a, b) => a + b, 0);
    expect(total).toBe(IDLE_AFTER_MS / 1000 - 1 + 5);
  });

  it("flushes the remainder on unmount", () => {
    const fetchMock = mockFetch();
    const { unmount } = renderHook(() => useReadingTimer(7));
    vi.advanceTimersByTime(12_000);
    unmount();
    expect(heartbeatSeconds(fetchMock)).toEqual([12]);
  });

  it("flushes on pagehide", () => {
    const fetchMock = mockFetch();
    renderHook(() => useReadingTimer(7));
    vi.advanceTimersByTime(8_000);
    window.dispatchEvent(new Event("pagehide"));
    expect(heartbeatSeconds(fetchMock)).toEqual([8]);
  });

  it("skips the flush when nothing accrued", () => {
    const fetchMock = mockFetch();
    const { unmount } = renderHook(() => useReadingTimer(7));
    unmount();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns failed seconds to the pending pool", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("offline"));
    vi.stubGlobal("fetch", fetchMock);
    const { unmount } = renderHook(() => useReadingTimer(7));

    vi.advanceTimersByTime(FLUSH_INTERVAL_S * 1000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await Promise.resolve(); // let the rejection handler restore `pending`
    await Promise.resolve();

    fetchMock.mockResolvedValue({ ok: true, status: 204 });
    wake();
    vi.advanceTimersByTime(FLUSH_INTERVAL_S * 1000);
    // The second flush carries the restored 30s plus the fresh 30s.
    expect(heartbeatSeconds(fetchMock)).toEqual([
      FLUSH_INTERVAL_S,
      FLUSH_INTERVAL_S * 2,
    ]);
    unmount();
  });
});
