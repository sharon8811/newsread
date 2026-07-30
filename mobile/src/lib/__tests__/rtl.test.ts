import { textDirection } from "../rtl";

describe("textDirection", () => {
  it("returns styles React Native understands", () => {
    expect(textDirection(true)).toEqual({ textAlign: "right", writingDirection: "rtl" });
    expect(textDirection(false)).toEqual({ textAlign: "left", writingDirection: "ltr" });
  });

  it("treats an unknown direction as left to right", () => {
    // Articles detected before the language was recorded report nothing; the
    // overwhelmingly common answer is the safe floor.
    expect(textDirection(undefined)).toEqual({ textAlign: "left", writingDirection: "ltr" });
  });
});
