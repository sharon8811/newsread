"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { useServerConfig } from "@/lib/queries";
import Button from "@/components/ui/Button";
import ErrorText from "@/components/ui/ErrorText";
import Field from "@/components/ui/Field";

export default function RegisterPage() {
  const { authed, ready, register } = useAuth();
  const { data: config } = useServerConfig();
  const router = useRouter();
  const [form, setForm] = useState({ name: "", username: "", email: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (ready && authed) router.replace("/");
  }, [ready, authed, router]);

  // Signups closed (single-user self-hosted deployments): send visitors to
  // sign in instead. The form stays hidden until the flags load so it never
  // flashes and then vanishes.
  useEffect(() => {
    if (config && !config.allow_signup) router.replace("/login");
  }, [config, router]);

  if (!config?.allow_signup) return null;

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
        <p className="font-serif-nr mt-1 text-lead italic" style={{ color: "var(--ink-dim)" }}>
          Share what you read, with your take attached.
        </p>

        <form onSubmit={submit} className="mt-10 flex flex-col gap-4">
          <Field
            label="Name"
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
            autoFocus
            required
          />
          <Field
            label="Username"
            value={form.username}
            onChange={(e) => set("username", e.target.value)}
            pattern="[a-zA-Z0-9_]{3,30}"
            title="3–30 characters: letters, numbers, underscores"
            hint="Friends will @mention you with this."
            required
          />
          <Field
            label="Email"
            type="email"
            value={form.email}
            onChange={(e) => set("email", e.target.value)}
            required
          />
          <Field
            label="Password"
            type="password"
            value={form.password}
            onChange={(e) => set("password", e.target.value)}
            minLength={8}
            required
          />
          <ErrorText>{error}</ErrorText>
          <Button
            variant="primary"
            type="submit"
            loading={busy}
            className="mt-1 w-full py-2.5"
          >
            {busy ? "Creating account…" : "Create account"}
          </Button>
        </form>

        <p className="mt-6 text-body" style={{ color: "var(--ink-faint)" }}>
          Already reading?{" "}
          <Link href="/login" style={{ color: "var(--accent-bright)" }}>
            Sign in
          </Link>
        </p>
      </div>
    </main>
  );
}
