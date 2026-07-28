"use client";

import { useEffect, useRef } from "react";

// Assisted scrolling: one deliberate gesture moves the cards reading view by
// exactly one article, and the article it lands on is aligned under the sticky
// header.
//
// Two mechanisms, because no single one covers every input device:
//
// - CSS scroll snapping (`y mandatory` + `scroll-snap-stop: always` per card)
//   owns touch and any scroll the app performs itself. `always` is what stops
//   a hard flick on a phone from flying past three articles: the browser is
//   not allowed to pass over a snap position within one scrolling operation.
// - A wheel handler owns mouse wheels and trackpads. Mandatory snapping alone
//   makes a wheel tick feel broken — a tick that moves less than half a card
//   gets snapped straight back to where it started — so wheel gestures are
//   translated into explicit one-article steps instead.
// - A touch clamp covers the hole in the first mechanism: Chrome honours
//   `scroll-snap-stop` for wheel and keyboard scrolling but not for a touch
//   fling, which happily coasts past several cards (verified on a phone
//   viewport). So a touch gesture is fenced at the first snap position beyond
//   where it started, and the fling is cut short there.
//
// Articles taller than the reading viewport are never trapped: the step logic
// hands scrolling back to the browser until the far edge of such a card is
// reached (CSS snapping does the same by spec, since an oversized snap area
// captures the snapport).

/** Marks a scroll-snap target inside the reading list. */
export const SNAP_ITEM_ATTR = "data-snap-item";

/** Trackpads open a swipe with a trickle of 1–3px deltas; a gesture has to
 * push at least this far before it counts as a deliberate step. */
export const WHEEL_STEP_THRESHOLD = 24;
/** A gesture is over once the wheel has been quiet this long. Trackpad inertia
 * keeps delivering events well after the fingers lift, and holding the step
 * lock until silence is what keeps one flick to one article. */
export const GESTURE_QUIET_MS = 160;
/** A wheel that never goes quiet (someone spinning a mouse wheel, or a long
 * inertia tail) still has to earn each further step: this much time and this
 * much extra delta since the previous one. Sized so steady spinning advances
 * at a readable pace while an inertia tail, which decays fast, mostly dies out
 * before it can buy a second article. */
export const HELD_STEP_INTERVAL_MS = 450;
export const HELD_STEP_THRESHOLD = 150;
/** How long a touch fence outlives the finger. Momentum keeps the scroller
 * moving after touchend, and the fence has to survive the whole coast. */
export const TOUCH_SETTLE_MS = 1200;
/** Opt an element out of the touch fence: taps on the jump pills scroll the
 * list on purpose, sometimes by many articles. */
export const SCROLL_JUMP_ATTR = "data-scroll-jump";

export type StepPlan = { top: number };

function snapItems(scroller: HTMLElement): HTMLElement[] {
  return Array.from(scroller.querySelectorAll<HTMLElement>(`[${SNAP_ITEM_ATTR}]`));
}

function scrollTopFor(scroller: HTMLElement, el: HTMLElement, headerHeight: number): number {
  const offset = el.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
  return Math.max(0, scroller.scrollTop + offset - headerHeight);
}

/**
 * Where a single step in `direction` should land, or null when the browser
 * should keep the scroll for itself (long article still being read, or an edge
 * of the list with nothing to step onto).
 *
 * `headerHeight` is the sticky reading header covering the top of the
 * scroller — the reading viewport starts below it, and so do snap positions.
 */
