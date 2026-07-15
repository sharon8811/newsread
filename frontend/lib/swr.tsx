"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";
import { SWRConfig } from "swr";
import { toast } from "sonner";
import { ApiError, fetcher, getToken } from "./api";
import { useAuth } from "./auth";

// Global SWR defaults: the shared fetcher, plus session-expiry handling — a
// 401 while a token is present means it expired or was revoked, so log out
// and return to login instead of leaving every request in silent error state.
export function SWRProvider({ children }: { children: React.ReactNode }) {
  const { logout } = useAuth();
  const router = useRouter();

  const onError = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError && err.status === 401 && getToken()) {
        logout();
        toast.error("Your session expired — sign in again.");
        router.replace("/login");
      }
    },
    [logout, router],
  );

  return <SWRConfig value={{ fetcher, onError }}>{children}</SWRConfig>;
}
