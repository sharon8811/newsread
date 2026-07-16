"use client";

import { useEffect, useId, useRef, useState } from "react";
import useSWR from "swr";
import {
  fetcher,
  type MessagingPlatform,
  type ShareTarget,
  type TargetOption,
  type UserPublic,
} from "@/lib/api";
import { useIntegrations, useShareTargets, useUserSearch } from "@/lib/queries";
import { useDebouncedValue } from "@/lib/useDebouncedValue";
import { SlackIcon, TeamsIcon, XIcon } from "./icons";
import Avatar from "./ui/Avatar";

export type ExternalShareDestination = {
  key: string;
  platform: MessagingPlatform;
  externalId: string;
  displayName: string;
  targetType: TargetOption["target_type"];
  meta: TargetOption["meta"];
  savedId: number | null;
};

type DestinationRow =
  | { key: string; kind: "user"; user: UserPublic }
  | { key: string; kind: "external"; destination: ExternalShareDestination };

type DestinationSection = {
  key: string;
  label: string;
  platform?: MessagingPlatform;
  rows: DestinationRow[];
  loading?: boolean;
  error?: string | null;
};

function externalKey(platform: MessagingPlatform, externalId: string) {
  return `${platform}:${externalId}`;
}

function displayTargetName(platform: MessagingPlatform, name: string) {
  return platform === "slack" ? name.replace(/^#\s*/, "") : name;
}

function fromSaved(target: ShareTarget): ExternalShareDestination {
  return {
    key: externalKey(target.platform, target.external_id),
    platform: target.platform,
    externalId: target.external_id,
    displayName: target.display_name,
    targetType: target.target_type,
    meta: target.meta,
    savedId: target.id,
  };
}

function fromOption(
  platform: MessagingPlatform,
  option: TargetOption,
  savedId: number | null,
): ExternalShareDestination {
  return {
    key: externalKey(platform, option.external_id),
    platform,
    externalId: option.external_id,
    displayName: option.display_name,
    targetType: option.target_type,
    meta: option.meta,
    savedId: option.saved_id ?? savedId,
  };
}

function PlatformIcon({ platform, size = 14 }: { platform: MessagingPlatform; size?: number }) {
  return platform === "slack" ? <SlackIcon size={size} /> : <TeamsIcon size={size} />;
}

function loadError(error: unknown, platform: MessagingPlatform) {
  if (!error) return null;
  const label = platform === "slack" ? "Slack" : "Teams";
  return error instanceof Error ? error.message : `Could not load ${label} destinations`;
}

export default function ShareDestinationPicker({
  recipients,
  externalDestinations,
  onAddRecipient,
  onRemoveRecipient,
  onAddExternal,
  onRemoveExternal,
}: {
  recipients: UserPublic[];
  externalDestinations: ExternalShareDestination[];
  onAddRecipient: (user: UserPublic) => void;
  onRemoveRecipient: (userId: number) => void;
  onAddExternal: (destination: ExternalShareDestination) => void;
  onRemoveExternal: (key: string) => void;
}) {
  const id = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(-1);
  const liveQuery = query.trim().replace(/^@/, "");
  const searchQuery = useDebouncedValue(liveQuery, 200);

  const { data: integrations } = useIntegrations();
  const { data: savedTargets } = useShareTargets();
  const { data: userMatches } = useUserSearch(liveQuery ? searchQuery : "");
  const connected = new Set(
    integrations
      ?.filter((integration) => integration.connected && integration.status === "active")
      .map((integration) => integration.platform) ?? [],
  );

  const targetQuery = encodeURIComponent(searchQuery);
  const slack = useSWR<TargetOption[]>(
    open && connected.has("slack") ? `/integrations/slack/targets?q=${targetQuery}` : null,
    fetcher,
  );
  const teams = useSWR<TargetOption[]>(
    open && connected.has("teams") ? `/integrations/teams/targets?q=${targetQuery}` : null,
    fetcher,
  );

  const selectedUsers = new Set(recipients.map((recipient) => recipient.id));
  const selectedExternal = new Set(externalDestinations.map((destination) => destination.key));

  function platformRows(
    platform: MessagingPlatform,
    options: TargetOption[] | undefined,
  ): DestinationRow[] {
    const saved = new Map(
      (savedTargets ?? [])
        .filter((target) => target.platform === platform)
        .map((target) => [target.external_id, fromSaved(target)]),
    );
    const merged = new Map(saved);
    for (const option of options ?? []) {
      merged.set(
        option.external_id,
        fromOption(platform, option, saved.get(option.external_id)?.savedId ?? null),
      );
    }
    const normalized = liveQuery.toLocaleLowerCase();
    return Array.from(merged.values())
      .filter(
        (destination) =>
          !selectedExternal.has(destination.key) &&
          (!normalized || destination.displayName.toLocaleLowerCase().includes(normalized)),
      )
      .map((destination) => ({
        key: destination.key,
        kind: "external" as const,
        destination,
      }));
  }

  const sections: DestinationSection[] = [];
  if (liveQuery) {
    sections.push({
      key: "newsread",
      label: "NewsRead people",
      rows: (userMatches ?? [])
        .filter((user) => !selectedUsers.has(user.id))
        .map((user) => ({ key: `user:${user.id}`, kind: "user" as const, user })),
      loading: searchQuery !== liveQuery || userMatches === undefined,
    });
  }
  if (connected.has("slack")) {
    sections.push({
      key: "slack",
      label: "Slack",
      platform: "slack",
      rows: platformRows("slack", slack.data),
      loading: slack.isLoading && slack.data === undefined,
      error: loadError(slack.error, "slack"),
    });
  }
  if (connected.has("teams")) {
    sections.push({
      key: "teams",
      label: "Microsoft Teams",
      platform: "teams",
      rows: platformRows("teams", teams.data),
      loading: teams.isLoading && teams.data === undefined,
      error: loadError(teams.error, "teams"),
    });
  }

  const rows = sections.flatMap((section) => section.rows);
  const resolvedActiveIndex = rows.length
    ? Math.min(Math.max(activeIndex, 0), rows.length - 1)
    : -1;
  const activeRow = resolvedActiveIndex >= 0 ? rows[resolvedActiveIndex] : undefined;
  const activeRowKey = activeRow?.key;

  useEffect(() => {
    if (!activeRowKey) return;
    const element = document.getElementById(`${id}-${activeRowKey}`);
    element?.scrollIntoView?.({ block: "nearest" });
  }, [activeRowKey, id]);

  function select(row: DestinationRow) {
    if (row.kind === "user") onAddRecipient(row.user);
    else onAddExternal(row.destination);
    setQuery("");
    inputRef.current?.focus();
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex(
        rows.length ? Math.min(resolvedActiveIndex + 1, rows.length - 1) : -1,
      );
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex(rows.length ? Math.max(resolvedActiveIndex - 1, 0) : -1);
    } else if (event.key === "Enter" && open && activeRow) {
      event.preventDefault();
      select(activeRow);
    } else if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
    } else if (event.key === "Backspace" && !query) {
      const lastExternal = externalDestinations.at(-1);
      const lastRecipient = recipients.at(-1);
      if (lastExternal) onRemoveExternal(lastExternal.key);
      else if (lastRecipient) onRemoveRecipient(lastRecipient.id);
    }
  }

  const hasFeedback = sections.some(
    (section) => section.loading || section.error || section.rows.length > 0,
  );

  return (
    <div
      className="mt-4"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      <label htmlFor={`${id}-input`} className="text-body-sm font-medium">
        Share to
        <span className="ml-1 font-normal" style={{ color: "var(--ink-faint)" }}>
          optional
        </span>
      </label>
      <div
        className="mt-1.5 flex min-h-11 w-full flex-wrap items-center gap-1.5 rounded-md border px-2.5 py-1.5 transition-[border-color,box-shadow] focus-within:border-[var(--accent-border)] focus-within:shadow-[0_0_0_3px_var(--accent-soft)]"
        style={{ background: "var(--bg-raised)", borderColor: "var(--line)" }}
        onClick={() => inputRef.current?.focus()}
      >
        {recipients.map((recipient) => (
          <span
            key={recipient.id}
            className="inline-flex max-w-full items-center gap-1 rounded-full px-2 py-1 text-body-sm"
            style={{ background: "var(--accent-soft)", color: "var(--accent-bright)" }}
          >
            <span className="truncate">@{recipient.username}</span>
            <button
              type="button"
              className="shrink-0 opacity-70 hover:opacity-100"
              aria-label={`Remove @${recipient.username}`}
              onClick={(event) => {
                event.stopPropagation();
                onRemoveRecipient(recipient.id);
              }}
            >
              <XIcon size={11} />
            </button>
          </span>
        ))}
        {externalDestinations.map((destination) => (
          <span
            key={destination.key}
            className="inline-flex max-w-full items-center gap-1.5 rounded-full px-2 py-1 text-body-sm"
            style={{ background: "var(--accent-soft)", color: "var(--accent-bright)" }}
          >
            <PlatformIcon platform={destination.platform} size={11} />
            <span className="truncate">
              {displayTargetName(destination.platform, destination.displayName)}
            </span>
            <button
              type="button"
              className="shrink-0 opacity-70 hover:opacity-100"
              aria-label={`Remove ${destination.displayName}`}
              onClick={(event) => {
                event.stopPropagation();
                onRemoveExternal(destination.key);
              }}
            >
              <XIcon size={11} />
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          id={`${id}-input`}
          className="min-w-32 flex-1 bg-transparent px-1 py-1.5 text-[16px] text-ink outline-none sm:text-body-lg"
          placeholder={recipients.length || externalDestinations.length ? "Add more" : "People, Slack, or Teams"}
          value={query}
          role="combobox"
          aria-autocomplete="list"
          aria-controls={`${id}-listbox`}
          aria-expanded={open}
          aria-activedescendant={open && activeRow ? `${id}-${activeRow.key}` : undefined}
          onFocus={() => setOpen(true)}
          onChange={(event) => {
            setQuery(event.target.value);
            setActiveIndex(0);
            setOpen(true);
          }}
          onKeyDown={handleKeyDown}
        />
      </div>

      {open && (
        <div
          id={`${id}-listbox`}
          role="listbox"
          aria-label="Share destinations"
          className="mt-1.5 max-h-[min(42dvh,320px)] overflow-y-auto rounded-md border py-1 shadow-[var(--shadow-modal)]"
          style={{ background: "var(--bg-raised)", borderColor: "var(--line)" }}
        >
          {sections.map((section) => (
            <div key={section.key} role="group" aria-label={section.label} className="py-1">
              <div className="flex items-center gap-1.5 px-3 py-1.5 mono-label">
                {section.platform && <PlatformIcon platform={section.platform} size={12} />}
                {section.label}
              </div>
              {section.loading && (
                <p className="px-3 py-2 text-body-sm" style={{ color: "var(--ink-faint)" }}>
                  Loading…
                </p>
              )}
              {section.error && (
                <p className="px-3 py-2 text-body-sm" style={{ color: "var(--danger)" }}>
                  {section.error}
                </p>
              )}
              {!section.loading && !section.error && section.rows.length === 0 && liveQuery && (
                <p className="px-3 py-2 text-body-sm" style={{ color: "var(--ink-faint)" }}>
                  No matches
                </p>
              )}
              {section.rows.map((row) => {
                const index = rows.findIndex((candidate) => candidate.key === row.key);
                const active = index === resolvedActiveIndex;
                return (
                  <button
                    key={row.key}
                    id={`${id}-${row.key}`}
                    type="button"
                    role="option"
                    aria-selected={false}
                    tabIndex={-1}
                    className="flex min-h-11 w-full items-center gap-2.5 px-3 text-left text-body transition-colors"
                    style={{ background: active ? "var(--bg-hover)" : "transparent" }}
                    onMouseEnter={() => setActiveIndex(index)}
                    onClick={() => select(row)}
                  >
                    {row.kind === "user" ? (
                      <>
                        <Avatar name={row.user.name} />
                        <span className="min-w-0 flex-1 truncate">{row.user.name}</span>
                        <span className="truncate font-mono-nr text-label" style={{ color: "var(--ink-faint)" }}>
                          @{row.user.username}
                        </span>
                      </>
                    ) : (
                      <>
                        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
                          <PlatformIcon platform={row.destination.platform} size={13} />
                        </span>
                        <span className="min-w-0 flex-1 truncate">
                          {displayTargetName(row.destination.platform, row.destination.displayName)}
                        </span>
                        <span className="shrink-0 font-mono-nr text-caption uppercase" style={{ color: "var(--ink-faint)" }}>
                          {row.destination.targetType}
                        </span>
                      </>
                    )}
                  </button>
                );
              })}
            </div>
          ))}
          {!hasFeedback && (
            <p className="px-3 py-3 text-body-sm" style={{ color: "var(--ink-faint)" }}>
              Type a NewsRead username, or connect Slack or Teams in Settings.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
