"use client";

import { useEffect } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { noteHistoryPop, trackBackNav } from "@/lib/backNav";

/** Invisible observer that feeds every committed URL (and each popstate) to
 * lib/backNav so detail-page back buttons know whether real history exists.
 * Mounted once in the app layout, inside Suspense for useSearchParams. */
export default function BackNavTracker() {
  const pathname = usePathname();
  const searchParams = useSearchParams();

  useEffect(() => {
    const onPop = () => noteHistoryPop();
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    trackBackNav(pathname, searchParams.toString());
  }, [pathname, searchParams]);

  return null;
}
