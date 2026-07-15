"use client";

import ShareCard from "@/components/ShareCard";
import EmptyState from "@/components/ui/EmptyState";
import { useSharesSent } from "@/lib/queries";

export default function SentPage() {
  const { data: shares, isLoading } = useSharesSent();

  return (
    <>
      <header
        className="sticky top-0 z-20 border-b px-4 pb-4 pt-4 sm:px-6 sm:pt-5"
        style={{
          background: "var(--bg-header)",
          backdropFilter: "blur(10px)",
          borderColor: "var(--line-soft)",
        }}
      >
        <h1 className="text-title font-semibold leading-none tracking-tight">Sent</h1>
      </header>

      {!isLoading && (!shares || shares.length === 0) && (
        <EmptyState
          title="You have not shared anything yet."
          subtitle="Find a great article and share it with a note — that is the whole point."
        />
      )}

      <div className="fade-up">
        {shares?.map((share) => (
          <ShareCard key={share.id} share={share} direction="sent" />
        ))}
      </div>
    </>
  );
}
