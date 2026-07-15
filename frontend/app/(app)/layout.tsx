"use client";

import Link from "next/link";
import { Suspense, useEffect, useLayoutEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import { MenuIcon } from "@/components/icons";
import { useAuth } from "@/lib/auth";
import {
  clearReadingReturnAnchor,
  getLatestReadingReturnAnchor,
} from "@/lib/readingSession";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { authed, ready } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [navOpen, setNavOpen] = useState(false);

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

  return (
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
        {/* Mobile top bar */}
        <header
          className="flex shrink-0 items-center gap-1 border-b px-3 py-2 md:hidden"
          style={{
            background: "var(--bg-header)",
            backdropFilter: "blur(10px)",
            borderColor: "var(--line-soft)",
          }}
        >
          <button
            className="icon-btn"
            aria-label="Open navigation"
            onClick={() => setNavOpen(true)}
          >
            <MenuIcon size={18} />
          </button>
          <Link href="/" className="wordmark text-lead">
            NewsRead<span className="dot">.</span>
          </Link>
        </header>

        <main className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">{children}</main>
      </div>
    </div>
  );
}
