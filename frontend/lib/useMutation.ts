"use client";

import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";

type MutationOptions<TArgs extends unknown[], TResult> = {
  /** Runs after the mutation resolves (mutate caches, close modals, …). */
  onSuccess?: (result: TResult, ...args: TArgs) => void;
  /** Message when the thrown value isn't an Error. */
  fallbackError?: string;
  /** Where errors surface: inline `error` state (default) or a toast. */
  surface?: "state" | "toast";
};

/** The app's mutation pattern in one place: a busy flag that also guards
 * against double-fires, and error capture to inline state or a toast.
 * Replaces the per-component setBusy/try/catch/finally machinery. */
export function useMutation<TArgs extends unknown[], TResult>(
  fn: (...args: TArgs) => Promise<TResult>,
  {
    onSuccess,
    fallbackError = "Something went wrong",
    surface = "state",
  }: MutationOptions<TArgs, TResult> = {},
) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busyRef = useRef(false);

  const run = useCallback(
    async (...args: TArgs): Promise<TResult | undefined> => {
      if (busyRef.current) return undefined;
      busyRef.current = true;
      setBusy(true);
      setError(null);
      try {
        const result = await fn(...args);
        onSuccess?.(result, ...args);
        return result;
      } catch (err) {
        const message = err instanceof Error ? err.message : fallbackError;
        if (surface === "toast") toast.error(message);
        else setError(message);
        return undefined;
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [fn, onSuccess, fallbackError, surface],
  );

  return { run, busy, error, setError };
}
