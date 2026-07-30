import { isRtl, textDirection } from "../rtl";

describe("isRtl", () => {
  it("detects Hebrew and Arabic", () => {
    expect(isRtl("ההצבעה בכנסת נדחתה בשבוע")).toBe(true);
    expect(isRtl("تأجيل التصويت لمدة أسبوع")).toBe(true);
  });

  it("leaves Latin, Cyrillic and CJK alone", () => {
    expect(isRtl("The vote was delayed")).toBe(false);
    expect(isRtl("Голосование отложено")).toBe(false);
    expect(isRtl("投票は延期された")).toBe(false);
  });

  it("skips neutral characters to find the first strong one", () => {
    // Leading digits, quotes and punctuation must not decide the direction.
    expect(isRtl('"2026: ההצבעה נדחתה"')).toBe(true);
    expect(isRtl("2026: the vote was delayed")).toBe(false);
  });

  it("follows the first strong character, not the majority", () => {
    // Same rule the browser's dir="auto" applies.
    expect(isRtl("ההצבעה delayed until next week, said the spokesperson")).toBe(true);
    expect(isRtl("Reuters: ההצבעה נדחתה")).toBe(false);
  });

  it("treats empty and missing text as left-to-right", () => {
    expect(isRtl("")).toBe(false);
    expect(isRtl(null)).toBe(false);
    expect(isRtl(undefined)).toBe(false);
    expect(isRtl("12345 — …")).toBe(false);
  });
});

describe("textDirection", () => {
  it("returns styles React Native understands", () => {
    expect(textDirection("שלום")).toEqual({ textAlign: "right", writingDirection: "rtl" });
    expect(textDirection("hello")).toEqual({ textAlign: "left", writingDirection: "ltr" });
  });
});
