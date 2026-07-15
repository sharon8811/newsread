"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import {
  api,
  fetcher,
  type Article,
  type DislikeOptions,
  type DislikeRuleCreate,
  type DislikeRuleCreated,
} from "@/lib/api";
import { mutateArticleLists } from "./ArticleList";
import { CheckIcon, EyeOffIcon } from "./icons";
import Modal, { ModalHeader } from "./Modal";
import Button from "./ui/Button";
import Chip from "./ui/Chip";
import ErrorText from "./ui/ErrorText";

/** Opens right after "Not interested": the article itself is hidden on mount
 * (one rule of kind 'article'), then each chip adds a broader rule — an
 * entity, an LLM-suggested topic, or a two-week mute of this story. Undo
 * deletes every rule created in this popover session. */
export default function NotInterestedModal({
  article,
  onClose,
}: {
  article: Article;
  onClose: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  // Chip key -> created rule (for the "also hid N recent" captions + Undo).
  const [applied, setApplied] = useState<Record<string, DislikeRuleCreated>>({});
  const [busyChip, setBusyChip] = useState<string | null>(null);
  const [createdIds, setCreatedIds] = useState<number[]>([]);

  const { data: options } = useSWR<DislikeOptions>(
    `/interests/dislike-options/${article.id}`,
    fetcher,
  );

  useEffect(() => {
    // Hide the article immediately — the chips are optional refinement.
    api<DislikeRuleCreated>("/interests/dislikes", {
      method: "POST",
      body: { kind: "article", article_id: article.id },
    })
      .then((created) => {
        setCreatedIds((ids) => [...ids, created.rule.id]);
        mutateArticleLists();
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Could not hide the article"),
      );
  }, [article.id]);

  async function addRule(chipKey: string, body: DislikeRuleCreate) {
    if (busyChip || applied[chipKey]) return;
    setBusyChip(chipKey);
    setError(null);
    try {
      const created = await api<DislikeRuleCreated>("/interests/dislikes", {
        method: "POST",
        body,
      });
      setCreatedIds((ids) => [...ids, created.rule.id]);
      setApplied((prev) => ({ ...prev, [chipKey]: created }));
      mutateArticleLists();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the rule");
    } finally {
      setBusyChip(null);
    }
  }

  async function undo() {
    setError(null);
    await Promise.all(
      createdIds.map((id) =>
        api(`/interests/dislikes/${id}`, { method: "DELETE" }).catch(() => {}),
      ),
    );
    setCreatedIds([]);
    mutateArticleLists();
    onClose();
  }

  function chip(chipKey: string, label: string, body: DislikeRuleCreate) {
    const created = applied[chipKey];
    // The article-rule suppression is always there; only extra hits are news.
    const alsoHid = created ? Math.max(created.rule.hidden_count - 1, 0) : 0;
    return (
      <Chip
        key={chipKey}
        active={!!created}
        disabled={busyChip === chipKey}
        onClick={() => addRule(chipKey, body)}
      >
        {label}
        {created && <CheckIcon size={11} />}
        {created && alsoHid > 0 && (
          <span className="font-mono-nr text-[10.5px]" style={{ color: "var(--ink-faint)" }}>
            +{alsoHid} recent
          </span>
        )}
      </Chip>
    );
  }

  return (
    <Modal onClose={onClose} contentClassName="p-6">
        <ModalHeader
          eyebrow={
            <>
              <EyeOffIcon size={12} />
              Hidden from your feed
            </>
          }
          title={article.title}
        />

        <p className="mt-4 text-[13.5px]" style={{ color: "var(--ink-dim)" }}>
          Hide similar articles too?
        </p>

        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {options?.story_available &&
            chip("story", "This story (2 weeks)", {
              kind: "story",
              article_id: article.id,
            })}
          {(options?.entities ?? article.entities.map((e) => ({
            entity_id: e.id,
            kind: e.kind,
            key: e.key,
            label: String(e.badge?.label ?? e.key),
          }))).map((entity) =>
            chip(`entity-${entity.entity_id}`, entity.label, {
              kind: "entity",
              entity_id: entity.entity_id,
            }),
          )}
          {options?.topics.map((topic) =>
            chip(`topic-${topic}`, topic, { kind: "topic", phrase: topic }),
          )}
          {!options && (
            <span
              className="font-mono-nr px-1 py-1 text-[11.5px]"
              style={{ color: "var(--ink-faint)" }}
            >
              Suggesting topics…
            </span>
          )}
          {options && !options.story_available && options.entities.length === 0 &&
            options.topics.length === 0 && (
              <span
                className="font-mono-nr px-1 py-1 text-[11.5px]"
                style={{ color: "var(--ink-faint)" }}
              >
                No broader suggestions for this one.
              </span>
            )}
        </div>

        {error && (
          <ErrorText className="mt-3">
            {error}
          </ErrorText>
        )}

        <div className="mt-5 flex items-center justify-between">
          <p className="font-mono-nr text-[11px]" style={{ color: "var(--ink-faint)" }}>
            Manage rules anytime in Settings
          </p>
          <div className="flex items-center gap-2">
            <Button onClick={undo}>Undo</Button>
            <Button variant="primary" onClick={onClose}>
              Done
            </Button>
          </div>
        </div>
    </Modal>
  );
}
