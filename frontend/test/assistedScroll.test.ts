import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  GESTURE_QUIET_MS,
  HELD_STEP_INTERVAL_MS,
  SCROLL_JUMP_ATTR,
  SNAP_ITEM_ATTR,
  TOUCH_SETTLE_MS,
  gestureLimit,
  planStep,
  snapOffsets,
  useAssistedScroll,
} from "@/lib/assistedScroll";

const HEADER = 50;
const VIEWPORT = 500; // scroller height below the page chrome

type Box = { top: number; height: number };

/** jsdom has no layout: build a scroller whose children report the geometry
 * the test wants, positioned relative to a scroller pinned at y=0. */
function buildScroller(boxes: Box[]) {
  const scroller = document.createElement("main");
  scroller.getBoundingClientRect = () =>
    ({ top: 0, bottom: VIEWPORT, height: VIEWPORT }) as DOMRect;
  Object.defineProperty(scroller, "scrollTop", { value: 0, writable: true });
  scroller.scrollTo = vi.fn();
  for (const box of boxes) {
    const item = document.createElement("div");
    item.setAttribute(SNAP_ITEM_ATTR, "");
    // Positions are given for scrollTop 0 and travel with the scroller, the
    // way real layout does.
    item.getBoundingClientRect = () => {
      const top = box.top - scroller.scrollTop;
      return { top, bottom: top + box.height, height: box.height } as DOMRect;
    };
    scroller.appendChild(item);
  }
  document.body.appendChild(scroller);
  return scroller;
}

function wheel(scroller: HTMLElement, deltaY: number, timeStamp = 0, init: WheelEventInit = {}) {
  const event = new WheelEvent("wheel", {
    deltaY,
    cancelable: true,
    bubbles: true,
    ...init,
  });
  Object.defineProperty(event, "timeStamp", { value: timeStamp });
  scroller.dispatchEvent(event);
  return event;
}

function touch(target: Element, type: string) {
  target.dispatchEvent(new Event(type, { bubbles: true }));
}

