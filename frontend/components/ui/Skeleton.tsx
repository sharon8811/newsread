import { cn } from "@/lib/cn";

// Loading placeholder block: size it with className (h-12, h-[320px], …).
export default function Skeleton({
  className,
  style,
}: {
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <div
      aria-hidden="true"
      className={cn("animate-pulse rounded-md bg-hover", className)}
      style={style}
    />
  );
}
