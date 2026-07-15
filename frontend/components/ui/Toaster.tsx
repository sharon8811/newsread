"use client";

import { Toaster as SonnerToaster } from "sonner";

// App-wide toast outlet, styled with the design tokens so it follows the
// theme automatically. Fire toasts with `toast.error(...)` / `toast(...)`
// from sonner, or via useMutation's `surface: "toast"`.
export default function Toaster() {
  return (
    <SonnerToaster
      position="bottom-right"
      gap={8}
      toastOptions={{
        style: {
          background: "var(--bg-raised)",
          color: "var(--ink)",
          border: "1px solid var(--line)",
          boxShadow: "var(--shadow-modal)",
          fontSize: "13px",
        },
      }}
    />
  );
}
