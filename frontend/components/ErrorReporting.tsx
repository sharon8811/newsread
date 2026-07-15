"use client";

import { useEffect } from "react";
import { registerGlobalErrorReporting } from "@/lib/reportError";

/** Mounted once in the root layout: turns on window-level error telemetry. */
export default function ErrorReporting() {
  useEffect(() => {
    registerGlobalErrorReporting();
  }, []);
  return null;
}
