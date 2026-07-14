"use client";

import * as Tabs from "@radix-ui/react-tabs";

import type { DiscussionDraft, HackerNewsDiscussionState } from "./HackerNewsDiscussion";
import { XIcon } from "./icons";
import Modal, { ModalClose, ModalTitle } from "./Modal";
import QAPanel from "./QAPanel";
import { streamDiscussionQA, streamQA, type ArticleDetail } from "@/lib/api";

export type AssistantScope = "article" | "discussion";

export default function ArticleAssistantDrawer({
  article,
  discussion,
  scope,
  onScopeChange,
  draft,
  onClose,
}: {
  article: ArticleDetail;
  discussion: HackerNewsDiscussionState;
  scope: AssistantScope;
  onScopeChange: (scope: AssistantScope) => void;
  draft: DiscussionDraft | null;
  onClose: () => void;
}) {
  const canAskDiscussion = Boolean(discussion.ref);
  const activeScope = scope === "discussion" && canAskDiscussion ? "discussion" : "article";

  return (
    <Modal
      onClose={onClose}
      placement="drawer"
      contentClassName="flex flex-col overflow-hidden"
    >
      <div className="flex items-start justify-between gap-4 border-b px-5 py-4" style={{ borderColor: "var(--line-soft)" }}>
        <div>
          <p className="mono-label">Reading assistant</p>
          <ModalTitle asChild>
            <h2 className="font-serif-nr mt-1 text-[20px] font-medium">Ask this story</h2>
          </ModalTitle>
        </div>
        <ModalClose asChild>
          <button className="icon-btn min-h-11 min-w-11" aria-label="Close assistant">
            <XIcon size={16} />
          </button>
        </ModalClose>
      </div>

      <Tabs.Root
        value={activeScope}
        onValueChange={(value) => onScopeChange(value as AssistantScope)}
        className="flex min-h-0 flex-1 flex-col"
      >
        <Tabs.List
          className="mx-5 mt-4 grid grid-cols-2 rounded-md border p-1"
          style={{ borderColor: "var(--line-soft)", background: "var(--bg-inset)" }}
          aria-label="Assistant source"
        >
          <Tabs.Trigger
            value="article"
            className="rounded px-3 py-2 text-[12.5px] font-medium text-[var(--ink-dim)] data-[state=active]:bg-[var(--bg-raised)] data-[state=active]:text-[var(--ink)]"
          >
            Article
          </Tabs.Trigger>
          <Tabs.Trigger
            value="discussion"
            disabled={!canAskDiscussion}
            className="rounded px-3 py-2 text-[12.5px] font-medium text-[var(--ink-dim)] data-[state=active]:bg-[var(--bg-raised)] data-[state=active]:text-[var(--ink)] disabled:opacity-40"
          >
            HN discussion
          </Tabs.Trigger>
        </Tabs.List>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-6 pt-5">
          {activeScope === "article" ? (
            <QAPanel
              key={`article-${article.id}`}
              qaKey={`/articles/${article.id}/qa`}
              stream={(question, onEvent) => streamQA(article.id, question, onEvent)}
              heading="Ask the article"
              placeholder="Ask anything about this article"
              suggestions={[
                "What are the key points?",
                "Why does this matter?",
                "What is the counterargument?",
              ]}
              variant="embedded"
            />
          ) : (
            <QAPanel
              key={draft?.key ?? `discussion-${article.id}`}
              qaKey={`/articles/${article.id}/discussion/qa`}
              stream={async (question, onEvent) => {
                const snapshot = await discussion.loadThread(300);
                return streamDiscussionQA(article.id, question, snapshot, onEvent);
              }}
              heading="Ask the discussion"
              placeholder="Ask what commenters think, or draft a reply"
              suggestions={[
                "Summarize the discussion",
                "Where do commenters disagree?",
                "What did commenters add?",
                "Find corrections and open questions",
              ]}
              initialInput={draft?.text ?? ""}
              variant="embedded"
            />
          )}
        </div>
      </Tabs.Root>
    </Modal>
  );
}
