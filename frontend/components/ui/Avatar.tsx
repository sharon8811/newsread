import { cn } from "@/lib/cn";

const SIZE_CLASS = {
  sm: "h-6 w-6 text-[11px]",
  md: "h-7 w-7 text-[12px]",
  lg: "h-8 w-8 text-[13px]",
} as const;

// Initials circle. `children` lets call sites layer overlays (e.g. the
// project-member remove button) on top of the initial.
export default function Avatar({
  name,
  size = "md",
  title,
  className,
  children,
}: {
  name: string | null | undefined;
  size?: keyof typeof SIZE_CLASS;
  title?: string;
  className?: string;
  children?: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "flex shrink-0 items-center justify-center rounded-full bg-accent-soft font-semibold text-accent",
        SIZE_CLASS[size],
        className,
      )}
      title={title}
    >
      {name?.[0]?.toUpperCase() ?? "?"}
      {children}
    </span>
  );
}
