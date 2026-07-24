"use client";

import Link from "next/link";
import { useState, useSyncExternalStore } from "react";
import { toast } from "sonner";
import {
  api,
  apiDownload,
  type BrowserConnection,
  type BrowserConnectionCreated,
  type BrowserHistoryDeletion,
  type BrowserHistorySettings,
} from "@/lib/api";
import { timeAgo } from "@/lib/format";
import {
  mutateBrowserHistory,
  mutateBrowserHistorySettings,
  useHistoryConnections,
  useHistoryExtension,
  useHistoryRules,
  useHistorySettings,
  useHistorySummary,
} from "@/lib/queries";
import {
  CheckIcon,
  CopyIcon,
  DownloadIcon,
  ListIcon,
  PlusIcon,
  TrashIcon,
  XIcon,
} from "./icons";
import ConfirmButton from "./ui/ConfirmButton";
import ErrorText from "./ui/ErrorText";
import Skeleton from "./ui/Skeleton";

function browserIsChromium() {
  if (typeof navigator === "undefined") return true;
  const ua = navigator.userAgent;
  return /(Chrome|Chromium|CriOS|Edg)\//.test(ua) && !/Firefox\//.test(ua);
}

function useIsChromium() {
  return useSyncExternalStore(
    () => () => undefined,
    browserIsChromium,
    () => true,
  );
}

