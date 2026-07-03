"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";

export default function LoginPage() {
  const { user, ready, login } = useAuth();
  const router = useRouter();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (ready && user) router.replace("/");
  }, [ready, user, router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await login(identifier.trim(), password);
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not sign in");
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="fade-up w-full max-w-[380px]">
        <h1 className="wordmark text-[40px]">
          NewsRead<span className="dot">.</span>
        </h1>
        <p className="font-serif-nr mt-1 text-[17px] italic" style={{ color: "var(--ink-dim)" }}>
          Read together.
        </p>

        <form onSubmit={submit} className="mt-10 flex flex-col gap-4">
          <div>
            <label className="mono-label mb-1.5 block">Email or username</label>
            <input
              className="input"
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              autoFocus
              required
            />
          </div>
          <div>
            <label className="mono-label mb-1.5 block">Password</label>
            <input
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          {error && (
            <p className="text-[13px]" style={{ color: "var(--danger)" }}>
              {error}
            </p>
          )}
          <button className="btn btn-accent mt-1 w-full py-2.5" disabled={busy} type="submit">
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="mt-6 text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
          New here?{" "}
          <Link href="/register" style={{ color: "var(--accent-bright)" }}>
            Create an account
          </Link>
        </p>
      </div>
    </main>
  );
}
