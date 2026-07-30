/** `dir` for content whose language the server already detected.
 *
 * Deliberately not `dir="auto"`: the browser resolves that on the first strong
 * character, so "OpenAI משיקה יכולות פרסום בתוך ChatGPT" — a Hebrew sentence
 * opening with a Latin brand name — lays out left to right. The backend runs a
 * real language detector once per article and sends the answer, so there is
 * nothing to guess at here.
 *
 * `rtl` is false for articles detected before the language was recorded, which
 * read left to right until something re-detects them. That is the same answer
 * `dir="auto"` gives for the overwhelmingly common case, so it is a safe floor.
 */
export function dirOf(rtl: boolean | undefined): "rtl" | "ltr" {
  return rtl ? "rtl" : "ltr";
}
