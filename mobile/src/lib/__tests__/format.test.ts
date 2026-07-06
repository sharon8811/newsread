import { timeAgo } from "../format";

const now = new Date("2026-07-06T12:00:00Z");

describe("timeAgo", () => {
  it("handles null and invalid dates", () => {
    expect(timeAgo(null, now)).toBe("");
    expect(timeAgo("not-a-date", now)).toBe("");
  });

  it("formats recent times compactly", () => {
    expect(timeAgo("2026-07-06T11:59:30Z", now)).toBe("now");
    expect(timeAgo("2026-07-06T11:15:00Z", now)).toBe("45m");
    expect(timeAgo("2026-07-06T07:00:00Z", now)).toBe("5h");
    expect(timeAgo("2026-07-03T12:00:00Z", now)).toBe("3d");
  });

  it("falls back to a date beyond a week", () => {
    expect(timeAgo("2026-06-01T12:00:00Z", now)).toMatch(/Jun/);
    expect(timeAgo("2024-06-01T12:00:00Z", now)).toMatch(/2024/);
  });
});
