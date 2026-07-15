"use client";

import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { XIcon } from "./icons";

type ModalProps = {
  children: ReactNode;
  onClose: () => void;
  placement?: "center" | "drawer";
  contentClassName?: string;
};

/** Shared accessible overlay. Radix owns the body portal, focus trap,
 * outside-click handling, Escape handling, and focus restoration. */
export default function Modal({
  children,
  onClose,
  placement = "center",
  contentClassName = "",
}: ModalProps) {
  const centered =
    "left-1/2 top-1/2 w-[calc(100%-3rem)] max-w-[480px] -translate-x-1/2 -translate-y-1/2 rounded-lg";
  const drawer =
    "bottom-0 right-0 top-auto h-[min(88dvh,760px)] w-full rounded-t-xl sm:bottom-auto sm:top-0 sm:h-dvh sm:max-w-[440px] sm:rounded-none sm:border-y-0 sm:border-r-0";

  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay
          className="fixed inset-0 z-50"
          style={{ background: "var(--bg-scrim)", backdropFilter: "blur(4px)" }}
          data-testid="modal-overlay"
        />
        <Dialog.Content
          className={cn(
            "fade-up fixed z-50 border",
            placement === "drawer" ? drawer : centered,
            contentClassName,
          )}
          style={{
            background: "var(--bg-raised)",
            borderColor: "var(--line)",
            boxShadow: "var(--shadow-modal)",
          }}
          aria-describedby={undefined}
        >
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export const ModalTitle = Dialog.Title;
export const ModalClose = Dialog.Close;

/** Standard modal header: mono eyebrow, serif title, close button. Extra
 * header content (site links, meta rows) goes in `children`, under the title. */
export function ModalHeader({
  eyebrow,
  title,
  titleClassName,
  children,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  titleClassName?: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <p className="mono-label flex items-center gap-1.5">{eyebrow}</p>}
        <ModalTitle asChild>
          <h2 className={cn("font-serif-nr mt-1.5 text-title leading-snug", titleClassName)}>
            {title}
          </h2>
        </ModalTitle>
        {children}
      </div>
      <ModalClose asChild>
        <button className="icon-btn shrink-0" aria-label="Close">
          <XIcon size={16} />
        </button>
      </ModalClose>
    </div>
  );
}
