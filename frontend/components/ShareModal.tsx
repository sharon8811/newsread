"use client";

import { useEffect, useRef, useState } from "react";
import { mutate } from "swr";
import { api, type Article, type Share, type UserPublic } from "@/lib/api";
import { CheckIcon, ShareIcon, XIcon } from "./icons";

export default function ShareModal({
  article,
  onClose,
}: {
  article: Article;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<UserPublic[]>([]);
  const [recipients, setRecipients] = useState<UserPublic[]>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const q = query.trim().replace(/^@/, "");
    if (!q) {
      setResults([]);
      return;
    }
    const t = setTimeout(() => {
      api<UserPublic[]>(`/users/search?q=${encodeURIComponent(q)}`)
        .then((users) =>
          setResults(users.filter((u) => !recipients.some((r) => r.id === u.id))),
        )
        .catch(() => setResults([]));
    }, 200);
    return () => clearTimeout(t);
  }, [query, recipients]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function addRecipient(user: UserPublic) {
    setRecipients((r) => [...r, user]);
    setQuery("");
    setResults([]);
    searchRef.current?.focus();
  }

  async function submit() {
    if (recipients.length === 0 || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api<Share>("/shares", {
        method: "POST",
        body: {
          article_id: article.id,
          recipients: recipients.map((r) => r.username),
          note: note.trim() || null,
        },
      });
      mutate("/shares/sent");
      setSent(true);
      setTimeout(onClose, 900);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not share");
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-6"
      style={{ background: "rgba(8, 6, 4, 0.72)", backdropFilter: "blur(4px)" }}
      onClick={onClose}
    >
      <div
        className="fade-up w-full max-w-[480px] rounded-2xl border p-6"
        style={{
          background: "var(--bg-raised)",
          borderColor: "var(--line)",
          boxShadow: "0 24px 80px rgba(0,0,0,0.55)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {sent ? (
          <div className="flex flex-col items-center gap-3 py-10">
            <span
              className="flex h-12 w-12 items-center justify-center rounded-full"
              style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
            >
              <CheckIcon size={22} />
            </span>
            <p className="font-serif-nr text-[18px] italic">Shared.</p>
          </div>
        ) : (
          <>
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="mono-label">Share with context</p>
                <h2 className="font-serif-nr mt-1.5 text-[19px] leading-snug">
                  {article.title}
                </h2>
              </div>
              <button className="icon-btn shrink-0" onClick={onClose}>
                <XIcon size={16} />
              </button>
            </div>

            <div className="relative mt-5">
              {recipients.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {recipients.map((r) => (
                    <span
                      key={r.id}
                      className="font-mono-nr flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px]"
                      style={{
                        borderColor: "var(--accent-border)",
                        background: "var(--accent-soft)",
                        color: "var(--accent-bright)",
                      }}
                    >
                      @{r.username}
                      <button
                        className="opacity-70 hover:opacity-100"
                        onClick={() =>
                          setRecipients((rs) => rs.filter((x) => x.id !== r.id))
                        }
                      >
                        <XIcon size={11} />
                      </button>
                    </span>
                  ))}
                </div>
              )}
              <input
                ref={searchRef}
                className="input"
                placeholder="@username — who should read this?"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                autoFocus
              />
              {results.length > 0 && (
                <div
                  className="absolute left-0 right-0 top-full z-10 mt-1.5 overflow-hidden rounded-lg border"
                  style={{ background: "var(--bg-raised)", borderColor: "var(--line)" }}
                >
                  {results.map((u) => (
                    <button
                      key={u.id}
                      className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left transition-colors hover:bg-[var(--bg-hover)]"
                      onClick={() => addRecipient(u)}
                    >
                      <span
                        className="flex h-7 w-7 items-center justify-center rounded-full font-serif-nr text-[12px] italic"
                        style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
                      >
                        {u.name[0]?.toUpperCase()}
                      </span>
                      <span className="text-[13.5px]">{u.name}</span>
                      <span
                        className="font-mono-nr text-[11.5px]"
                        style={{ color: "var(--ink-faint)" }}
                      >
                        @{u.username}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            <textarea
              className="input mt-3 resize-none font-serif-nr italic"
              style={{ fontSize: 15.5, minHeight: 96 }}
              placeholder="Why are you sharing this? Your note is the first thing they will see."
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />

            {error && (
              <p className="mt-2 text-[12.5px]" style={{ color: "var(--danger)" }}>
                {error}
              </p>
            )}

            <div className="mt-4 flex items-center justify-between">
              <p className="font-mono-nr text-[11px]" style={{ color: "var(--ink-faint)" }}>
                {recipients.length === 0
                  ? "Add at least one reader"
                  : `${recipients.length} reader${recipients.length > 1 ? "s" : ""}`}
              </p>
              <button
                className="btn btn-accent"
                disabled={recipients.length === 0 || busy}
                onClick={submit}
              >
                <ShareIcon size={14} />
                {busy ? "Sending…" : "Send"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
