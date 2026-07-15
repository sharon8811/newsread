"use client";

import { memo, useDeferredValue, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR, { mutate } from "swr";
import {
  fetcher,
  type ChatMessage,
  type QAStreamEvent,
  type ToolEvent,
} from "@/lib/api";
import { useAiStatus } from "@/lib/queries";
import { CheckIcon, CommentIcon, ExternalIcon, RefreshIcon, SearchIcon, ShareIcon } from "./icons";
import ErrorText from "./ui/ErrorText";

type LiveToolCall = ToolEvent & { id: string; done: boolean };

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

function hostOf(url: unknown): string {
  try {
    return new URL(String(url)).hostname.replace(/^www\./, "");
  } catch {
    return String(url ?? "");
  }
}

function toolLabel(name: string, args: Record<string, unknown>): string {
  if (name === "tavily_search") return `Searching the web (Tavily): “${args.query ?? ""}”`;
  if (name === "web_search") return `Searching the web (SearXNG): “${args.query ?? ""}”`;
  if (name === "web_extract") return `Reading ${hostOf(args.url)}`;
  return `Running ${name}`;
}

function ToolChip({
  name,
  args,
  summary,
  done,
}: {
  name: string;
  args: Record<string, unknown>;
  summary: string | null;
  done: boolean;
}) {
  const Icon = name === "web_extract" ? ExternalIcon : SearchIcon;
  return (
    <div
      className="flex items-center gap-2 text-body-sm"
      style={{ color: "var(--ink-faint)" }}
    >
      {done ? (
        <CheckIcon size={12} />
      ) : (
        <RefreshIcon size={12} className="spinning" />
      )}
      <Icon size={12} />
      <span>{toolLabel(name, args)}</span>
      {done && summary && <span style={{ opacity: 0.65 }}>· {summary}</span>}
    </div>
  );
}

// GFM parsing is the expensive part of rendering an answer — memoized so a
// streamed answer only re-parses when the deferred text actually advances,
// and finished messages never re-parse at all.
const MarkdownAnswer = memo(function MarkdownAnswer({ text }: { text: string }) {
  return (
    <div className="reader" style={{ fontSize: 15.5 }}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
});

function ToolTrace({ calls }: { calls: (ToolEvent & { done: boolean })[] }) {
  if (calls.length === 0) return null;
  return (
    <div
      className="mb-3 flex flex-col gap-1.5 border-b pb-3"
      style={{ borderColor: "var(--line-soft)" }}
    >
      {calls.map((c, i) => (
        <ToolChip key={i} name={c.name} args={c.args} summary={c.summary} done={c.done} />
      ))}
    </div>
  );
}

/** Generic streaming chat over a persisted conversation. The article page
 * and the project page differ only in endpoint, copy, and suggestions. */
export default function QAPanel({
  qaKey,
  stream,
  heading,
  placeholder,
  suggestions,
  initialInput = "",
  variant = "section",
}: {
  qaKey: string;
  stream: (content: string, onEvent: (event: QAStreamEvent) => void) => Promise<void>;
  heading: string;
  placeholder: string;
  suggestions: string[];
  initialInput?: string;
  variant?: "section" | "embedded";
}) {
  const { data: status } = useAiStatus();
  const key = qaKey;
  const { data: messages } = useSWR<ChatMessage[]>(
    status?.configured ? key : null,
    fetcher,
  );
  const [input, setInput] = useState(initialInput);
  const [pending, setPending] = useState<string | null>(null);
  const [toolCalls, setToolCalls] = useState<LiveToolCall[]>([]);
  const [liveText, setLiveText] = useState("");
  // Deltas arrive faster than GFM can re-parse the whole accumulated answer;
  // rendering the deferred value keeps input and scrolling responsive.
  const deferredLiveText = useDeferredValue(liveText);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if ((messages && messages.length > 0) || pending) {
      bottomRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [messages, pending, toolCalls.length, deferredLiveText]);

  if (!status?.configured) return null;

  async function send(question: string) {
    const q = question.trim();
    if (!q || pending) return;
    setPending(q);
    setToolCalls([]);
    setLiveText("");
    setInput("");
    setError(null);
    try {
      let finished = false;
      await stream(q, (event) => {
        if (event.type === "tool_call") {
          // Any text so far was pre-tool-call preamble, not the answer.
          setLiveText("");
          setToolCalls((calls) => [
            ...calls,
            { id: event.id, name: event.name, args: event.args, summary: null, done: false },
          ]);
        } else if (event.type === "tool_result") {
          setToolCalls((calls) =>
            calls.map((c) =>
              c.id === event.id ? { ...c, summary: event.summary, done: true } : c,
            ),
          );
        } else if (event.type === "delta") {
          setLiveText((text) => text + event.text);
        } else if (event.type === "done") {
          finished = true;
        }
      });
      if (!finished) throw new Error("The assistant's reply was interrupted");
      await mutate(key);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The assistant could not answer");
      setInput(q);
    } finally {
      setPending(null);
      setToolCalls([]);
      setLiveText("");
    }
  }

  return (
    <section
      className={variant === "section" ? "mt-10 border-t pt-7" : "min-h-0"}
      style={{ borderColor: "var(--line-soft)" }}
    >
      <div className="flex items-center gap-2">
        <CommentIcon size={13} />
        <span className="mono-label">{heading}</span>
        {status.search && (
          <span className="mono-label" style={{ opacity: 0.55 }}>
            · web-aware
          </span>
        )}
      </div>

      {(!messages || messages.length === 0) && !pending && (
        <div className="mt-4 flex flex-wrap gap-2">
          {suggestions.map((s) => (
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
                className="max-w-[85%] rounded-lg rounded-br-sm px-4 py-2.5 text-body"
                style={{ background: "var(--accent-soft)", color: "var(--ink)" }}
              >
                {m.content}
              </p>
            </div>
          ) : (
            <div
              key={m.id}
              className="max-w-[95%] border-l pl-4"
              style={{ borderColor: "var(--line)" }}
            >
              <ToolTrace
                calls={(m.tool_events ?? []).map((t) => ({ ...t, done: true }))}
              />
              <MarkdownAnswer text={m.content} />
            </div>
          ),
        )}
        {pending && (
          <>
            <div className="flex justify-end">
              <p
                className="max-w-[85%] rounded-lg rounded-br-sm px-4 py-2.5 text-body"
                style={{ background: "var(--accent-soft)", color: "var(--ink)" }}
              >
                {pending}
              </p>
            </div>
            <div className="max-w-[95%] border-l pl-4" style={{ borderColor: "var(--line)" }}>
              <ToolTrace calls={toolCalls} />
              {deferredLiveText ? (
                <MarkdownAnswer text={deferredLiveText} />
              ) : (
                <TypingDots />
              )}
            </div>
          </>
        )}
        <div ref={bottomRef} />
      </div>

      {error && (
        <ErrorText className="mt-3">
          {error}
        </ErrorText>
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
          placeholder={placeholder}
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