/** Move the scroller the way the browser would during a gesture. */
function scrollTo(scroller: HTMLElement, top: number) {
  (scroller as unknown as { scrollTop: number }).scrollTop = top;
  scroller.dispatchEvent(new Event("scroll"));
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("planStep", () => {
  it("returns nothing when the list has no snap targets", () => {
    expect(planStep(buildScroller([]), HEADER, 1)).toBeNull();
  });

  it("steps to the next article when the current one fits the viewport", () => {
    // Card A aligned under the header, card B right below it.
    const scroller = buildScroller([
      { top: HEADER, height: 300 },
      { top: HEADER + 300, height: 300 },
    ]);
    expect(planStep(scroller, HEADER, 1)).toEqual({ top: 300 });
  });

  it("steps back to the previous article", () => {
    const scroller = buildScroller([
      { top: HEADER - 300, height: 300 },
      { top: HEADER, height: 300 },
    ]);
    expect(planStep(scroller, HEADER, -1)).toEqual({ top: 0 });
  });

  it("leaves scrolling alone inside an article taller than the viewport", () => {
    const scroller = buildScroller([
      { top: HEADER, height: 1200 },
      { top: HEADER + 1200, height: 300 },
    ]);
    expect(planStep(scroller, HEADER, 1)).toBeNull();
  });

  it("steps on once a tall article's bottom edge is reached", () => {
    const scroller = buildScroller([
      { top: HEADER - 850, height: 1200 }, // bottom sits at the viewport edge
      { top: HEADER + 350, height: 300 },
    ]);
    expect(planStep(scroller, HEADER, 1)).toEqual({ top: 350 });
  });

  it("scrolls back inside a tall article before stepping off it", () => {
    const scroller = buildScroller([{ top: HEADER - 400, height: 1200 }]);
    expect(planStep(scroller, HEADER, -1)).toBeNull();
  });

  it("stops at the ends of the list", () => {
    const single = buildScroller([{ top: HEADER, height: 200 }]);
    expect(planStep(single, HEADER, 1)).toBeNull();
    expect(planStep(single, HEADER, -1)).toBeNull();
  });

  it("snaps onto the nearest card when the boundary sits above the list", () => {
    // A "loading earlier articles" strip holds the top of the scroller.
    const scroller = buildScroller([
      { top: HEADER + 120, height: 300 },
      { top: HEADER + 420, height: 300 },
    ]);
    expect(planStep(scroller, HEADER, 1)).toEqual({ top: 120 });
    expect(planStep(scroller, HEADER, -1)).toBeNull();
  });

  it("snaps back onto the last card above the boundary", () => {
    const scroller = buildScroller([
      { top: HEADER - 400, height: 300 }, // fully above the reading viewport
      { top: HEADER + 100, height: 300 },
    ]);
    expect(planStep(scroller, HEADER, -1)).toEqual({ top: 0 });
  });
});

describe("useAssistedScroll", () => {
  let scroller: HTMLElement;

  function mount(enabled = true, headerHeight = HEADER) {
    return renderHook(() =>
      useAssistedScroll({ enabled, getScroller: () => scroller, headerHeight }),
    );
  }

  beforeEach(() => {
    scroller = buildScroller([
      { top: HEADER, height: 300 },
      { top: HEADER + 300, height: 300 },
    ]);
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: false }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("snaps the scroller and insets snap positions by the header", () => {
    const { unmount } = mount();
    expect(scroller.style.scrollSnapType).toBe("y mandatory");
    expect(scroller.style.scrollPaddingTop).toBe(`${HEADER}px`);
    unmount();
    expect(scroller.style.scrollSnapType).toBe("");
    expect(scroller.style.scrollPaddingTop).toBe("");
  });

  it("leaves the scroller untouched when disabled", () => {
    mount(false);
    expect(scroller.style.scrollSnapType).toBe("");
    expect(wheel(scroller, 200).defaultPrevented).toBe(false);
    expect(scroller.scrollTo).not.toHaveBeenCalled();
  });

  it("moves one article per wheel gesture", () => {
    mount();
    act(() => {
      wheel(scroller, 200, 0);
    });
    expect(scroller.scrollTo).toHaveBeenCalledWith({ top: 300, behavior: "smooth" });
  });

  it("ignores the trickle at the start of a trackpad swipe", () => {
    mount();
    act(() => {
      wheel(scroller, 4, 0);
      wheel(scroller, 6, 10);
    });
    expect(scroller.scrollTo).not.toHaveBeenCalled();
    act(() => {
      wheel(scroller, 20, 20);
    });
    expect(scroller.scrollTo).toHaveBeenCalledTimes(1);
  });

  it("does not let one flick and its inertia cross several articles", () => {
    vi.useFakeTimers();
    try {
      mount();
      act(() => {
        // A hard flick: one big delta, then a decaying inertia tail.
        let stamp = 0;
        for (const delta of [400, 260, 180, 120, 80, 50, 30, 20, 10, 5]) {
          wheel(scroller, delta, (stamp += 16));
        }
      });
      expect(scroller.scrollTo).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps stepping when the wheel never goes quiet, but paced", () => {
    mount();
    act(() => {
      wheel(scroller, 200, 0);
    });
    // Enough delta but too soon: the gesture has not earned a second article.
    act(() => {
      wheel(scroller, 200, 100);
    });
    expect(scroller.scrollTo).toHaveBeenCalledTimes(1);
    act(() => {
      wheel(scroller, 200, HELD_STEP_INTERVAL_MS + 200);
    });
    expect(scroller.scrollTo).toHaveBeenCalledTimes(2);
  });

  it("adds up small ticks from hardware that never fills one gesture", () => {
    vi.useFakeTimers();
    try {
      mount();
      // 10px ticks, spaced further apart than the quiet window: each one ends
      // its own gesture, and the list would never move if travel were dropped.
      let stamp = 0;
      for (let i = 0; i < 3; i++) {
        act(() => {
          wheel(scroller, 10, (stamp += 300));
        });
        act(() => {
          vi.advanceTimersByTime(GESTURE_QUIET_MS + 10);
        });
      }
      expect(scroller.scrollTo).toHaveBeenCalledWith({ top: 300, behavior: "smooth" });
    } finally {
      vi.useRealTimers();
    }
  });

  it("counts travel afresh when the wheel turns around", () => {
    vi.useFakeTimers();
    try {
      scroller.remove();
      scroller = buildScroller([
        { top: HEADER - 300, height: 300 },
        { top: HEADER, height: 300 },
        { top: HEADER + 300, height: 300 },
        { top: HEADER + 600, height: 300 },
      ]);
      (scroller as unknown as { scrollTop: number }).scrollTop = 300;
      mount();
      act(() => {
        wheel(scroller, 20, 0); // half-hearted push down, no step
      });
      expect(scroller.scrollTo).not.toHaveBeenCalled();
      act(() => {
        vi.advanceTimersByTime(GESTURE_QUIET_MS + 10);
      });
      // Turning around: the abandoned 20px must not eat into this push.
      act(() => {
        wheel(scroller, -30, 300);
      });
      expect(scroller.scrollTo).toHaveBeenCalledWith({ top: 0, behavior: "smooth" });
    } finally {
      vi.useRealTimers();
    }
  });

  it("treats a fresh gesture after a pause as a fresh step", () => {
    vi.useFakeTimers();
    try {
      mount();
      act(() => {
        wheel(scroller, 200, 0);
      });
      act(() => {
        vi.advanceTimersByTime(GESTURE_QUIET_MS + 10);
      });
      act(() => {
        wheel(scroller, 30, GESTURE_QUIET_MS + 20);
      });
      expect(scroller.scrollTo).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("steps on a line-mode wheel tick, whose deltas are not pixels", () => {
    mount();
    // Firefox reports ~3 lines per tick; unscaled that never reaches the
    // pixel threshold and the list would look frozen.
    act(() => {
      wheel(scroller, 3, 0, { deltaMode: 1 });
    });
    expect(scroller.scrollTo).toHaveBeenCalledWith({ top: 300, behavior: "smooth" });
  });

  it("steps on a page-mode wheel tick", () => {
    mount();
    act(() => {
      wheel(scroller, 1, 0, { deltaMode: 2 });
    });
    expect(scroller.scrollTo).toHaveBeenCalledTimes(1);
  });

  it("stays out of pinch-zoom and horizontal gestures", () => {
    mount();
    act(() => {
      wheel(scroller, 200, 0, { ctrlKey: true });
      wheel(scroller, 100, 10, { deltaX: 300 });
    });
    expect(scroller.scrollTo).not.toHaveBeenCalled();
  });

  it("hands a long article back to the browser", () => {
    scroller.remove();
    scroller = buildScroller([
      { top: HEADER, height: 1200 },
      { top: HEADER + 1200, height: 300 },
    ]);
    mount();
    const event = wheel(scroller, 200, 0);
    expect(event.defaultPrevented).toBe(false);
    expect(scroller.scrollTo).not.toHaveBeenCalled();
  });

  it("jumps without animation when the reader prefers reduced motion", () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true }));
    mount();
    act(() => {
      wheel(scroller, 200, 0);
    });
    expect(scroller.scrollTo).toHaveBeenCalledWith({ top: 300, behavior: "auto" });
  });

  it("fences a touch gesture at the next article and cuts the fling", () => {
    mount();
    act(() => touch(scroller, "touchstart"));
    act(() => scrollTo(scroller, 120)); // the swipe gets under way
    expect(scroller.scrollTo).not.toHaveBeenCalled();
    act(() => scrollTo(scroller, 900)); // momentum tries to run away
    expect(scroller.scrollTo).toHaveBeenCalledWith({ top: 300, behavior: "auto" });
  });

  it("fences an upward gesture too", () => {
    scroller.remove();
    scroller = buildScroller([
      { top: HEADER, height: 300 },
      { top: HEADER + 300, height: 300 },
      { top: HEADER + 600, height: 300 },
    ]);
    (scroller as unknown as { scrollTop: number }).scrollTop = 600;
    mount();
    act(() => touch(scroller, "touchstart"));
    act(() => scrollTo(scroller, 560));
    act(() => scrollTo(scroller, 0)); // flung back to the top of the list
    expect(scroller.scrollTo).toHaveBeenCalledWith({ top: 300, behavior: "auto" });
  });

  it("re-fences when a gesture turns around mid-swipe", () => {
    scroller.remove();
    scroller = buildScroller([
      { top: HEADER, height: 300 },
      { top: HEADER + 300, height: 300 },
      { top: HEADER + 600, height: 300 },
    ]);
    (scroller as unknown as { scrollTop: number }).scrollTop = 600;
    mount();
    act(() => touch(scroller, "touchstart"));
    act(() => scrollTo(scroller, 640)); // a nudge down…
    act(() => scrollTo(scroller, 100)); // …then a flick the other way
    expect(scroller.scrollTo).toHaveBeenCalledWith({ top: 300, behavior: "auto" });
  });

  it("stops fencing once the gesture has settled", () => {
    vi.useFakeTimers();
    try {
      mount();
      act(() => touch(scroller, "touchstart"));
      act(() => touch(scroller, "touchend"));
      act(() => {
        vi.advanceTimersByTime(TOUCH_SETTLE_MS + 10);
      });
      act(() => scrollTo(scroller, 2000));
      expect(scroller.scrollTo).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("lets the jump pills scroll as far as they like", () => {
    const pill = document.createElement("button");
    pill.setAttribute(SCROLL_JUMP_ATTR, "");
    scroller.appendChild(pill);
    mount();
    act(() => touch(pill, "touchstart"));
    act(() => scrollTo(scroller, 2000));
    expect(scroller.scrollTo).not.toHaveBeenCalled();
  });

  it("drops a live fence when a jump pill is tapped right after a swipe", () => {
    const pill = document.createElement("button");
    pill.setAttribute(SCROLL_JUMP_ATTR, "");
    scroller.appendChild(pill);
    mount();
    // A swipe arms the fence at the next article…
    act(() => touch(scroller, "touchstart"));
    act(() => scrollTo(scroller, 200));
    act(() => touch(scroller, "touchend"));
    // …and the pill is tapped before the fence has settled.
    act(() => touch(pill, "touchstart"));
    act(() => scrollTo(scroller, 2000));
    expect(scroller.scrollTo).not.toHaveBeenCalled();
  });

  it("does not fence a gesture that never moves the scroller", () => {
    mount();
    act(() => touch(scroller, "touchstart"));
    act(() => scrollTo(scroller, 1));
    expect(scroller.scrollTo).not.toHaveBeenCalled();
  });

  it("leaves the last article free to reach the end of the list", () => {
    scroller.remove();
    scroller = buildScroller([{ top: HEADER, height: 300 }]);
    mount();
    act(() => touch(scroller, "touchstart"));
    act(() => scrollTo(scroller, 200));
    act(() => scrollTo(scroller, 900));
    expect(scroller.scrollTo).not.toHaveBeenCalled();
  });

  it("reports the snap offsets used for fencing", () => {
    expect(snapOffsets(scroller, HEADER)).toEqual([0, 300]);
    expect(gestureLimit(scroller, HEADER, 0, 1)).toBe(300);
    expect(gestureLimit(scroller, HEADER, 300, 1)).toBeNull();
  });

  it("does nothing without a scroller", () => {
    const detached = renderHook(() =>
      useAssistedScroll({ enabled: true, getScroller: () => null, headerHeight: HEADER }),
    );
    expect(() => detached.unmount()).not.toThrow();
  });
});
