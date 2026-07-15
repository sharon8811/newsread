import { cn } from "@/lib/cn";

// Inline error line. Always role="alert" so screen readers announce it;
// renders nothing when there is no error, so call sites can drop their
// `{error && ...}` guards.
export default function ErrorText({
  children,
  className,
}: {
  children?: React.ReactNode;
  className?: string;
}) {
  if (!children) return null;
  return (
    <p role="alert" className={cn("text-body-sm text-danger", className)}>
      {children}
    </p>
  );
}
