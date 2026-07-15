// Status label shown inside a shimmering media frame while an AI illustration
// renders in the background — shared by the article hero, cards and rows so
// the "generating" state reads identically everywhere. The parent frame must
// be relatively positioned; `compact` drops the text for thumbnail-sized
// frames where it wouldn't fit.
export default function GeneratingIndicator({ compact = false }: { compact?: boolean }) {
  return (
    <span
      role="status"
      aria-label="Generating illustration"
      className="font-mono-nr absolute inset-0 flex items-center justify-center gap-2 text-label"
      style={{ color: "var(--ink-faint)" }}
    >
      <span aria-hidden="true" style={{ color: "var(--accent)" }}>
        ✦
      </span>
      {!compact && (
        <>
          generating illustration
          <span aria-hidden="true" className="inline-flex items-center gap-1">
            {[0, 1, 2].map((i) => (
              <span
                key={i}
                className="typing-dot"
                style={{ animationDelay: `${i * 0.18}s` }}
              />
            ))}
          </span>
        </>
      )}
    </span>
  );
}
