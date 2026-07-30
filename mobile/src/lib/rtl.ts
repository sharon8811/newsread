// Per-text direction for article content. The web gets this from dir="auto";
// React Native has no equivalent (writingDirection is iOS-only and there is no
// cross-platform "auto"), so the same first-strong-character rule the browser
// applies is implemented here.
//
// Only content gets a direction — the app's own chrome stays left-to-right.

// Hebrew, Arabic (+ supplement and extended), Syriac, Thaana, NKo, and the
// Hebrew/Arabic presentation forms.
//
// Two deliberate holes, so this agrees with the browser's dir="auto": the
// Arabic-Indic digits (U+0660-0669, U+06F0-06F9) are weak, not strong — a
// headline numbered in them is still whatever script follows — and U+FEFF at
// the end of the presentation-forms block is the byte-order mark, which is
// invisible and must never decide a paragraph's direction.
const RTL_RANGES =
  "\\u0590-\\u05FF\\u0600-\\u065F\\u066A-\\u06EF\\u06FA-\\u07FF" +
  "\\u08A0-\\u08FF\\uFB1D-\\uFDFF\\uFE70-\\uFEFE";
// Strong left-to-right letters we actually see: Latin (incl. accents), Greek,
// Cyrillic, Devanagari, CJK, kana and Hangul.
const LTR_RANGES =
  "A-Za-z\\u00C0-\\u024F\\u0370-\\u03FF\\u0400-\\u04FF\\u0900-\\u097F" +
  "\\u3040-\\u30FF\\u4E00-\\u9FFF\\uAC00-\\uD7AF";

// Digits, punctuation, quotes and whitespace are neutral: they are skipped so
// a headline like "2026: ההצבעה נדחתה" still reads as right-to-left.
const STRONG = new RegExp(`[${RTL_RANGES}${LTR_RANGES}]`);
const RTL = new RegExp(`[${RTL_RANGES}]`);

export function isRtl(text: string | null | undefined): boolean {
  if (!text) return false;
  const match = STRONG.exec(text);
  return match !== null && RTL.test(match[0]);
}

/** Text styles that lay a string out in its own direction. Spread onto a
 * <Text> (or a markdown body style) that renders publisher or model text. */
export function textDirection(text: string | null | undefined): {
  textAlign: "left" | "right";
  writingDirection: "ltr" | "rtl";
} {
  return isRtl(text)
    ? { textAlign: "right", writingDirection: "rtl" }
    : { textAlign: "left", writingDirection: "ltr" };
}
