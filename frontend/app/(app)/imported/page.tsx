"use client";

import { useEffect, useState } from "react";
import ArticleList from "@/components/ArticleList";
import ImportUrlModal from "@/components/ImportUrlModal";
import { PlusIcon, SearchIcon } from "@/components/icons";
import { useAuth } from "@/lib/auth";
import { useImportFeed } from "@/lib/queries";

export default function ImportedPage() {
  const { user } = useAuth();
  // Creates the hidden import feed server-side on first visit; its id scopes
  // the list below.
  const { data: importFeed } = useImportFeed();

  // Imported has no stories flavor — map that default (and none) to cards.
  const view = user?.default_view === "list" ? "list" : "cards";

  const [adding, setAdding] = useState(false);
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 250);
    return () => clearTimeout(t);
  }, [search]);

  return (
    <>
      <header
        data-reading-header
        className="sticky top-0 z-20 border-b px-4 pb-4 pt-4 sm:px-6 sm:pt-5"
        style={{
          background: "var(--bg-header)",
          backdropFilter: "blur(10px)",
          borderColor: "var(--line-soft)",
        }}
      >
        <div className="flex items-center gap-3">
          <h1 className="text-title font-semibold leading-none tracking-tight">
            Imported
          </h1>
          <button
            className="btn btn-accent ml-auto flex items-center gap-1.5"
            onClick={() => setAdding(true)}
          >
            <PlusIcon size={13} />
            Add link
          </button>
        </div>
        <div className="mt-3.5 flex">
          <div className="relative w-full sm:ml-auto sm:w-[240px]">
            <SearchIcon
              size={13}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2"
            />
            <input
              className="input"
              style={{ paddingLeft: 32, fontSize: 13, paddingTop: 6, paddingBottom: 6 }}
              placeholder="Search imported articles…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>
      </header>
      {importFeed && (
        <ArticleList
          variant={view}
          filter="all"
          feedId={String(importFeed.feed_id)}
          q={q}
          emptyTitle={q ? "Nothing matches your search." : "Nothing imported yet."}
          emptySubtitle={
            q
              ? undefined
              : "Add a link to save and summarize any page from around the web."
          }
        />
      )}
      {adding && <ImportUrlModal onClose={() => setAdding(false)} />}
    </>
  );
}
