"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import ArticleList from "@/components/ArticleList";
import StoriesView from "@/components/StoriesView";
import ViewSwitcher from "@/components/ViewSwitcher";
import { SearchIcon } from "@/components/icons";
import { useAuth } from "@/lib/auth";
import { type ViewMode } from "@/lib/api";

const VIEW_MODES = ["cards", "list", "stories"] as const;

function Saved() {
  const searchParams = useSearchParams();
  const { user } = useAuth();

  // Saved is a merged view: user default only. ?view= is a read-only deep
  // link; in-session switches are plain state (see inbox page for why the
  // URL is never written).
  const [localView, setLocalView] = useState<ViewMode | null>(null);
  const rawParamView = searchParams.get("view");
  const paramView = VIEW_MODES.includes(rawParamView as ViewMode)
    ? (rawParamView as ViewMode)
    : null;
  const view: ViewMode = localView ?? paramView ?? user?.default_view ?? "cards";

  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 250);
    return () => clearTimeout(t);
  }, [search]);

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
        <div className="flex items-center gap-3">
          <h1 className="text-[20px] font-semibold leading-none tracking-tight">Saved</h1>
          <div className="ml-auto">
            <ViewSwitcher view={view} feed={null} onSwitch={setLocalView} />
          </div>
        </div>
        {view !== "stories" && (
          <div className="mt-3.5 flex">
            <div className="relative w-full sm:ml-auto sm:w-[240px]">
              <SearchIcon
                size={13}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2"
              />
              <input
                className="input"
                style={{ paddingLeft: 32, fontSize: 13, paddingTop: 6, paddingBottom: 6 }}
                placeholder="Search saved articles…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>
        )}
      </header>
      {view === "stories" ? (
        <StoriesView
          filter="saved"
          markOnAdvance={false}
          onExit={() => setLocalView("cards")}
        />
      ) : (
        <ArticleList
          variant={view === "list" ? "list" : "cards"}
          filter="saved"
          q={q}
          emptyTitle={q ? "Nothing matches your search." : "Nothing saved yet."}
          emptySubtitle={
            q ? undefined : "Bookmark an article to keep it here for later."
          }
        />
      )}
    </>
  );
}

export default function SavedPage() {
  return (
    <Suspense fallback={null}>
      <Saved />
    </Suspense>
  );
}
