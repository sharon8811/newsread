import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync("app/globals.css", "utf8");

describe("mobile Safari styles", () => {
  it("keeps browser text adjustment predictable without disabling user zoom", () => {
    expect(css).toContain("-webkit-text-size-adjust: 100%");
    expect(css).toContain("text-size-adjust: 100%");
    expect(css).not.toMatch(/user-scalable|maximum-scale/i);
  });

  it("uses a phone-specific semantic type scale", () => {
    expect(css).toMatch(
      /@media \(max-width: 639px\)[\s\S]*--text-body-lg: 16px;[\s\S]*--text-title: 20px;/,
    );
  });

  it("prevents focused shared controls from triggering Safari auto-zoom", () => {
    expect(css).toMatch(
      /@media \(max-width: 639px\)[\s\S]*input,\s*select,\s*textarea\s*\{\s*font-size: 16px !important;/,
    );
  });
});
