import { cn } from "@/lib/cn";

// Full-area empty state (promoted from ArticleList). `action` renders under
// the copy — usually a Button or a styled Link.
export default function EmptyState({
  title,
  subtitle,
  action,
  className,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center px-8 py-28 text-center",
        className,
      )}
    >
      <p className="text-[17px] font-medium text-ink-dim">{title}</p>
      {subtitle && (
        <p className="mt-2 max-w-sm text-[13.5px] text-ink-faint">{subtitle}</p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
