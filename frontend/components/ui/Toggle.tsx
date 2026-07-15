"use client";

// Promoted from FeedSettingsModal: the app's on/off control. Prefer this over
// a raw checkbox so identical semantics get an identical control everywhere.
export default function Toggle({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="relative h-[22px] w-[38px] rounded-full transition-colors disabled:cursor-default disabled:opacity-45"
      style={{ background: checked ? "var(--accent)" : "var(--line)" }}
    >
      <span
        className="absolute top-[3px] h-4 w-4 rounded-full transition-all"
        style={{ background: "var(--bg-raised)", left: checked ? 18 : 3 }}
      />
    </button>
  );
}