export default function BrowserHistorySettingsSection() {
  const { data: connections, isLoading: loadingConnections } =
    useHistoryConnections();
  const { data: settings } = useHistorySettings();
  const { data: summary } = useHistorySummary();
  const { data: rules } = useHistoryRules();
  const { data: extension } = useHistoryExtension();
  const [name, setName] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [created, setCreated] = useState<BrowserConnectionCreated | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isChromium = useIsChromium();

  async function createConnection(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api<BrowserConnectionCreated>("/history/connections", {
        method: "POST",
        body: { name: trimmed },
      });
      setCreated(result);
      setName("");
      mutateBrowserHistorySettings();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the connection");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(connection: BrowserConnection) {
    try {
      await api(`/history/connections/${connection.id}`, { method: "DELETE" });
      mutateBrowserHistorySettings();
      toast.success(`${connection.name} revoked`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not revoke the connection");
    }
  }

  async function updateRetention(value: string) {
    const retentionDays = value === "forever" ? null : Number(value);
    try {
      await api<BrowserHistorySettings>("/history/settings", {
        method: "PATCH",
        body: { retention_days: retentionDays },
      });
      mutateBrowserHistorySettings();
      toast.success("Retention updated");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not update retention");
    }
  }

  async function clearHistory() {
    try {
      const result = await api<BrowserHistoryDeletion>("/history", {
        method: "DELETE",
        body: { confirm: "DELETE" },
      });
      mutateBrowserHistory();
      mutateBrowserHistorySettings();
      toast.success(
        result.deleted_count === 1
          ? "Deleted 1 history item"
          : `Deleted ${result.deleted_count} history items`,
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not clear history");
    }
  }

  async function removeRule(id: number) {
    try {
      await api(`/history/domain-rules/${id}`, { method: "DELETE" });
      mutateBrowserHistorySettings();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not remove the rule");
    }
  }

  async function downloadExtension() {
    setDownloading(true);
    try {
      // Content-Disposition isn't CORS-exposed in split-origin dev, so build
      // the versioned fallback from metadata we already have.
      await apiDownload(
        "/history/extension/download",
        `newsread-history-extension${extension?.version ? `-${extension.version}` : ""}.zip`,
      );
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Could not download the extension",
      );
    } finally {
      setDownloading(false);
    }
  }

  async function copyToken() {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.token);
      toast.success("Pairing token copied");
    } catch {
      toast.error("Could not copy. Select the token and copy it manually.");
    }
  }

  return (
    <section id="browser-history" className="mt-9 scroll-mt-24">
      <div className="flex items-start gap-3">
        <span
          className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-md"
          style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
        >
          <ListIcon size={17} />
        </span>
        <div>
          <p className="mono-label">Browser history</p>
          <p className="mt-1.5 text-body" style={{ color: "var(--ink-faint)" }}>
            Pair the NewsRead Chrome extension to make your browsing history
            searchable here. Captured pages stay on this NewsRead server.
          </p>
        </div>
      </div>

      {!isChromium && (
        <div
          className="mt-3.5 rounded-md border px-3.5 py-2.5 text-body-sm"
          style={{ borderColor: "var(--line)", color: "var(--ink-dim)" }}
        >
          Pairing currently requires a Chromium browser such as Chrome, Edge, or
          Brave. You can still manage existing history from this browser.
        </div>
      )}

      <div
        className="mt-4 rounded-lg border p-4"
        style={{ background: "var(--bg-raised)", borderColor: "var(--line)" }}
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-body-lg font-medium">Install the extension</p>
            <p className="mt-0.5 text-body-sm" style={{ color: "var(--ink-faint)" }}>
              {extension?.available
                ? `Download the packaged extension${
                    extension.version ? ` (v${extension.version})` : ""
                  } and load it into Chrome once.`
                : "The packaged extension is not available on this server; build it from the repository's extension/ directory (npm install && npm run build)."}
            </p>
          </div>
          {extension?.available && (
            <button
              className="btn btn-accent shrink-0"
              onClick={downloadExtension}
              disabled={downloading}
            >
              <DownloadIcon size={13} />
              {downloading ? "Downloading…" : "Download extension"}
            </button>
          )}
        </div>
        <ol
          className="mt-3 list-decimal space-y-1 pl-5 text-body-sm"
          style={{ color: "var(--ink-dim)" }}
        >
          <li>
            {extension?.available
              ? "Download and unzip the extension."
              : "Build the extension, or unzip a copy you already have."}
          </li>
          <li>
            Open <code>chrome://extensions</code> and turn on Developer mode.
          </li>
          <li>Choose “Load unpacked” and select the unzipped folder.</li>
          <li>Create a pairing token below and paste it into the extension.</li>
        </ol>
      </div>

      <div
        className="mt-3 rounded-lg border p-4"
        style={{ background: "var(--bg-raised)", borderColor: "var(--line)" }}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-body-lg font-medium">Paired browsers</p>
            <p className="mt-0.5 text-body-sm" style={{ color: "var(--ink-faint)" }}>
              {summary
                ? `${summary.active_connection_count} active · ${summary.history_count} saved pages`
                : "Create a one-time token for each browser."}
            </p>
          </div>
          {summary?.has_history && (
            <Link href="/history" className="btn">
              Open history
            </Link>
          )}
        </div>

        {created && (
          <div
            className="mt-4 rounded-md border p-3.5"
            style={{
              background: "var(--accent-soft)",
              borderColor: "var(--accent-border)",
            }}
          >
            <div className="flex items-center justify-between gap-2">
              <p className="text-body font-medium">Copy this token now</p>
              <button
                className="icon-btn"
                title="Dismiss pairing token"
                aria-label="Dismiss pairing token"
                onClick={() => setCreated(null)}
              >
                <XIcon size={13} />
              </button>
            </div>
            <p className="mt-1 text-body-sm" style={{ color: "var(--ink-dim)" }}>
              It is shown once and cannot be recovered later.
            </p>
            <div className="mt-3 flex gap-2">
              <code
                className="min-w-0 flex-1 select-all overflow-x-auto rounded border px-3 py-2 text-body-sm"
                style={{ background: "var(--bg)", borderColor: "var(--line)" }}
              >
                {created.token}
              </code>
              <button className="btn btn-accent shrink-0" onClick={copyToken}>
                <CopyIcon size={13} />
                Copy
              </button>
            </div>
          </div>
        )}

        <form className="mt-4 flex gap-2" onSubmit={createConnection}>
          <label className="sr-only" htmlFor="history-browser-name">
            Browser name
          </label>
          <input
            id="history-browser-name"
            className="input min-w-0 flex-1"
            placeholder="e.g. Sharon’s MacBook"
            value={name}
            maxLength={100}
            onChange={(event) => setName(event.target.value)}
          />
          <button
            type="submit"
            className="btn btn-accent shrink-0"
            disabled={busy || !name.trim()}
          >
            <PlusIcon size={13} />
            {busy ? "Creating…" : "Create token"}
          </button>
        </form>
        <ErrorText className="mt-2">{error}</ErrorText>

        {loadingConnections && !connections && (
          <div className="mt-4 space-y-2">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        )}
        {connections && connections.length > 0 && (
          <div className="mt-4 divide-y" style={{ borderColor: "var(--line-soft)" }}>
            {connections.map((connection) => {
              const active = connection.revoked_at === null;
              return (
                <div
                  key={connection.id}
                  className="flex items-center gap-3 py-3 first:pt-0 last:pb-0"
                >
                  <span
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full"
                    style={
                      active
                        ? { background: "var(--accent-soft)", color: "var(--accent)" }
                        : { background: "var(--bg-hover)", color: "var(--ink-faint)" }
                    }
                  >
                    {active ? <CheckIcon size={13} /> : <XIcon size={12} />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-body font-medium">{connection.name}</p>
                    <p className="mt-0.5 text-caption" style={{ color: "var(--ink-faint)" }}>
                      {active
                        ? connection.last_seen_at
                          ? `Last synced ${timeAgo(connection.last_seen_at)}`
                          : "Waiting for first sync"
                        : `Revoked ${timeAgo(connection.revoked_at)}`}
                      {" · "}
                      {connection.token_prefix}…
                    </p>
                  </div>
                  {active && (
                    <ConfirmButton
                      className="shrink-0"
                      onConfirm={() => revoke(connection)}
                      confirmLabel="Really revoke?"
                    >
                      Revoke
                    </ConfirmButton>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div
        className="mt-3 rounded-lg border p-4"
        style={{ background: "var(--bg-raised)", borderColor: "var(--line)" }}
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-body font-medium">Retention</p>
            <p className="mt-0.5 text-body-sm" style={{ color: "var(--ink-faint)" }}>
              Older captured pages are removed automatically.
            </p>
          </div>
          <select
            className="input w-auto"
            aria-label="Browser history retention"
            value={settings?.retention_days ?? "forever"}
            disabled={!settings}
            onChange={(event) => updateRetention(event.target.value)}
          >
            <option value="30">30 days</option>
            <option value="90">90 days</option>
            <option value="365">1 year</option>
            <option value="forever">Keep forever</option>
          </select>
        </div>

        {rules && rules.length > 0 && (
          <div className="mt-4 border-t pt-4" style={{ borderColor: "var(--line-soft)" }}>
            <p className="text-body font-medium">Excluded domains</p>
            <div className="mt-2 flex flex-col gap-1">
              {rules.map((rule) => (
                <div key={rule.id} className="flex items-center gap-2 py-1 text-body-sm">
                  <span className="min-w-0 flex-1 truncate">
                    {rule.hostname}
                    {rule.match_subdomains ? " and subdomains" : ""}
                    {rule.mode === "metadata_only" ? " · metadata only" : ""}
                  </span>
                  <button
                    className="icon-btn shrink-0"
                    title={`Remove rule for ${rule.hostname}`}
                    aria-label={`Remove rule for ${rule.hostname}`}
                    onClick={() => removeRule(rule.id)}
                  >
                    <TrashIcon size={12} />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        <div
          className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t pt-4"
          style={{ borderColor: "var(--line-soft)" }}
        >
          <div>
            <p className="text-body font-medium">Clear browser history</p>
            <p className="mt-0.5 text-body-sm" style={{ color: "var(--ink-faint)" }}>
              Deletes captured pages and tells paired browsers not to restore them.
            </p>
          </div>
          <ConfirmButton onConfirm={clearHistory} confirmLabel="Really clear all?">
            Clear all
          </ConfirmButton>
        </div>
      </div>
    </section>
  );
}
