"use client";

import { useState } from "react";
import { mutate } from "swr";
import {
  api,
  type Article,
  type Share,
  type UserPublic,
} from "@/lib/api";
import { keys } from "@/lib/keys";
import { useAiStatus } from "@/lib/queries";
import {
  CheckIcon,
  ExternalIcon,
  ShareIcon,
  SparkleIcon,
  WhatsAppIcon,
  XIcon,
} from "./icons";
import Modal, { ModalClose, ModalTitle } from "./Modal";
import ShareDestinationPicker, {
  type ExternalShareDestination,
} from "./ShareDestinationPicker";
import Chip from "./ui/Chip";
import ErrorText from "./ui/ErrorText";

export default function ShareModal({
  article,
  onClose,
}: {
  article: Article;
  onClose: () => void;
}) {
  const [recipients, setRecipients] = useState<UserPublic[]>([]);
  const [externalDestinations, setExternalDestinations] = useState<
    ExternalShareDestination[]
  >([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [aiBusy, setAiBusy] = useState(false);
  const [appShareBusy, setAppShareBusy] = useState(false);
  const [appShareStatus, setAppShareStatus] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  // External destinations selected for this share; internal share tracked separately
  // so a retry after a partial failure doesn't re-send what already went out.
  const [whatsapp, setWhatsapp] = useState(false);
  const [internalSent, setInternalSent] = useState(false);

  const { data: aiStatus } = useAiStatus();

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
    recipients.length === 0 && externalDestinations.length === 0 && !whatsapp;

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
        mutate(keys.sharesSent);
      } catch (err) {
        failures.push(err instanceof Error ? err.message : "Could not share");
      }
    }

    for (const destination of externalDestinations) {
      try {
        await api("/shares/external", {
          method: "POST",
          body: {
            article_id: article.id,
            message: note.trim(),
            ...(destination.savedId
              ? { target_id: destination.savedId }
              : {
                  target: {
                    platform: destination.platform,
                    external_id: destination.externalId,
                    display_name: destination.displayName,
                    target_type: destination.targetType,
                    meta: destination.meta,
                  },
                }),
          },
        });
        setExternalDestinations((current) =>
          current.filter((item) => item.key !== destination.key),
        );
      } catch (err) {
        failures.push(
          `${destination.displayName}: ${err instanceof Error ? err.message : "failed"}`,
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
    <Modal
      onClose={onClose}
      contentClassName="max-h-[calc(100dvh-1.5rem)] overflow-y-auto p-4 sm:max-h-[calc(100dvh-3rem)] sm:p-6"
    >
        {sent ? (
          <div className="flex flex-col items-center gap-3 py-10">
            <ModalTitle className="sr-only">Article shared</ModalTitle>
            <span
              className="flex h-12 w-12 items-center justify-center rounded-full"
              style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
            >
              <CheckIcon size={22} />
            </span>
            <p className="text-lead font-semibold tracking-tight">Shared.</p>
          </div>
        ) : (
          <>
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="mono-label">Share with context</p>
                <ModalTitle asChild>
                  <h2 className="font-serif-nr mt-1.5 text-title leading-snug">
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

            <div className="mt-5">
              <label htmlFor="share-message" className="text-body-sm font-medium">
                Message
                <span className="ml-1 font-normal" style={{ color: "var(--ink-faint)" }}>
                  optional
                </span>
              </label>
              <textarea
                id="share-message"
                className="input mt-1.5 min-h-24 resize-none font-serif-nr text-[16px] italic sm:text-[15.5px]"
                placeholder="Add context for the people or app you share with"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                autoFocus
              />
            </div>
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

            <ShareDestinationPicker
              recipients={recipients}
              externalDestinations={externalDestinations}
              onAddRecipient={(recipient) =>
                setRecipients((current) => [...current, recipient])
              }
              onRemoveRecipient={(userId) =>
                setRecipients((current) => current.filter((recipient) => recipient.id !== userId))
              }
              onAddExternal={(destination) =>
                setExternalDestinations((current) => [...current, destination])
              }
              onRemoveExternal={(key) =>
                setExternalDestinations((current) =>
                  current.filter((destination) => destination.key !== key),
                )
              }
            />

            <div className="mt-2.5 flex items-center justify-between gap-3">
              <Chip
                active={whatsapp}
                onClick={() => setWhatsapp((value) => !value)}
                title="Opens WhatsApp with the message prefilled"
              >
                <WhatsAppIcon size={12} />
                WhatsApp
                {whatsapp && <CheckIcon size={11} />}
              </Chip>
              {(recipients.length > 0 || externalDestinations.length > 0) && (
                <p className="font-mono-nr text-label" style={{ color: "var(--ink-faint)" }}>
                  {recipients.length + externalDestinations.length} selected
                </p>
              )}
            </div>

            {appShareStatus && (
              <p
                className="mt-2 text-right text-body-sm"
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

            <div className="mt-4 grid grid-cols-2 gap-2">
                <button
                  className="btn btn-accent w-full"
                  disabled={appShareBusy}
                  onClick={shareWithApp}
                  title="Open your device's app picker"
                >
                  <ExternalIcon size={13} />
                  {appShareBusy ? "Opening…" : "Share to app"}
                </button>
                <button
                  className="btn w-full"
                  disabled={nothingChosen || busy}
                  onClick={submit}
                >
                  <ShareIcon size={14} />
                  {busy ? "Sending…" : "Send"}
                </button>
            </div>
          </>
        )}
    </Modal>
  );
}
