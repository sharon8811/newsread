"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";

export default function RegisterPage() {
  const { user, ready, register } = useAuth();
  const router = useRouter();
  const [form, setForm] = useState({ name: "", username: "", email: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (ready && user) router.replace("/");
  }, [ready, user, router]);

  function set<K extends keyof typeof form>(key: K, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await register({
        name: form.name.trim(),
        username: form.username.trim(),
        email: form.email.trim(),
        password: form.password,
      });
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create account");
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
          Share what you read, with your take attached.
        </p>

        <form onSubmit={submit} className="mt-10 flex flex-col gap-4">
          <div>
            <label className="mono-label mb-1.5 block">Name</label>
            <input
              className="input"
              value={form.name}
              onChange={(e) => set("name", e.target.value)}
              autoFocus
              required
            />
          </div>
          <div>
            <label className="mono-label mb-1.5 block">Username</label>
            <input
              className="input"
              value={form.username}
              onChange={(e) => set("username", e.target.value)}
              pattern="[a-zA-Z0-9_]{3,30}"
              title="3–30 characters: letters, numbers, underscores"
              required
            />
            <p className="mt-1 text-[11.5px]" style={{ color: "var(--ink-faint)" }}>
              Friends will @mention you with this.
            </p>
          </div>
          <div>
            <label className="mono-label mb-1.5 block">Email</label>
            <input
              className="input"
              type="email"
              value={form.email}
              onChange={(e) => set("email", e.target.value)}
              required
            />
          </div>
          <div>
            <label className="mono-label mb-1.5 block">Password</label>
            <input
              className="input"
              type="password"
              value={form.password}
              onChange={(e) => set("password", e.target.value)}
              minLength={8}
              required
            />
          </div>
          {error && (
            <p className="text-[13px]" style={{ color: "var(--danger)" }}>
              {error}
            </p>
          )}
          <button className="btn btn-accent mt-1 w-full py-2.5" disabled={busy} type="submit">
            {busy ? "Creating account…" : "Create account"}
          </button>
        </form>

        <p className="mt-6 text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
          Already reading?{" "}
          <Link href="/login" style={{ color: "var(--accent-bright)" }}>
            Sign in
          </Link>
        </p>
      </div>
    </main>
  );
}
