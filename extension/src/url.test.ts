import { describe, expect, it } from "vitest";
import {
  hostnameMatches,
  normalizeCaptureUrl,
  normalizeHostname,
  permissionPattern,
} from "./url.js";

describe("capture URL policy", () => {
  it("normalizes safe URLs and removes tracking, fragments, and secrets", () => {
    const url = normalizeCaptureUrl(
      "https://Example.COM/story?utm_source=x&topic=ai&token=secret#part",
    );
    expect(url?.href).toBe("https://example.com/story?topic=ai");
  });

  it.each([
    "http://localhost/page",
    "http://intranet/page",
    "http://127.0.0.1/page",
    "https://mail.internal/page",
    "javascript:alert(1)",
  ])("rejects non-public capture URL %s", (value) => {
    expect(normalizeCaptureUrl(value)).toBeNull();
  });

  it("normalizes exclusion domains and matches subdomains", () => {
    expect(normalizeHostname("*.Private.Example.com.")).toBe(
      "private.example.com",
    );
    expect(hostnameMatches("mail.private.example.com", "private.example.com")).toBe(
      true,
    );
    expect(
      hostnameMatches("mail.private.example.com", "private.example.com", false),
    ).toBe(false);
  });

  it("requests only the selected NewsRead origin", () => {
    expect(permissionPattern("https://news.example.com/settings")).toBe(
      "https://news.example.com/*",
    );
    expect(() => permissionPattern("file:///tmp/newsread")).toThrow();
  });
});
