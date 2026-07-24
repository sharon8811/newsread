const TEXT_FRAGMENT_DIRECTIVE = ":~:text=";

export interface TextFragmentAnchor {
  quote: string;
  prefix?: string | null;
  suffix?: string | null;
}

function encodeTextFragmentValue(value: string): string {
  // "-" is part of the text-fragment grammar but encodeURIComponent leaves it
  // untouched. Encoding it here prevents quote/context text becoming syntax.
  return encodeURIComponent(value).replace(/-/g, "%2D");
}

function normalizedAnchorPart(value: string | null | undefined): string {
  return value?.replace(/\s+/g, " ").trim() ?? "";
}

export function buildTextFragmentUrl(
  sourceUrl: string | null | undefined,
  anchor: TextFragmentAnchor,
): string | null {
  if (!sourceUrl) return null;

  let url: URL;
  try {
    url = new URL(sourceUrl);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;

  const quote = normalizedAnchorPart(anchor.quote);
  if (!quote) return null;

  const prefix = normalizedAnchorPart(anchor.prefix);
  const suffix = normalizedAnchorPart(anchor.suffix);
  const directive = [
    prefix ? `${encodeTextFragmentValue(prefix)}-,` : "",
    encodeTextFragmentValue(quote),
    suffix ? `,-${encodeTextFragmentValue(suffix)}` : "",
  ].join("");

  const authorFragment = url.hash
    .slice(1)
    .split(":~:", 1)[0]
    ?.replace(/&+$/, "");
  url.hash = `${authorFragment ? `${authorFragment}` : ""}${TEXT_FRAGMENT_DIRECTIVE}${directive}`;
  return url.toString();
}
