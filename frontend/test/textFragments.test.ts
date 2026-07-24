import { describe, expect, it } from "vitest";
import { buildTextFragmentUrl } from "@/lib/textFragments";

describe("buildTextFragmentUrl", () => {
  it("encodes quote punctuation and grammar delimiters", () => {
    expect(
      buildTextFragmentUrl("https://example.com/article", {
        quote: "state-of-the-art, safe & sound",
      }),
    ).toBe(
      "https://example.com/article#:~:text=state%2Dof%2Dthe%2Dart%2C%20safe%20%26%20sound",
    );
  });

  it("adds encoded prefix and suffix context", () => {
    expect(
      buildTextFragmentUrl("https://example.com/article", {
        quote: "the result",
        prefix: "In section-2",
        suffix: "was repeated, twice",
      }),
    ).toBe(
      "https://example.com/article#:~:text=In%20section%2D2-,the%20result,-was%20repeated%2C%20twice",
    );
  });

  it("preserves a safe author fragment before the directive", () => {
    expect(
      buildTextFragmentUrl("https://example.com/article#conclusion", {
        quote: "Final result",
      }),
    ).toBe(
      "https://example.com/article#conclusion:~:text=Final%20result",
    );
  });

  it("replaces an existing text directive without nesting it", () => {
    expect(
      buildTextFragmentUrl(
        "https://example.com/article#section:~:text=old",
        { quote: "new" },
      ),
    ).toBe("https://example.com/article#section:~:text=new");
  });

  it("supports RTL text", () => {
    expect(
      buildTextFragmentUrl("https://example.com/he", {
        quote: "שלום עולם",
      }),
    ).toBe(
      `https://example.com/he#:~:text=${encodeURIComponent("שלום עולם")}`,
    );
  });

  it.each([
    [null, { quote: "text" }],
    ["javascript:alert(1)", { quote: "text" }],
    ["https://example.com", { quote: "   " }],
  ])("rejects unsafe or incomplete input", (url, anchor) => {
    expect(buildTextFragmentUrl(url, anchor)).toBeNull();
  });
});
