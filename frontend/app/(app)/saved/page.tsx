"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import ArticleList from "@/components/ArticleList";
import StoriesView from "@/components/StoriesView";
import ViewSwitcher from "@/components/ViewSwitcher";
import { useAuth } from "@/lib/auth";
import { type ViewMode } from "@/lib/api";

const VIEW_MODES = ["list", "stories", "zen"] as const;

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
  const view: ViewMode = localView ?? paramView ?? user?.default_view ?? "list";

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
        <div className="flex items-center gap-3">
          <h1 className="font-serif-nr text-[24px] italic leading-none">Saved</h1>
          <div className="ml-auto">
            <ViewSwitcher view={view} feed={null} onSwitch={setLocalView} />
          </div>
        </div>
      </header>
      {view === "stories" ? (
        <StoriesView
          filter="saved"
          markOnAdvance={false}
          onExit={() => setLocalView("list")}
        />
      ) : (
        <ArticleList
          variant={view === "zen" ? "zen" : "list"}
          filter="saved"
          emptyTitle="Nothing saved yet."
          emptySubtitle="Bookmark an article to keep it here for later."
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
