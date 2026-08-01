"use client";

import Link from "next/link";
import { Suspense, useCallback, useEffect, useLayoutEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import BackNavTracker from "@/components/BackNavTracker";
import Sidebar from "@/components/Sidebar";
import { MenuIcon } from "@/components/icons";
import { useAuth } from "@/lib/auth";
import { MobileNavContext, ownsMobileChrome } from "@/lib/mobileNav";
import {
  clearReadingReturnAnchor,
  getLatestReadingReturnAnchor,
} from "@/lib/readingSession";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { authed, ready, suspended, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [navOpen, setNavOpen] = useState(false);
  const openNav = useCallback(() => setNavOpen(true), []);
  // Reading routes go full-screen on phones: they render their own compact bar
  // (inbox) or none at all (article detail), so the shell's own bar stands down.
  const pageOwnsBar = ownsMobileChrome(pathname);

  useEffect(() => {
    if (ready && !authed) router.replace("/login");
  }, [ready, authed, router]);

  // Close the drawer whenever navigation happens.
  useEffect(() => setNavOpen(false), [pathname]);

  // The app shell owns the persistent scroll container, and Next may preserve
  // a list route in its client cache rather than remounting it on browser Back.
  // Restore after the route commit (and again across two frames) so framework
  // scroll handling cannot overwrite the semantic article-row anchor.
  useLayoutEffect(() => {
    if (pathname.startsWith("/article/")) return;
    const pending = getLatestReadingReturnAnchor();
    if (!pending) return;
    // Article-return anchors are only valid for the inbox/feed list at `/`.
    // Visiting Sent, Settings, or another app section turns the next inbox
    // visit into a fresh load instead of a delayed article-detail return.
    if (pathname !== "/") {
      clearReadingReturnAnchor(pending.key);
      return;
    }

    const restore = () => {
      const scroller = document.querySelector<HTMLElement>("main");
      const article = document.querySelector<HTMLElement>(
        `[data-article-id="${pending.anchor.articleId}"]`,
      );
      if (!scroller || !article) return false;
      const currentOffset =
        article.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
      scroller.scrollTop += currentOffset - pending.anchor.offset;
      return true;
    };

    restore();
    let secondFrame = 0;
    const firstFrame = requestAnimationFrame(() => {
      restore();
      secondFrame = requestAnimationFrame(() => {
        if (restore()) clearReadingReturnAnchor(pending.key);
      });
    });
    return () => {
      cancelAnimationFrame(firstFrame);
      cancelAnimationFrame(secondFrame);
    };
  }, [pathname]);

  if (!ready || !authed) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <span className="wordmark fade-up text-display-lg">
          NewsRead<span className="dot">.</span>
        </span>
      </div>
    );
  }

  if (suspended) {
    // The token is valid but the server refuses every request (403
    // "Account suspended") — a dedicated screen instead of an app whose
    // every fetch errors.
    return (
      <div className="fade-up flex min-h-dvh flex-col items-center justify-center px-8 text-center">
        <span className="wordmark text-display-lg">
          NewsRead<span className="dot">.</span>
        </span>
        <p className="mt-6 text-lead font-medium" style={{ color: "var(--ink-dim)" }}>
          Your account is suspended.
        </p>
        <p className="mt-1.5 max-w-[420px] text-body" style={{ color: "var(--ink-faint)" }}>
          Reading and syncing are paused for this account. If you think this is a mistake,
          contact the administrator of this NewsRead instance.
        </p>
        <button className="btn mt-6" onClick={logout}>
          Sign out
        </button>
      </div>
    );
  }

  return (
    <MobileNavContext.Provider value={openNav}>
    <Suspense fallback={null}>
      <BackNavTracker />
    </Suspense>
    <div className="flex">
      {/* Desktop: persistent sidebar */}
      <div className="hidden md:block">
        <Suspense fallback={<div className="w-[250px] shrink-0" />}>
          <Sidebar />
        </Suspense>
      </div>

      {/* Mobile: slide-in drawer + scrim */}
      <div className="md:hidden">
        {navOpen && (
          <div
            className="fixed inset-0 z-40"
            style={{ background: "var(--bg-scrim)" }}
            onClick={() => setNavOpen(false)}
          />
        )}
        <div
          className="fixed inset-y-0 left-0 z-50 transition-transform duration-200 ease-out"
          style={{ transform: navOpen ? "translateX(0)" : "translateX(-100%)" }}
          onClick={(e) => {
            // Tapping any link inside the drawer closes it, even when the
            // target route is already active (pathname unchanged).
            if ((e.target as HTMLElement).closest("a")) setNavOpen(false);
          }}
        >
          <Suspense fallback={null}>
            <Sidebar />
          </Suspense>
        </div>
      </div>

      <div className="flex h-dvh min-w-0 flex-1 flex-col">
        {/* Mobile top bar (reading routes bring their own) */}
        {!pageOwnsBar && (
          <header
            className="flex shrink-0 items-center gap-1 border-b px-3 py-2 md:hidden"
            style={{
              background: "var(--bg-header)",
              backdropFilter: "blur(10px)",
              borderColor: "var(--line-soft)",
            }}
          >
            <button className="icon-btn" aria-label="Open navigation" onClick={openNav}>
              <MenuIcon size={18} />
            </button>
            <Link href="/" className="wordmark text-lead">
              NewsRead<span className="dot">.</span>
            </Link>
          </header>
        )}

        <main className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">{children}</main>
      </div>
    </div>
    </MobileNavContext.Provider>
  );
}
