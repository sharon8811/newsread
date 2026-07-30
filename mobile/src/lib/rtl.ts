// Text styles for content whose direction the server already determined.
//
// React Native has no dir="auto", but it does not need one: the backend runs a
// real language detector (lingua) once per article and sends `rtl` with it, so
// there is nothing to infer here. Guessing from the text would get "OpenAI
// משיקה יכולות פרסום" wrong anyway — a Hebrew sentence opening with a Latin
// brand name — which is exactly the bug this replaced.

export type TextDirection = {
  textAlign: "left" | "right";
  writingDirection: "ltr" | "rtl";
};

/** Spread onto a <Text> (or a markdown body style) rendering article text. */
export function textDirection(rtl: boolean | undefined): TextDirection {
  return rtl
    ? { textAlign: "right", writingDirection: "rtl" }
    : { textAlign: "left", writingDirection: "ltr" };
}
