import { describe, it, expect, vi, afterEach } from "vitest";
import { timeAgo, humanCount, domainOf, describeLink, formatDuration } from "@/lib/format";

describe("formatDuration", () => {
  it("shows zero as 0m", () => {
    expect(formatDuration(0)).toBe("0m");
  });

  it("shows sub-minute values in seconds", () => {
    expect(formatDuration(45)).toBe("45s");
  });

  it("shows minutes", () => {
    expect(formatDuration(12 * 60)).toBe("12m");
  });

  it("rounds 59.5+ minutes up into hours", () => {
    expect(formatDuration(3599)).toBe("1h");
  });

  it("shows hours with a minute remainder", () => {
    expect(formatDuration(2 * 3600 + 14 * 60)).toBe("2h 14m");
  });

  it("drops a zero remainder", () => {
    expect(formatDuration(3 * 3600)).toBe("3h");
  });
});

describe("timeAgo", () => {
  afterEach(() => vi.useRealTimers());

  it("returns empty string for null", () => {
    expect(timeAgo(null)).toBe("");
  });

  it("formats recent times", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2024-01-01T12:00:00Z"));
    expect(timeAgo("2024-01-01T11:59:30Z")).toBe("just now");
    expect(timeAgo("2024-01-01T11:30:00Z")).toBe("30m ago");
    expect(timeAgo("2024-01-01T09:00:00Z")).toBe("3h ago");
  });

  it("formats days, weeks and years", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2024-01-15T12:00:00Z"));
    expect(timeAgo("2024-01-12T12:00:00Z")).toBe("3d ago");
    expect(timeAgo("2024-01-01T12:00:00Z")).toBe("2w ago");
    expect(timeAgo("2022-01-15T12:00:00Z")).toBe("2y ago");
  });

  it("clamps future times to just now", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2024-01-01T12:00:00Z"));
    expect(timeAgo("2024-01-01T13:00:00Z")).toBe("just now");
  });
});

describe("humanCount", () => {
  it("returns empty string for null/undefined", () => {
    expect(humanCount(null)).toBe("");
    expect(humanCount(undefined)).toBe("");
  });

  it("formats small numbers as-is", () => {
    expect(humanCount(0)).toBe("0");
    expect(humanCount(999)).toBe("999");
  });

  it("formats thousands", () => {
    expect(humanCount(1500)).toBe("1.5k");
    expect(humanCount(15000)).toBe("15k");
  });

  it("formats millions and billions", () => {
    expect(humanCount(2_500_000)).toBe("2.5M");
    expect(humanCount(3_000_000_000)).toBe("3.0B");
  });
});

describe("domainOf", () => {
  it("strips www and returns hostname", () => {
    expect(domainOf("https://www.example.com/path")).toBe("example.com");
    expect(domainOf("https://sub.example.com")).toBe("sub.example.com");
  });

  it("returns the input for invalid URLs", () => {
    expect(domainOf("not a url")).toBe("not a url");
  });
});

describe("describeLink", () => {
  it("labels GitHub PRs and issues by repo and number", () => {
    expect(describeLink("https://github.com/org/repo/pull/42")).toEqual({
      kind: "github",
      label: "repo#42",
    });
    expect(describeLink("https://github.com/org/repo/issues/7")).toEqual({
      kind: "github",
      label: "repo#7",
    });
    expect(describeLink("https://github.com/org/repo")).toEqual({
      kind: "github",
      label: "GitHub",
    });
  });

  it("recognizes YouTube on both hosts", () => {
    expect(describeLink("https://www.youtube.com/watch?v=x").kind).toBe("youtube");
    expect(describeLink("https://youtu.be/x").label).toBe("YouTube");
  });

  it("falls back to the hostname, or the raw string when unparsable", () => {
    expect(describeLink("https://example.com/a")).toEqual({ kind: "link", label: "example.com" });
    expect(describeLink("not a url")).toEqual({ kind: "link", label: "not a url" });
  });
});
