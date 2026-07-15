"use client";

import { useEffect, useState } from "react";

/** The value as it stood `delayMs` ago. Feed the result into an SWR key
 * (e.g. useUserSearch) instead of hand-rolling debounce+cancel effects —
 * SWR's keying drops stale responses on its own. */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);

  return debounced;
}
