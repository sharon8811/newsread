// Suspense fallback for (app) pages: fills the <main> scroll area while a
// route segment loads, using the same typing dots as chat/AI pending states.
export default function Loading() {
  return (
    <div
      role="status"
      aria-label="Loading"
      className="flex h-full items-center justify-center gap-1.5"
    >
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="typing-dot"
          style={{ animationDelay: `${i * 0.18}s` }}
        />
      ))}
    </div>
  );
}
