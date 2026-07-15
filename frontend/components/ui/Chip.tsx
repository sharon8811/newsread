"use client";

import { cn } from "@/lib/cn";

type ChipProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  active?: boolean;
};

// Toggleable pill: filter/topic/share-target selectors.
export default function Chip({ active = false, className, children, ...rest }: ChipProps) {
  return (
    <button
      type="button"
      aria-pressed={active}
      className={cn(
        "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-body-sm transition-colors",
        active
          ? "border-accent-border bg-accent-soft text-accent-bright"
          : "border-line text-ink-dim",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}
