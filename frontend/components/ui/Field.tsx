"use client";

import { useId } from "react";
import { cn } from "@/lib/cn";
import ErrorText from "./ErrorText";

type FieldProps = React.InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  hint?: string;
  error?: string | null;
  /** Applied to the wrapper; input styling goes through inputClassName. */
  className?: string;
  inputClassName?: string;
};

// Label + .input + hint/error scaffolding used by auth and settings forms.
// The label is always associated with the input (htmlFor/id).
export default function Field({
  label,
  hint,
  error,
  className,
  inputClassName,
  id,
  ...inputProps
}: FieldProps) {
  const autoId = useId();
  const inputId = id ?? autoId;
  return (
    <div className={className}>
      <label htmlFor={inputId} className="mono-label mb-1.5 block">
        {label}
      </label>
      <input id={inputId} className={cn("input", inputClassName)} {...inputProps} />
      {hint && !error && (
        <p className="mt-1 text-[11.5px] text-ink-faint">{hint}</p>
      )}
      <ErrorText className="mt-1">{error}</ErrorText>
    </div>
  );
}
