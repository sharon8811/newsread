import { CheckIcon } from "./icons";

export default function ReadStateIndicator({ isRead }: { isRead: boolean }) {
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1.5"
      style={{ color: isRead ? "var(--ink-faint)" : "var(--accent)" }}
      aria-label={isRead ? "Read" : "Unread"}
    >
      {isRead ? <CheckIcon size={11} /> : <span className="dot-unread" />}
      <span className="font-mono-nr text-[10px] font-medium uppercase tracking-[0.08em]">
        {isRead ? "Read" : "Unread"}
      </span>
    </span>
  );
}