export function planStep(
  scroller: HTMLElement,
  headerHeight: number,
  direction: 1 | -1,
): StepPlan | null {
  const items = snapItems(scroller);
  if (items.length === 0) return null;

  const scrollerRect = scroller.getBoundingClientRect();
  const top = scrollerRect.top + headerHeight;
  const bottom = scrollerRect.bottom;
  const rects = items.map((el) => el.getBoundingClientRect());

  const currentIndex = rects.findIndex((r) => r.top <= top + 1 && r.bottom > top + 1);
  if (currentIndex === -1) {
    // The boundary sits in a gap or above the first card (the "loading earlier
    // articles" strip). Step onto the nearest card in the travel direction.
    const target =
      direction > 0
        ? items[rects.findIndex((r) => r.top > top + 1)]
        : items[rects.findLastIndex((r) => r.bottom <= top + 1)];
    return target ? { top: scrollTopFor(scroller, target, headerHeight) } : null;
  }

  // Still content to read inside the current article in this direction.
  const current = rects[currentIndex];
  if (direction > 0 ? current.bottom > bottom + 1 : current.top < top - 1) return null;

  const next = items[currentIndex + direction];
  return next ? { top: scrollTopFor(scroller, next, headerHeight) } : null;
}

/** Scroll offsets at which each article sits aligned under the header. */
export function snapOffsets(scroller: HTMLElement, headerHeight: number): number[] {
  const base = scroller.getBoundingClientRect().top + headerHeight;
  return snapItems(scroller).map((el) =>
    Math.max(0, scroller.scrollTop + el.getBoundingClientRect().top - base),
  );
}

/**
 * How far a gesture starting at `from` may travel: the first snap position
 * beyond it in the travel direction. null when there is none — the ends of the
 * list, where the browser should be free to reach the loading sentinels.
 */
export function gestureLimit(
  scroller: HTMLElement,
  headerHeight: number,
  from: number,
  direction: 1 | -1,
): number | null {
  const offsets = snapOffsets(scroller, headerHeight);
  const beyond =
    direction > 0
      ? offsets.find((top) => top > from + 2)
      : offsets.filter((top) => top < from - 2).pop();
  return beyond ?? null;
}

/** Pixels per line for wheels that report their delta in lines (Firefox, and
 * some Windows mouse drivers). Chrome's own wheel-to-pixel scale. */
export const WHEEL_LINE_HEIGHT = 40;

/**
 * A wheel event's vertical travel in pixels. `deltaY` is only pixels when
 * `deltaMode` says so — a line-mode tick is ~3, which would sit under the step
 * threshold and leave the list looking frozen until several ticks piled up.
 */
