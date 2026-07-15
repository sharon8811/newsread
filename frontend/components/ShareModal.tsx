"use client";

import { useEffect, useRef, useState } from "react";
import useSWR, { mutate } from "swr";
import {
  api,
  fetcher,
  type AiStatus,
  type Article,
  type Share,
  type ShareTarget,
  type UserPublic,
} from "@/lib/api";
import {
  CheckIcon,
  ExternalIcon,
  ShareIcon,
  SlackIcon,
  SparkleIcon,
  TeamsIcon,
  WhatsAppIcon,
  XIcon,
} from "./icons";
import Modal, { ModalClose, ModalTitle } from "./Modal";
import Avatar from "./ui/Avatar";
import Badge from "./ui/Badge";
import Chip from "./ui/Chip";
import ErrorText from "./ui/ErrorText";

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
  const [aiBusy, setAiBusy] = useState(false);
  const [appShareBusy, setAppShareBusy] = useState(false);
  const [appShareStatus, setAppShareStatus] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  // External targets selected for this share; internal share tracked separately
  // so a retry after a partial failure doesn't re-send what already went out.
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [whatsapp, setWhatsapp] = useState(false);
  const [internalSent, setInternalSent] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  const { data: targets } = useSWR<ShareTarget[]>("/share-targets", fetcher);
  const { data: aiStatus } = useSWR<AiStatus>("/ai/status", fetcher);

  useEffect(() => {
    const q = query.trim().replace(/^@/, "");
    if (!q) return;
    const t = setTimeout(() => {
      api<UserPublic[]>(`/users/search?q=${encodeURIComponent(q)}`)
        .then((users) =>
          setResults(users.filter((u) => !recipients.some((r) => r.id === u.id))),
        )
        .catch(() => setResults([]));
    }, 200);
    return () => clearTimeout(t);
  }, [query, recipients]);

  function addRecipient(user: UserPublic) {
    setRecipients((r) => [...r, user]);
    setQuery("");
    setResults([]);
    searchRef.current?.focus();
  }

  function toggleTarget(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function suggestMessage() {
    if (aiBusy) return;
    setAiBusy(true);
    setError(null);
    try {
      const res = await api<{ message: string }>("/ai/share-message", {
        method: "POST",
        body: { article_id: article.id, draft: note },
      });
      setNote(res.message);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The AI suggestion failed");
    } finally {
      setAiBusy(false);
    }
  }

  async function shareWithApp() {
    if (appShareBusy) return;
    setError(null);
    setAppShareStatus(null);

    const trimmedNote = note.trim();
    const shareData: ShareData = {
      title: article.title,
      url: article.url,
      ...(trimmedNote ? { text: trimmedNote } : {}),
    };

    if (typeof navigator.share !== "function") {
      if (typeof navigator.clipboard?.writeText !== "function") {
        setError("App sharing is not supported in this browser");
        return;
      }
      try {
        const text = trimmedNote ? `${trimmedNote}\n${article.url}` : article.url;
        await navigator.clipboard.writeText(text);
        setAppShareStatus("Message and link copied. Paste them into any app.");
      } catch {
        setError("Could not open the app picker or copy the link");
      }
      return;
    }

    setAppShareBusy(true);
    try {
      await navigator.share(shareData);
      setSent(true);
      setTimeout(onClose, 900);
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        setError(err instanceof Error ? err.message : "Could not open the app picker");
      }
    } finally {
      setAppShareBusy(false);
    }
  }

  const nothingChosen =
    recipients.length === 0 && selected.size === 0 && !whatsapp;

  async function submit() {
    if (nothingChosen || busy) return;
    setBusy(true);
    setError(null);
    const failures: string[] = [];

    if (recipients.length > 0 && !internalSent) {
      try {
        await api<Share>("/shares", {
          method: "POST",
          body: {
            article_id: article.id,
            recipients: recipients.map((r) => r.username),
            note: note.trim() || null,
          },
        });
        setInternalSent(true);
        mutate("/shares/sent");
      } catch (err) {
        failures.push(err instanceof Error ? err.message : "Could not share");
      }
    }

    for (const target of targets?.filter((t) => selected.has(t.id)) ?? []) {
      try {
        await api("/shares/external", {
          method: "POST",
          body: { article_id: article.id, message: note.trim(), target_id: target.id },
        });
        setSelected((prev) => {
          const next = new Set(prev);
          next.delete(target.id);
          return next;
        });
      } catch (err) {
        failures.push(
          `${target.display_name}: ${err instanceof Error ? err.message : "failed"}`,
        );
      }
    }

    if (whatsapp) {
      const text = note.trim() ? `${note.trim()}\n${article.url}` : article.url;
      window.open(`https://wa.me/?text=${encodeURIComponent(text)}`, "_blank");
      setWhatsapp(false);
    }

    if (failures.length > 0) {
      setError(failures.join(" · "));
      setBusy(false);
      return;
    }
    setSent(true);
    setTimeout(onClose, 900);
  }

  return (
    <Modal onClose={onClose} contentClassName="max-h-[calc(100dvh-3rem)] overflow-y-auto p-6">
        {sent ? (
          <div className="flex flex-col items-center gap-3 py-10">
            <ModalTitle className="sr-only">Article shared</ModalTitle>
            <span
              className="flex h-12 w-12 items-center justify-center rounded-full"
              style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
            >
              <CheckIcon size={22} />
            </span>
            <p className="text-[16px] font-semibold tracking-tight">Shared.</p>
          </div>
        ) : (
          <>
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="mono-label">Share with context</p>
                <ModalTitle asChild>
                  <h2 className="font-serif-nr mt-1.5 text-[19px] leading-snug">
                    {article.title}
                  </h2>
                </ModalTitle>
              </div>
              <ModalClose asChild>
                <button
                  className="icon-btn min-h-11 min-w-11 shrink-0"
                  aria-label="Close share dialog"
                >
                  <XIcon size={16} />
                </button>
              </ModalClose>
            </div>

            <div className="relative mt-5">
              {recipients.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {recipients.map((r) => (
                    <Badge
                      key={r.id}
                      tone="accent-strong"
                      className="gap-1.5 px-2.5 py-1 text-[12px]"
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
                    </Badge>
                  ))}
                </div>
              )}
              <input
                ref={searchRef}
                className="input"
                placeholder="@username: who should read this?"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  if (!e.target.value.trim().replace(/^@/, "")) setResults([]);
                }}
                autoFocus
              />
              {results.length > 0 && (
                <div
                  className="absolute left-0 right-0 top-full z-10 mt-1.5 overflow-hidden rounded-md border"
                  style={{ background: "var(--bg-raised)", borderColor: "var(--line)" }}
                >
                  {results.map((u) => (
                    <button
                      key={u.id}
                      className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left transition-colors hover:bg-[var(--bg-hover)]"
                      onClick={() => addRecipient(u)}
                    >
                      <Avatar name={u.name} />
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

            {/* External quick-share targets (saved in Settings) + WhatsApp handoff */}
            <div className="mt-4 flex flex-wrap gap-1.5">
              {targets?.map((target) => {
                const active = selected.has(target.id);
                return (
                  <Chip key={target.id} active={active} onClick={() => toggleTarget(target.id)}>
                    {target.platform === "slack" ? (
                      <SlackIcon size={12} />
                    ) : (
                      <TeamsIcon size={12} />
                    )}
                    {target.platform === "slack"
                      ? target.display_name.replace(/^#\s*/, "")
                      : target.display_name}
                    {active && <CheckIcon size={11} />}
                  </Chip>
                );
              })}
              <Chip
                active={whatsapp}
                onClick={() => setWhatsapp((v) => !v)}
                title="Opens WhatsApp with the message prefilled"
              >
                <WhatsAppIcon size={12} />
                WhatsApp
                {whatsapp && <CheckIcon size={11} />}
              </Chip>
            </div>

            <textarea
              className="input mt-3 resize-none font-serif-nr italic"
              style={{ fontSize: 15.5, minHeight: 96 }}
              placeholder="Why are you sharing this? Sent as your note and as the chat message."
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            {aiStatus?.configured && (
              <div className="mt-2 flex justify-end">
                <button
                  className="btn"
                  style={{ fontSize: 12 }}
                  disabled={aiBusy}
                  onClick={suggestMessage}
                >
                  <SparkleIcon size={13} />
                  {aiBusy
                    ? "Thinking…"
                    : note.trim()
                      ? "Refine with AI"
                      : "Draft with AI"}
                </button>
              </div>
            )}

            {appShareStatus && (
              <p
                className="mt-2 text-right text-[12px]"
                role="status"
                style={{ color: "var(--ink-faint)" }}
              >
                {appShareStatus}
              </p>
            )}

            {error && (
              <ErrorText className="mt-2">
                {error}
              </ErrorText>
            )}

            <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="font-mono-nr text-[11px]" style={{ color: "var(--ink-faint)" }}>
                {nothingChosen
                  ? "Select a reader or channel"
                  : [
                      recipients.length > 0 &&
                        `${recipients.length} reader${recipients.length > 1 ? "s" : ""}`,
                      selected.size > 0 &&
                        `${selected.size} channel${selected.size > 1 ? "s" : ""}`,
                      whatsapp && "WhatsApp",
                    ]
                      .filter(Boolean)
                      .join(" · ")}
              </p>
              <div className="flex items-center justify-end gap-2">
                <button
                  className="btn"
                  disabled={appShareBusy}
                  onClick={shareWithApp}
                  title="Open your device's app picker"
                >
                  <ExternalIcon size={13} />
                  {appShareBusy ? "Opening…" : "Choose an app"}
                </button>
                <button
                  className="btn btn-accent"
                  disabled={nothingChosen || busy}
                  onClick={submit}
                >
                  <ShareIcon size={14} />
                  {busy ? "Sending…" : "Send"}
                </button>
              </div>
            </div>
          </>
        )}
    </Modal>
  );
}
