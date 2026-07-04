"use client";

import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import { MenuIcon } from "@/components/icons";
import { useAuth } from "@/lib/auth";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, ready } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    if (ready && !user) router.replace("/login");
  }, [ready, user, router]);

  // Close the drawer whenever navigation happens.
  useEffect(() => setNavOpen(false), [pathname]);

  if (!ready || !user) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <span className="wordmark fade-up text-[26px]">
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
          <Link href="/" className="wordmark text-[17px]">
            NewsRead<span className="dot">.</span>
          </Link>
        </header>

        <main className="min-h-0 flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}
