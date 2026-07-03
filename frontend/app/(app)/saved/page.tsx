"use client";

import ArticleList from "@/components/ArticleList";

export default function SavedPage() {
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
        <h1 className="font-serif-nr text-[24px] italic leading-none">Saved</h1>
      </header>
      <ArticleList
        filter="saved"
        emptyTitle="Nothing saved yet."
        emptySubtitle="Bookmark an article to keep it here for later."
      />
    </>
  );
}
