import { parseCitationData } from "./citation-navigation.js";
import type { CitationAnchor } from "./types.js";

interface RuntimeResponse {
  ok?: boolean;
  value?: unknown;
}

function citationLink(target: EventTarget | null): HTMLAnchorElement | null {
  return target instanceof Element
    ? target.closest<HTMLAnchorElement>("a[data-newsread-citation]")
    : null;
}

export async function handleCitationClick(
  event: MouseEvent,
  view: Window,
  runtime: Pick<typeof chrome.runtime, "sendMessage">,
): Promise<void> {
  if (
    !event.isTrusted ||
    event.button !== 0 ||
    event.defaultPrevented ||
    view.navigator.userActivation?.isActive === false
  ) {
    return;
  }

  const link = citationLink(event.target);
  if (!link) return;
  const citation = parseCitationData(
    link.dataset.newsreadCitation,
    link.href,
  );
  if (!citation) return;

  event.preventDefault();
  try {
    const response = (await runtime.sendMessage({
      type: "OPEN_CITATION",
      citation,
    })) as RuntimeResponse | undefined;
    if (response?.ok && response.value === true) return;
  } catch {
    // Fall through to the native link below.
  }
  view.open(link.href, "_blank", "noopener,noreferrer");
}

export async function requestPendingCitation(
  runtime: Pick<typeof chrome.runtime, "sendMessage">,
  retries = 3,
): Promise<CitationAnchor | null> {
  for (let attempt = 0; attempt < retries; attempt += 1) {
    try {
      const response = (await runtime.sendMessage({
        type: "GET_PENDING_CITATION",
      })) as RuntimeResponse | undefined;
      const value = response?.ok ? response.value : null;
      if (value && typeof value === "object") {
        const anchor = value as Partial<CitationAnchor>;
        if (
          typeof anchor.quote === "string" &&
          (anchor.prefix === null || typeof anchor.prefix === "string") &&
          (anchor.suffix === null || typeof anchor.suffix === "string")
        ) {
          return anchor as CitationAnchor;
        }
      }
    } catch {
      return null;
    }
    await new Promise((resolve) => setTimeout(resolve, 100 * (attempt + 1)));
  }
  return null;
}
