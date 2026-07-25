"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import HistoryDocumentSummary from "@/components/HistoryDocumentSummary";
import PrivateHistoryImage from "@/components/PrivateHistoryImage";
import { CommentIcon, ExternalIcon } from "@/components/icons";
import ErrorText from "@/components/ui/ErrorText";
import Skeleton from "@/components/ui/Skeleton";
import { streamHistoryQA } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import {
  useHistoryDocument,
  useHistoryDocumentContent,
} from "@/lib/queries";
import { keys } from "@/lib/keys";

const QAPanel = dynamic(() => import("@/components/QAPanel"));

type ContentBlock = NonNullable<
  ReturnType<typeof useHistoryDocumentContent>["data"]
>["blocks"][number];

/** Consecutive captured list items belong to one list — rendering each in its
 * own <ul> spaced them apart like unrelated paragraphs. */
function groupBlocks(
  blocks: ContentBlock[],
): { kind: ContentBlock["kind"]; blocks: ContentBlock[] }[] {
  const groups: { kind: ContentBlock["kind"]; blocks: ContentBlock[] }[] = [];
  for (const block of blocks) {
    const previous = groups[groups.length - 1];
    if (block.kind === "list_item" && previous?.kind === "list_item") {
      previous.blocks.push(block);
      continue;
    }
    groups.push({ kind: block.kind, blocks: [block] });
  }
  return groups;
}

function safeUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

export default function HistoryDocumentPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const documentId = Number(id);
  const validId = Number.isSafeInteger(documentId) && documentId > 0;
  const { data: document, error } = useHistoryDocument(
    validId ? documentId : null,
  );
  const { data: content, error: contentError } = useHistoryDocumentContent(
    validId ? documentId : null,
  );
  const [qaEnabled, setQaEnabled] = useState(false);

  if (!validId || error) {
    return (
      <div className="mx-auto max-w-[700px] px-6 py-24 text-center">
        <ErrorText>This saved page is no longer available.</ErrorText>
        <Link href="/history" className="btn mt-5">
          Back to history
        </Link>
      </div>
    );
  }
  if (!document || (!content && !contentError)) {
    return (
      <div className="mx-auto max-w-[700px] px-6 py-12">
        <Skeleton className="h-5 w-24" />
        <Skeleton className="mt-8 h-10 w-4/5" />
        <Skeleton className="mt-5 h-56" />
      </div>
    );
  }

  const primary = document.locations[0];
  const title = primary?.title?.trim() || primary?.url || "Saved page";
  const originalUrl = primary ? safeUrl(primary.url) : null;

  return (
    <article className="fade-up mx-auto max-w-[700px] px-5 pb-24 pt-6 sm:px-8 sm:pt-10">
      <button
        className="font-mono-nr text-label"
        style={{ color: "var(--ink-faint)" }}
        onClick={() => router.back()}
      >
        ← back
      </button>

      <div className="mt-7 flex items-start gap-3">
        {primary?.favicon_image_id && (
          <span
            className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-md border"
            style={{ borderColor: "var(--line-soft)" }}
          >
            <PrivateHistoryImage
              imageId={primary.favicon_image_id}
              alt=""
              className="h-6 w-6 object-contain"
            />
          </span>
        )}
        <div className="min-w-0">
          <p className="mono-label">{primary?.hostname ?? "Browser history"}</p>
          <h1 className="font-serif-nr mt-2 text-[29px] font-medium leading-[1.18] sm:text-[36px]">
            {title}
          </h1>
          {primary && (
            <p
              className="font-mono-nr mt-3 text-body-sm"
              style={{ color: "var(--ink-faint)" }}
            >
              Saved {timeAgo(primary.last_seen_at)} · {primary.visit_count} URL{" "}
              {primary.visit_count === 1 ? "visit" : "visits"}
            </p>
          )}
        </div>
      </div>

      {document.lead_image_id && (
        <div
          className="mt-6 aspect-[2/1] overflow-hidden rounded-lg border"
          style={{ borderColor: "var(--line-soft)", background: "var(--bg-hover)" }}
        >
          <PrivateHistoryImage
            imageId={document.lead_image_id}
            alt=""
            className="h-full w-full object-cover"
          />
        </div>
      )}

      {originalUrl && (
        <a
          className="btn mt-6"
          href={originalUrl}
          target="_blank"
          rel="noreferrer"
        >
          <ExternalIcon size={13} />
          Open current page
        </a>
      )}

      <HistoryDocumentSummary documentId={documentId} />

      {contentError ? (
        <div className="mt-8">
          <ErrorText>Could not decrypt the saved page text.</ErrorText>
        </div>
      ) : (
        <section className="reader mt-9" aria-label="Saved page content">
          {groupBlocks(content?.blocks ?? []).map((group) => {
            if (group.kind === "list_item") {
              return (
                <ul key={group.blocks[0]!.id}>
                  {group.blocks.map((block) => (
                    <li key={block.id}>{block.text}</li>
                  ))}
                </ul>
              );
            }
            const block = group.blocks[0]!;
            if (block.kind === "heading") {
              return <h2 key={block.id}>{block.text}</h2>;
            }
            if (block.kind === "quote") {
              return <blockquote key={block.id}>{block.text}</blockquote>;
            }
            if (block.kind === "code") {
              return (
                <pre key={block.id}>
                  <code>{block.text}</code>
                </pre>
              );
            }
            return <p key={block.id}>{block.text}</p>;
          })}
        </section>
      )}

      {document.other_versions.length > 0 && (
        <section
          className="mt-10 border-t pt-7"
          style={{ borderColor: "var(--line-soft)" }}
        >
          <p className="mono-label">Other saved versions</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {document.other_versions.map((version) => (
              <Link
                key={version.document_id}
                href={`/history/documents/${version.document_id}`}
                className="btn"
              >
                {timeAgo(version.last_seen_at)}
                {version.is_current ? " · current" : ""}
              </Link>
            ))}
          </div>
        </section>
      )}

      {!qaEnabled ? (
        <section
          className="mt-10 border-t pt-7"
          style={{ borderColor: "var(--line-soft)" }}
        >
          <button className="btn" onClick={() => setQaEnabled(true)}>
            <CommentIcon size={13} />
            Ask about this saved page
          </button>
          <p
            className="mt-2 text-caption"
            style={{ color: "var(--ink-faint)" }}
          >
            Q&amp;A stays off until you enable it.
          </p>
        </section>
      ) : (
        <QAPanel
          qaKey={keys.historyDocumentQa(documentId)}
          stream={(question, onEvent) =>
            streamHistoryQA(documentId, question, onEvent)
          }
          heading="Saved page Q&A"
          placeholder="Ask about this saved version…"
          suggestions={[
            "What are the main claims?",
            "What evidence supports the conclusion?",
            "What should I remember from this?",
          ]}
        />
      )}
    </article>
  );
}
