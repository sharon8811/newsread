"use client";

import { useEffect, useRef, useState } from "react";
import useSWR, { mutate } from "swr";
import {
  api,
  fetcher,
  type AiStatus,
  type ArticleDetail,
  type ChatMessage,
} from "@/lib/api";
import { CommentIcon, ShareIcon } from "./icons";

const SUGGESTIONS = [
  "What are the key points?",
  "Why does this matter?",
  "What is the counterargument?",
];

function TypingDots() {
  return (
    <span className="inline-flex items-center gap-1 px-1 py-2">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="typing-dot"
          style={{ animationDelay: `${i * 0.18}s` }}
        />
      ))}
    </span>
  );
}

export default function QAPanel({ article }: { article: ArticleDetail }) {
  const { data: status } = useSWR<AiStatus>("/ai/status", fetcher);
  const key = `/articles/${article.id}/qa`;
  const { data: messages } = useSWR<ChatMessage[]>(
    status?.configured ? key : null,
    fetcher,
  );
  const [input, setInput] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if ((messages && messages.length > 0) || pending) {
      bottomRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [messages, pending]);

  if (!status?.configured) return null;

  async function send(question: string) {
    const q = question.trim();
    if (!q || pending) return;
    setPending(q);
    setInput("");
    setError(null);
    try {
      await api(key, { method: "POST", body: { content: q } });
      await mutate(key);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The assistant could not answer");
      setInput(q);
    } finally {
      setPending(null);
    }
  }

  return (
    <section className="mt-10 border-t pt-7" style={{ borderColor: "var(--line-soft)" }}>
      <div className="flex items-center gap-2">
        <CommentIcon size={13} />
        <span className="mono-label">Ask the article</span>
      </div>

      {(!messages || messages.length === 0) && !pending && (
        <div className="mt-4 flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              className="btn"
              style={{ fontSize: 12.5, padding: "6px 12px" }}
              onClick={() => send(s)}
            >
              {s}
            </button>
          ))}
        </div>
      )}

      <div className="mt-4 flex flex-col gap-4">
        {messages?.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="flex justify-end">
              <p
                className="max-w-[85%] rounded-lg rounded-br-sm px-4 py-2.5 text-[13.5px]"
                style={{ background: "var(--accent-soft)", color: "var(--ink)" }}
              >
                {m.content}
              </p>
            </div>
          ) : (
            <div
              key={m.id}
              className="font-serif-nr max-w-[95%] whitespace-pre-line border-l pl-4 text-[15.5px] leading-relaxed"
              style={{ borderColor: "var(--line)" }}
            >
              {m.content}
            </div>
          ),
        )}
        {pending && (
          <>
            <div className="flex justify-end">
              <p
                className="max-w-[85%] rounded-lg rounded-br-sm px-4 py-2.5 text-[13.5px]"
                style={{ background: "var(--accent-soft)", color: "var(--ink)" }}
              >
                {pending}
              </p>
            </div>
            <div className="border-l pl-4" style={{ borderColor: "var(--line)" }}>
              <TypingDots />
            </div>
          </>
        )}
        <div ref={bottomRef} />
      </div>

      {error && (
        <p className="mt-3 text-[13px]" style={{ color: "var(--danger)" }}>
          {error}
        </p>
      )}

      <form
        className="mt-5 flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <input
          className="input"
          placeholder="Ask anything about this article…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={pending !== null}
        />
        <button
          className="btn btn-accent shrink-0"
          type="submit"
          disabled={!input.trim() || pending !== null}
          style={{ padding: "9px 14px" }}
        >
          <ShareIcon size={14} />
        </button>
      </form>
    </section>
  );
}
