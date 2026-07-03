"use client";

import useSWR from "swr";
import ShareCard from "@/components/ShareCard";
import { fetcher, type Share } from "@/lib/api";

export default function SentPage() {
  const { data: shares, isLoading } = useSWR<Share[]>("/shares/sent", fetcher);

  return (
    <>
      <header
        className="sticky top-0 z-20 border-b px-6 pb-4 pt-5"
        style={{
          background: "rgba(15, 13, 10, 0.88)",
          backdropFilter: "blur(10px)",
          borderColor: "var(--line-soft)",
        }}
      >
        <h1 className="font-serif-nr text-[24px] italic leading-none">Sent</h1>
      </header>

      {!isLoading && (!shares || shares.length === 0) && (
        <div className="flex flex-col items-center px-8 py-28 text-center">
          <p className="font-serif-nr text-[22px] italic" style={{ color: "var(--ink-dim)" }}>
            You have not shared anything yet.
          </p>
          <p className="mt-2 max-w-md text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
            Find a great article and share it with a note — that is the whole point.
          </p>
        </div>
      )}

      <div className="fade-up">
        {shares?.map((share) => (
          <ShareCard key={share.id} share={share} direction="sent" />
        ))}
      </div>
    </>
  );
}
