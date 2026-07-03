"use client";

import { Suspense, useEffect } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import { useAuth } from "@/lib/auth";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, ready } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (ready && !user) router.replace("/login");
  }, [ready, user, router]);

  if (!ready || !user) {
    return (
      <div className="flex h-screen items-center justify-center">
        <span className="wordmark fade-up text-[26px]">
          NewsRead<span className="dot">.</span>
        </span>
      </div>
    );
  }

  return (
    <div className="flex">
      <Suspense fallback={<div className="w-[250px] shrink-0" />}>
        <Sidebar />
      </Suspense>
      <main className="h-screen flex-1 overflow-y-auto">{children}</main>
    </div>
  );
}
