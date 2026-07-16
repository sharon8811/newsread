"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { useServerConfig } from "@/lib/queries";
import Button from "@/components/ui/Button";
import ErrorText from "@/components/ui/ErrorText";
import Field from "@/components/ui/Field";

export default function LoginPage() {
  const { authed, ready, login } = useAuth();
  const { data: config } = useServerConfig();
  const router = useRouter();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (ready && authed) router.replace("/");
  }, [ready, authed, router]);

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
    <main className="flex min-h-dvh items-center justify-center px-4 py-8 sm:px-6">
      <div className="fade-up w-full max-w-[380px]">
        <h1 className="wordmark text-[40px]">
          NewsRead<span className="dot">.</span>
        </h1>
        <p className="font-serif-nr mt-1 text-lead italic" style={{ color: "var(--ink-dim)" }}>
          Read together.
        </p>

        <form onSubmit={submit} className="mt-10 flex flex-col gap-4">
          <Field
            label="Email or username"
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            autoFocus
            required
          />
          <Field
            label="Password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <ErrorText>{error}</ErrorText>
          <Button
            variant="primary"
            type="submit"
            loading={busy}
            className="mt-1 w-full py-2.5"
          >
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>

        {config?.allow_signup && (
          <p className="mt-6 text-body" style={{ color: "var(--ink-faint)" }}>
            New here?{" "}
            <Link href="/register" style={{ color: "var(--accent-bright)" }}>
              Create an account
            </Link>
          </p>
        )}
      </div>
    </main>
  );
}
