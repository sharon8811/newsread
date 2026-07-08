import { AppState } from "react-native";

import { api } from "../api";
import { FLUSH_INTERVAL_S, localDay, startReadingTracker } from "../useReadingTimer";

jest.mock("expo-router", () => ({ useFocusEffect: jest.fn() }));
jest.mock("../api", () => ({ api: jest.fn() }));

const apiMock = api as jest.MockedFunction<typeof api>;

type AppStateListener = (state: string) => void;
let appStateListeners: AppStateListener[] = [];
let removeSpy: jest.Mock;

function setAppState(state: string) {
  Object.defineProperty(AppState, "currentState", { value: state, configurable: true });
}

function heartbeatSeconds(): number[] {
  return apiMock.mock.calls.map(
    ([, opts]) => ((opts as { body: { seconds: number } }).body).seconds,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.useFakeTimers();
  apiMock.mockResolvedValue(undefined as never);
  appStateListeners = [];
  removeSpy = jest.fn();
  setAppState("active");
  jest.spyOn(AppState, "addEventListener").mockImplementation(((
    _type: string,
    listener: AppStateListener,
  ) => {
    appStateListeners.push(listener);
    return { remove: removeSpy };
  }) as never);
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

describe("localDay", () => {
  it("formats a date in local time as YYYY-MM-DD", () => {
    expect(localDay(new Date(2026, 0, 5, 23, 59))).toBe("2026-01-05");
  });
});

describe("startReadingTracker", () => {
  it("heartbeats after the flush interval", () => {
    const stop = startReadingTracker(7);
    jest.advanceTimersByTime(FLUSH_INTERVAL_S * 1000);

    expect(apiMock).toHaveBeenCalledWith("/activity/heartbeat", {
      method: "POST",
      body: { article_id: 7, seconds: FLUSH_INTERVAL_S, source: "mobile", day: localDay() },
    });
    stop();
  });

  it("does not count while the app is backgrounded", () => {
    const stop = startReadingTracker(7);
    setAppState("background");
    jest.advanceTimersByTime(FLUSH_INTERVAL_S * 2000);
    setAppState("active");
    jest.advanceTimersByTime(5000);
    stop();

    expect(heartbeatSeconds()).toEqual([5]);
  });

  it("flushes when the app leaves the foreground", () => {
    const stop = startReadingTracker(7);
    jest.advanceTimersByTime(9000);
    appStateListeners.forEach((listener) => listener("background"));

    expect(heartbeatSeconds()).toEqual([9]);
    stop();
  });

  it("does not flush on a foreground-to-foreground state change", () => {
    const stop = startReadingTracker(7);
    jest.advanceTimersByTime(9000);
    appStateListeners.forEach((listener) => listener("active"));
    expect(apiMock).not.toHaveBeenCalled();
    stop();
  });

  it("flushes the remainder and unsubscribes on stop", () => {
    const stop = startReadingTracker(7);
    jest.advanceTimersByTime(12_000);
    stop();

    expect(heartbeatSeconds()).toEqual([12]);
    expect(removeSpy).toHaveBeenCalled();
  });

  it("skips the flush when nothing accrued", () => {
    const stop = startReadingTracker(7);
    stop();
    expect(apiMock).not.toHaveBeenCalled();
  });

  it("returns failed seconds to the pending pool", async () => {
    apiMock.mockRejectedValueOnce(new Error("offline") as never);
    const stop = startReadingTracker(7);

    jest.advanceTimersByTime(FLUSH_INTERVAL_S * 1000);
    expect(apiMock).toHaveBeenCalledTimes(1);
    await Promise.resolve(); // let the rejection handler restore `pending`
    await Promise.resolve();

    jest.advanceTimersByTime(FLUSH_INTERVAL_S * 1000);
    expect(heartbeatSeconds()).toEqual([FLUSH_INTERVAL_S, FLUSH_INTERVAL_S * 2]);
    stop();
  });
});
