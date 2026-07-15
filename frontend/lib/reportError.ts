import { API_URL } from "./api";

// Client-side error telemetry. Production browser errors previously vanished
// (console.error only); this ships them to the backend log. Reporting is
// best-effort and must never throw or loop: failures are swallowed, repeats
// are deduped per session, and the volume is capped.

const MAX_REPORTS_PER_SESSION = 20;

let reportedCount = 0;
const seenFingerprints = new Set<string>();

export function reportClientError(
  error: unknown,
  context?: string,
  digest?: string,
) {
  try {
    const err =
      error instanceof Error ? error : new Error(String(error ?? "unknown"));
    const fingerprint = `${err.name}:${err.message}`;
    if (seenFingerprints.has(fingerprint)) return;
    if (reportedCount >= MAX_REPORTS_PER_SESSION) return;
    seenFingerprints.add(fingerprint);
    reportedCount += 1;

    void fetch(`${API_URL}/api/client-errors`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: err.message.slice(0, 2000),
        stack: err.stack?.slice(0, 8000) ?? null,
        url: typeof window === "undefined" ? null : window.location.href,
        digest: digest ?? null,
        context: context ?? null,
      }),
      // Survives navigation/unload — errors often happen right before both.
      keepalive: true,
    }).catch(() => {});
  } catch {
    // Never let the reporter become an error source itself.
  }
}

let registered = false;

/** Idempotent: wire window-level error + unhandledrejection reporting once
 * per app load. Errors caught by React error boundaries don't reach these
 * listeners — error.tsx reports those itself. */
export function registerGlobalErrorReporting() {
  if (registered || typeof window === "undefined") return;
  registered = true;
  window.addEventListener("error", (event) => {
    reportClientError(event.error ?? event.message, "window-error");
  });
  window.addEventListener("unhandledrejection", (event) => {
    reportClientError(event.reason, "unhandled-rejection");
  });
}

/** Test-only: reset module state between cases. */
export function resetErrorReportingForTests() {
  registered = false;
  reportedCount = 0;
  seenFingerprints.clear();
}
