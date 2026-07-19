"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, type ArticleDetail } from "@/lib/api";
import { useMutation } from "@/lib/useMutation";
import { mutateArticleLists } from "./ArticleList";
import Modal, { ModalHeader } from "./Modal";
import ErrorText from "./ui/ErrorText";

export default function ImportUrlModal({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const [url, setUrl] = useState("");

  const { run: importUrl, busy, error } = useMutation(
    (u: string) => api<ArticleDetail>("/imports", { method: "POST", body: { url: u } }),
    {
      fallbackError: "Could not import that link",
      onSuccess(article) {
        // The Imported list gained (or re-surfaced) a row; the article page
        // itself polls while extraction runs in the background.
        mutateArticleLists();
        onClose();
        router.push(`/article/${article.id}`);
      },
    },
  );

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (url.trim()) importUrl(url.trim());
  }

  return (
    <Modal onClose={onClose} contentClassName="p-6">
      <ModalHeader eyebrow="Imported" title="Add a link" />
      <p className="mt-2 text-body-sm leading-relaxed" style={{ color: "var(--ink-dim)" }}>
        Paste any article URL — it gets fetched, summarized, and kept here so
        you can read it, pin it to a project, or share it.
      </p>
      <form onSubmit={submit} className="mt-4">
        <input
          className="input"
          placeholder="https://example.com/article"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          autoFocus
        />
        <ErrorText className="mt-1.5">{error}</ErrorText>
        <button
          className="btn btn-accent mt-3 w-full"
          disabled={busy || !url.trim()}
          type="submit"
        >
          {busy ? "Importing…" : "Import"}
        </button>
      </form>
    </Modal>
  );
}
