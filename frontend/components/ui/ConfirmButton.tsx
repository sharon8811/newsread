"use client";

import { useEffect, useState } from "react";
import Button from "./Button";

type ConfirmButtonProps = Omit<
  React.ComponentProps<typeof Button>,
  "onClick"
> & {
  onConfirm: () => void;
  /** Label shown after the first click ("Really unsubscribe?"). */
  confirmLabel: React.ReactNode;
  /** Disarm automatically if the second click never comes. */
  resetAfterMs?: number;
};

// Two-step destructive button: first click arms it and swaps the label,
// second click confirms. The armed state times out so a stray click doesn't
// leave a live destructive button behind.
export default function ConfirmButton({
  onConfirm,
  confirmLabel,
  resetAfterMs = 4000,
  variant = "danger",
  children,
  ...rest
}: ConfirmButtonProps) {
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!confirming) return;
    const t = setTimeout(() => setConfirming(false), resetAfterMs);
    return () => clearTimeout(t);
  }, [confirming, resetAfterMs]);

  return (
    <Button
      variant={variant}
      onClick={() => {
        if (confirming) {
          setConfirming(false);
          onConfirm();
        } else {
          setConfirming(true);
        }
      }}
      {...rest}
    >
      {confirming ? confirmLabel : children}
    </Button>
  );
}
