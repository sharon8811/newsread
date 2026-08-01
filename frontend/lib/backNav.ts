// In-app back buttons cannot blindly call router.back(): when iOS discards a
// background tab and later restores it directly on a detail URL, the restored
// context has no in-app history entry behind it and back() is a silent no-op
// (issue #128). This module keeps what's needed to make back reliable:
//  - a count of in-app history entries behind the current one, tracked from
//    client-side navigations observed in THIS JavaScript context. The counter
//    dies with the context — exactly when history stops being trustworthy.
//  - the last non-detail URL the user visited, persisted in sessionStorage
//    (which iOS restores with the tab) as the place a detail page returns to
//    when real history is unusable.
// Miscounts from exotic flows (forward button, replace navigations) are
// biased downward on purpose: under-counting turns back into a push to the
// originating list, over-counting would recreate the dead button.

const FALLBACK_KEY = "newsread.back-fallback";

// Detail pages are the ones with in-app back buttons; they never serve as a
// fallback target — falling back onto another detail page could strand the
// user on a chain of unreturnable pages.
const DETAIL_PREFIXES = ["/article/", "/entity/", "/history/documents/"];

let lastTrackedUrl: string | null = null;
let historyDepth = 0;
let popPending = false;

export function isDetailPath(pathname: string): boolean {
  return DETAIL_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

/** A popstate fired: the next URL change is history traversal, not a push. */
export function noteHistoryPop() {
  popPending = true;
}

/** Record the current location. Called on every pathname/search commit; the
 * first call after a page load is the baseline, not a navigation. */
export function trackBackNav(pathname: string, search: string) {
  const url = `${pathname}${search ? `?${search}` : ""}`;
  if (url === lastTrackedUrl) return;
  if (lastTrackedUrl !== null) {
    historyDepth = popPending ? Math.max(0, historyDepth - 1) : historyDepth + 1;
  }
  popPending = false;
  lastTrackedUrl = url;
  if (!isDetailPath(pathname)) {
    try {
      sessionStorage.setItem(FALLBACK_KEY, url);
    } catch {
      // Storage unavailable — navigateBack's default covers it.
    }
  }
}

/** Back that survives a discarded/restored tab: real history back while this
 * context knows an in-app entry exists behind us, otherwise a push to the
 * originating list. */
export function navigateBack(
  router: { back: () => void; push: (href: string) => void },
  defaultFallback = "/",
) {
  if (historyDepth > 0) {
    router.back();
    return;
  }
  let fallback: string | null = null;
  try {
    fallback = sessionStorage.getItem(FALLBACK_KEY);
  } catch {
    // Storage unavailable — use the default.
  }
  router.push(fallback ?? defaultFallback);
}

export function resetBackNav() {
  lastTrackedUrl = null;
  historyDepth = 0;
  popPending = false;
}
