import { cn } from "@/lib/cn";

const TONE_CLASS = {
  neutral: "border-line text-ink-faint",
  accent: "border-line text-accent",
  "accent-strong": "border-accent-border bg-accent-soft text-accent-bright",
} as const;

// Static pill: mono metadata chips ("Only you", "Done", rule kinds, tiers).
export default function Badge({
  tone = "neutral",
  className,
  title,
  children,
}: {
  tone?: keyof typeof TONE_CLASS;
  className?: string;
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "font-mono-nr inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px]",
        TONE_CLASS[tone],
        className,
      )}
      title={title}
    >
      {children}
    </span>
  );
}
