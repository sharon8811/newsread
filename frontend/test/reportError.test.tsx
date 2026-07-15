import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import ErrorReporting from "@/components/ErrorReporting";
import {
  registerGlobalErrorReporting,
  reportClientError,
  resetErrorReportingForTests,
} from "@/lib/reportError";

describe("reportClientError", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    resetErrorReportingForTests();
    fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);
  });

  it("posts the error with stack, context and digest, keepalive on", () => {
    reportClientError(new Error("kaboom"), "error-boundary", "digest-1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/client-errors");
    expect(init.keepalive).toBe(true);
    const body = JSON.parse(String(init.body));
    expect(body).toMatchObject({
      message: "kaboom",
      context: "error-boundary",
      digest: "digest-1",
    });
    expect(body.stack).toContain("kaboom");
    expect(body.url).toContain("http");
  });

  it("wraps non-Error values", () => {
    reportClientError("string failure");
    const body = JSON.parse(String(fetchMock.mock.calls[0][1].body));
    expect(body.message).toBe("string failure");
    expect(body.context).toBeNull();
  });

  it("handles null errors and missing stacks", () => {
    reportClientError(null);
    const first = JSON.parse(String(fetchMock.mock.calls[0][1].body));
    expect(first.message).toBe("unknown");

    const bare = new Error("no stack attached");
    bare.stack = undefined;
    reportClientError(bare);
    const second = JSON.parse(String(fetchMock.mock.calls[1][1].body));
    expect(second.stack).toBeNull();
  });

  it("dedupes repeats of the same error", () => {
    reportClientError(new Error("same"));
    reportClientError(new Error("same"));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("caps the number of reports per session", () => {
    for (let i = 0; i < 30; i += 1) reportClientError(new Error(`distinct-${i}`));
    expect(fetchMock).toHaveBeenCalledTimes(20);
  });

  it("never throws, even when fetch itself does", () => {
    fetchMock.mockImplementation(() => {
      throw new Error("fetch exploded");
    });
    expect(() => reportClientError(new Error("x"))).not.toThrow();
  });

  it("registers window listeners only once and reports from both", () => {
    registerGlobalErrorReporting();
    registerGlobalErrorReporting(); // idempotent — no double reports below
    window.dispatchEvent(new ErrorEvent("error", { error: new Error("boom-1") }));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const rejection = Object.assign(new Event("unhandledrejection"), {
      reason: new Error("boom-2"),
    });
    window.dispatchEvent(rejection);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("falls back to the event message when an error event has no error object", () => {
    registerGlobalErrorReporting();
    window.dispatchEvent(new ErrorEvent("error", { message: "script blew up" }));
    const body = JSON.parse(String(fetchMock.mock.calls[0][1].body));
    expect(body.message).toBe("script blew up");
  });
});

describe("<ErrorReporting>", () => {
  it("wires the global listeners on mount", () => {
    resetErrorReportingForTests();
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);
    render(<ErrorReporting />);
    window.dispatchEvent(new ErrorEvent("error", { error: new Error("mounted") }));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