export function wheelPixels(event: WheelEvent, scroller: HTMLElement): number {
  if (event.deltaMode === 1) return event.deltaY * WHEEL_LINE_HEIGHT;
  if (event.deltaMode === 2) return event.deltaY * (scroller.clientHeight || 800);
  return event.deltaY;
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function useAssistedScroll(opts: {
  enabled: boolean;
  /** The scroll container; read lazily because it is resolved in a mount
   * effect by the list that owns it. */
  getScroller: () => HTMLElement | null;
  /** Height of the sticky list header overlapping the scroller's top edge. */
  headerHeight: number;
}) {
  const { enabled, getScroller, headerHeight } = opts;
  // The wheel handler is installed once per enable/disable, so it reads the
  // live header height rather than closing over the mount-time value.
  const headerRef = useRef(headerHeight);
  useEffect(() => {
    headerRef.current = headerHeight;
  }, [headerHeight]);

  // Snapping lives on the app shell's scroller, which this list does not
  // render — hence styling the node directly, and putting it back on unmount
  // so other routes sharing the shell scroll normally.
  useEffect(() => {
    const scroller = getScroller();
    if (!scroller || !enabled) return;
    const previousSnap = scroller.style.scrollSnapType;
    const previousPadding = scroller.style.scrollPaddingTop;
    scroller.style.scrollSnapType = "y mandatory";
    scroller.style.scrollPaddingTop = `${headerHeight}px`;
    return () => {
      scroller.style.scrollSnapType = previousSnap;
      scroller.style.scrollPaddingTop = previousPadding;
    };
  }, [enabled, headerHeight, getScroller]);

  useEffect(() => {
    const scroller = getScroller();
    if (!scroller || !enabled) return;

    let accumulated = 0;
    let stepping = false;
    let lastStepAt = 0;
    let quietTimer: ReturnType<typeof setTimeout> | undefined;
    const endGesture = () => {
      stepping = false;
      accumulated = 0;
    };

    const onWheel = (event: WheelEvent) => {
      // Pinch-zoom and horizontal swipes are somebody else's gesture.
      if (event.ctrlKey || Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
      const plan = planStep(scroller, headerRef.current, event.deltaY > 0 ? 1 : -1);
      if (!plan) return;

      event.preventDefault();
      clearTimeout(quietTimer);
      quietTimer = setTimeout(endGesture, GESTURE_QUIET_MS);

      accumulated += wheelPixels(event, scroller);
      const now = event.timeStamp;
      const threshold = stepping ? HELD_STEP_THRESHOLD : WHEEL_STEP_THRESHOLD;
      if (Math.abs(accumulated) < threshold) return;
      if (stepping && now - lastStepAt < HELD_STEP_INTERVAL_MS) return;

      accumulated = 0;
      stepping = true;
      lastStepAt = now;
      scroller.scrollTo({
        top: plan.top,
        behavior: prefersReducedMotion() ? "auto" : "smooth",
      });
    };

    scroller.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      scroller.removeEventListener("wheel", onWheel);
      clearTimeout(quietTimer);
    };
  }, [enabled, getScroller]);

  // Touch: fence the gesture at one article. Everything stays passive so the
  // swipe itself keeps native feel and momentum; the fence only bites once the
  // scroller tries to leave the article the gesture was allowed to reach.
  useEffect(() => {
    const scroller = getScroller();
    if (!scroller || !enabled) return;

    let fenced = false;
    let startTop = 0;
    let direction: 1 | -1 | 0 = 0;
    let limit: number | null = null;
    let settleTimer: ReturnType<typeof setTimeout> | undefined;

    const clearFence = () => {
      fenced = false;
      direction = 0;
      limit = null;
    };

    const onTouchStart = (event: TouchEvent) => {
      const target = event.target as HTMLElement | null;
      clearTimeout(settleTimer);
      // Tapping a jump pill has to drop any fence left over from the swipe
      // that preceded it, or the pill's own scroll gets clamped to that
      // gesture's one-article limit.
      if (target?.closest?.(`[${SCROLL_JUMP_ATTR}]`)) {
        clearFence();
        return;
      }
      fenced = true;
      startTop = scroller.scrollTop;
      direction = 0;
      limit = null;
    };

    const onScroll = () => {
      if (!fenced) return;
      const top = scroller.scrollTop;
      if (direction === 0) {
        if (Math.abs(top - startTop) < 2) return;
        direction = top > startTop ? 1 : -1;
        limit = gestureLimit(scroller, headerRef.current, startTop, direction);
      }
      if (limit === null) return;
      // Cutting the scroll short here also cancels the fling: a programmatic
      // scroll ends the browser's own animation.
      if (direction > 0 ? top > limit + 4 : top < limit - 4) {
        scroller.scrollTo({ top: limit, behavior: "auto" });
      }
    };

    const onTouchEnd = () => {
      clearTimeout(settleTimer);
      settleTimer = setTimeout(clearFence, TOUCH_SETTLE_MS);
    };

    scroller.addEventListener("touchstart", onTouchStart, { passive: true });
    scroller.addEventListener("touchend", onTouchEnd, { passive: true });
    scroller.addEventListener("touchcancel", onTouchEnd, { passive: true });
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      scroller.removeEventListener("touchstart", onTouchStart);
      scroller.removeEventListener("touchend", onTouchEnd);
      scroller.removeEventListener("touchcancel", onTouchEnd);
      scroller.removeEventListener("scroll", onScroll);
      clearTimeout(settleTimer);
    };
  }, [enabled, getScroller]);
}
