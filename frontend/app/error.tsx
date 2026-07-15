"use client";

import { useEffect } from "react";
import { reportClientError } from "@/lib/reportError";

// Route-segment error boundary. This Next version passes unstable_retry
// (re-fetches and re-renders the segment); reset is the legacy prop.
export default function ErrorPage({
  error,
  unstable_retry,
  reset,
}: {
  error: Error & { digest?: string };
  unstable_retry?: () => void;
  reset?: () => void;
}) {
  useEffect(() => {
    console.error(error);
    // Boundary-caught errors never reach the window listeners; report here.
    reportClientError(error, "error-boundary", error.digest);
  }, [error]);

  const retry = unstable_retry ?? reset;

  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-4 px-6 text-center">
      <p className="mono-label">Something went wrong</p>
      <h1 className="font-serif-nr text-display font-medium">
        This page hit an unexpected error.
      </h1>
      <p className="text-body text-ink-faint">
        {error.digest
          ? `Reference: ${error.digest}`
          : "Try again — it may have been temporary."}
      </p>
      {retry && (
        <button className="btn mt-2" onClick={() => retry()}>
          Try again
        </button>
      )}
    </div>
  );
}
