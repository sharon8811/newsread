// Reading-time tracking, mirroring frontend/lib/useReadingTimer.ts: accrue
// seconds while the article screen is focused AND the app is in the
// foreground, heartbeat every 30s, flush on background/blur. No idle detection
// here — a lit, foregrounded phone showing the article is active reading, and
// auto-lock bounds the runaway case.

import { useFocusEffect } from "expo-router";
import { useCallback } from "react";
import { AppState } from "react-native";

import { api } from "./api";

export const FLUSH_INTERVAL_S = 30;
// The backend rejects heartbeats over 120s, so one flush always fits.
export const MAX_HEARTBEAT_S = 120;

// YYYY-MM-DD in the device's timezone; the backend buckets by this so "today"
// flips at the user's midnight, not UTC's.
export function localDay(now = new Date()): string {
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/** Starts accruing and heartbeating reading time for one article; returns the
 * stop function. Plain function so tests can drive it without rendering. */
export function startReadingTracker(articleId: number): () => void {
  let pending = 0; // counted seconds not yet flushed
  let sinceFlush = 0;

  const flush = () => {
    const seconds = Math.min(pending, MAX_HEARTBEAT_S);
    if (seconds < 1) return;
    pending = 0;
    sinceFlush = 0;
    api("/activity/heartbeat", {
      method: "POST",
      body: { article_id: articleId, seconds, source: "mobile", day: localDay() },
    }).catch(() => {
      // Transient failure: put the time back (capped) for the next flush.
      pending = Math.min(pending + seconds, MAX_HEARTBEAT_S);
    });
  };

  const tick = setInterval(() => {
    if (AppState.currentState !== "active") return;
    pending += 1;
    sinceFlush += 1;
    if (sinceFlush >= FLUSH_INTERVAL_S) flush();
  }, 1000);

  // JS timers stall in the background anyway; flush what we have the moment
  // the app leaves the foreground so nothing is lost to a kill.
  const appState = AppState.addEventListener("change", (state) => {
    if (state !== "active") flush();
  });

  return () => {
    clearInterval(tick);
    appState.remove();
    flush();
  };
}

export function useReadingTimer(articleId: number | undefined) {
  useFocusEffect(
    useCallback(() => {
      if (articleId === undefined) return;
      return startReadingTracker(articleId);
    }, [articleId]),
  );
}
