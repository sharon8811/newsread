"use client";

import { createContext, useContext } from "react";

// On mobile the app shows exactly one bar. Reading routes replace the shell's
// wordmark header with their own compact bar, so they need a way to open the
// nav drawer the shell still owns.
export const MobileNavContext = createContext<() => void>(() => {});

export function useOpenMobileNav() {
  return useContext(MobileNavContext);
}

/** Routes that render their own mobile bar instead of the shell's. */
export function ownsMobileChrome(pathname: string): boolean {
  return pathname === "/" || pathname.startsWith("/article/");
}
