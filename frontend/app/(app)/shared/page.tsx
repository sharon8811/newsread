"use client";

import useSWR from "swr";
import ShareCard from "@/components/ShareCard";
import { fetcher, type Share } from "@/lib/api";

export default function SharedPage() {
  const { data: shares, isLoading } = useSWR<Share[]>("/shares/received", fetcher);

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
        <h1 className="text-[20px] font-semibold leading-none tracking-tight">Shared with me</h1>
      </header>

      {!isLoading && (!shares || shares.length === 0) && (
        <div className="flex flex-col items-center px-8 py-28 text-center">
          <p className="text-[17px] font-medium" style={{ color: "var(--ink-dim)" }}>
            Nothing shared with you yet.
          </p>
          <p className="mt-2 max-w-md text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
            When someone @mentions you on an article, it lands here — with their note
            front and center.
          </p>
        </div>
      )}

      <div className="fade-up">
        {shares?.map((share) => (
          <ShareCard key={share.id} share={share} direction="received" />
        ))}
      </div>
    </>
  );
}
