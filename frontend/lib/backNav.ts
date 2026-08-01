// In-app back buttons cannot blindly call router.back(): when iOS discards a
// background tab and later restores it directly on a detail URL, the restored
// context has no in-app history entry behind it and back() is a silent no-op
// (issue #128). This module keeps what's needed to make back reliable:
//  - a count of in-app history entries behind the current one, tracked from
//    client-side navigations observed in THIS JavaScript context and mirrored
//    onto the history entry's own state, so an ordinary reload re-seeds it. A
//    restored discarded tab gets a fresh entry with no marker — exactly when
//    history stops being trustworthy.
//  - the last non-detail URL the user visited, persisted in sessionStorage
//    (which iOS restores with the tab) as the place a detail page returns to
//    when real history is unusable.
// Miscounts from exotic flows (forward button, replace navigations) are
// biased downward on purpose: under-counting turns back into a push to the
// originating list, over-counting would recreate the dead button.

const FALLBACK_KEY = "newsread.back-fallback";

// The depth also rides on the current history entry's own state, so an
// ordinary reload (history intact, module state gone) re-seeds instead of
// degrading to the fallback. A genuinely fresh entry — restored tab, shared
// link — has no marker and correctly starts at zero. Exported for tests.
export const BACK_NAV_STATE_KEY = "__newsreadBackDepth";

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

function readStateDepth(): number | null {
  try {
    const value = (window.history.state ?? {})[BACK_NAV_STATE_KEY];
    return typeof value === "number" ? value : null;
  } catch {
    return null;
  }
}

function writeStateDepth(depth: number) {
  try {
    // Merge, don't replace: Next keeps its own router state in the entry.
    window.history.replaceState(
      { ...(window.history.state ?? {}), [BACK_NAV_STATE_KEY]: depth },
      "",
      window.location.href,
    );
  } catch {
    // Reloads on this entry degrade to the fallback push.
  }
}

/** Record the current location. Called on every pathname/search commit; the
 * first call after a page load is the baseline, not a navigation. */
export function trackBackNav(pathname: string, search: string) {
  const url = `${pathname}${search ? `?${search}` : ""}`;
  if (url === lastTrackedUrl) return;
  if (lastTrackedUrl === null) {
    // Page load: an ordinary reload keeps browser history usable, and the
    // entry's own state survives it — restore the depth from there.
    historyDepth = readStateDepth() ?? 0;
  } else {
    historyDepth = popPending ? Math.max(0, historyDepth - 1) : historyDepth + 1;
  }
  popPending = false;
  lastTrackedUrl = url;
  writeStateDepth(historyDepth);
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

/** Full fresh-tab semantics for tests: module state AND the current entry's
 * depth marker are gone, exactly like a discarded-and-restored tab. */
export function resetBackNav() {
  lastTrackedUrl = null;
  historyDepth = 0;
  popPending = false;
  try {
    const state = { ...(window.history.state ?? {}) };
    delete state[BACK_NAV_STATE_KEY];
    window.history.replaceState(state, "", window.location.href);
  } catch {
    // No history to scrub.
  }
}
