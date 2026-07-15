"use client";

import { cn } from "@/lib/cn";

const VARIANT_CLASS = {
  secondary: "btn",
  primary: "btn btn-accent",
  ghost: "btn btn-ghost",
  danger: "btn btn-danger",
} as const;

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof VARIANT_CLASS;
  /** sm keeps .btn padding but drops the label to 12px (dense card actions). */
  size?: "md" | "sm";
  /** Disables the button and marks it busy; keep the label swap at the call site. */
  loading?: boolean;
};

export default function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  disabled,
  className,
  type = "button",
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(VARIANT_CLASS[variant], size === "sm" && "text-body-sm", className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {children}
    </button>
  );
}
