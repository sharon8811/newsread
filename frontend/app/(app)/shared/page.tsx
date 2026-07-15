"use client";

import ShareCard from "@/components/ShareCard";
import EmptyState from "@/components/ui/EmptyState";
import { useSharesReceived } from "@/lib/queries";

export default function SharedPage() {
  const { data: shares, isLoading } = useSharesReceived();

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
        <h1 className="text-title font-semibold leading-none tracking-tight">Shared with me</h1>
      </header>

      {!isLoading && (!shares || shares.length === 0) && (
        <EmptyState
          title="Nothing shared with you yet."
          subtitle="When someone @mentions you on an article, it lands here — with their note front and center."
        />
      )}

      <div className="fade-up">
        {shares?.map((share) => (
          <ShareCard key={share.id} share={share} direction="received" />
        ))}
      </div>
    </>
  );
}
