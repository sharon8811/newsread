"use client";

import { useEffect } from "react";
import { API_URL, getToken } from "./api";

// Clients flush every 30s of counted reading; the backend rejects heartbeats
// over 120s, so a single flush always fits.
export const FLUSH_INTERVAL_S = 30;
export const MAX_HEARTBEAT_S = 120;
// No pointer/keyboard/scroll input for this long pauses the timer — a focused
// tab someone walked away from shouldn't keep accruing reading time.
export const IDLE_AFTER_MS = 60_000;

const INPUT_EVENTS = ["pointermove", "pointerdown", "keydown", "wheel", "touchstart", "scroll"] as const;

// YYYY-MM-DD in the user's timezone; the backend buckets by this so "today"
// flips at the user's midnight, not UTC's.
export function localDay(now = new Date()): string {
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/** Accrues reading time for an article while the tab is visible, focused and
 * not idle, and heartbeats it to the backend. Flushes on an interval and when
 * the page hides/unmounts (keepalive fetch, since sendBeacon can't carry the
 * Authorization header). */
export function useReadingTimer(articleId: number | undefined) {
  useEffect(() => {
    if (articleId === undefined) return;
    let pending = 0; // counted seconds not yet flushed
    let sinceFlush = 0;
    let lastInput = Date.now();

    const counting = () =>
      document.visibilityState === "visible" &&
      document.hasFocus() &&
      Date.now() - lastInput < IDLE_AFTER_MS;

    function flush() {
      const seconds = Math.min(pending, MAX_HEARTBEAT_S);
      if (seconds < 1) return;
      pending = 0;
      sinceFlush = 0;
      const token = getToken();
      fetch(`${API_URL}/api/activity/heartbeat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ article_id: articleId, seconds, source: "web", day: localDay() }),
        keepalive: true,
      }).catch(() => {
        // Transient failure: put the time back (capped) for the next flush.
        pending = Math.min(pending + seconds, MAX_HEARTBEAT_S);
      });
    }

    const tick = window.setInterval(() => {
      if (!counting()) return;
      pending += 1;
      sinceFlush += 1;
      if (sinceFlush >= FLUSH_INTERVAL_S) flush();
    }, 1000);

    const onInput = () => {
      lastInput = Date.now();
    };
    for (const event of INPUT_EVENTS) window.addEventListener(event, onInput, { passive: true });

    const onHide = () => {
      if (document.visibilityState === "hidden") flush();
    };
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", flush);

    return () => {
      window.clearInterval(tick);
      for (const event of INPUT_EVENTS) window.removeEventListener(event, onInput);
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, [articleId]);
}
