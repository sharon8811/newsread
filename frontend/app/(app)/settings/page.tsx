"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR, { mutate } from "swr";
import { toast } from "sonner";
import ConfirmButton from "@/components/ui/ConfirmButton";
import ErrorText from "@/components/ui/ErrorText";
import {
  api,
  fetcher,
  PLATFORM_LABELS,
  type IntegrationStatus,
  type MessagingPlatform,
  type ShareTarget,
  type TargetOption,
} from "@/lib/api";
import { keys } from "@/lib/keys";
import {
  mutateIntegrations,
  mutateShareTargets,
  useIntegrations,
  useServerConfig,
  useShareTargets,
} from "@/lib/queries";
import { useDebouncedValue } from "@/lib/useDebouncedValue";
import { useMutation } from "@/lib/useMutation";
import {
  CheckIcon,
  PlusIcon,
  SearchIcon,
  SlackIcon,
  TeamsIcon,
  TrashIcon,
  XIcon,
} from "@/components/icons";
import AISettingsSection from "@/components/AISettingsSection";
import NotInterestedSection from "@/components/NotInterestedSection";

function PlatformIcon({ platform, size }: { platform: MessagingPlatform; size?: number }) {
  return platform === "slack" ? <SlackIcon size={size} /> : <TeamsIcon size={size} />;
}

function ConnectionCard({
  integration,
  onChanged,
}: {
  integration: IntegrationStatus;
  onChanged: () => void;
}) {
  const label = PLATFORM_LABELS[integration.platform];

  const connectMutation = useMutation(
    () => api<{ url: string }>(`/integrations/${integration.platform}/authorize`),
    {
      fallbackError: "Could not start the connection",
      onSuccess({ url }) {
        window.location.href = url; // provider consent screen; comes back to /settings
      },
    },
  );
  const disconnectMutation = useMutation(
    () => api(`/integrations/${integration.platform}`, { method: "DELETE" }),
    {
      fallbackError: "Could not disconnect",
      onSuccess() {
        onChanged();
        mutate(keys.shareTargets);
      },
    },
  );
  const connect = connectMutation.run;
  const disconnect = disconnectMutation.run;
  const busy = connectMutation.busy || disconnectMutation.busy;
  const error = connectMutation.error ?? disconnectMutation.error;

  return (
    <div
      className="flex items-center gap-3.5 rounded-lg border p-4"
      style={{ background: "var(--bg-raised)", borderColor: "var(--line)" }}
    >
      <span
        className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md"
        style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
      >
        <PlatformIcon platform={integration.platform} size={20} />
      </span>
      <div className="min-w-0 flex-1 leading-tight">
        <p className="text-body-lg font-medium">{label}</p>
        <p className="mt-0.5 truncate text-body-sm" style={{ color: "var(--ink-faint)" }}>
          {!integration.configured
            ? "Not configured on the server"
            : integration.connected && integration.status === "error"
              ? "Connection broken — reconnect to keep sharing"
              : integration.connected
                ? `Connected${integration.workspace_name ? ` to ${integration.workspace_name}` : ""}${integration.account_name ? ` as ${integration.account_name}` : ""}`
                : "Share articles to your channels, as you"}
        </p>
        <ErrorText className="mt-1">{error}</ErrorText>
      </div>
      {integration.configured && (
        <div className="flex shrink-0 items-center gap-2">
          {integration.connected ? (
            <>
              {integration.status === "error" && (
                <button className="btn btn-accent" disabled={busy} onClick={connect}>
                  Reconnect
                </button>
              )}
              <ConfirmButton
                onConfirm={disconnect}
                confirmLabel="Really disconnect?"
                title={`Your saved ${label} channels will be removed`}
                disabled={busy}
              >
                Disconnect
              </ConfirmButton>
            </>
          ) : (
            <button className="btn btn-accent" disabled={busy} onClick={connect}>
              {busy ? "Opening…" : "Connect"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function TargetPicker({ platform }: { platform: MessagingPlatform }) {
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);

  // SWR keyed on the debounced query replaces the debounce+cancel effect:
  // stale responses drop out because their key is no longer rendered.
  const q = useDebouncedValue(query.trim(), 300);
  const {
    data: options,
    error: loadError,
    isLoading: loading,
    mutate: mutateOptions,
  } = useSWR<TargetOption[]>(
    `/integrations/${platform}/targets?q=${encodeURIComponent(q)}`,
    fetcher,
  );

  function flipSaved(externalId: string, savedId: number | null) {
    mutateOptions(
      (opts) =>
        opts?.map((o) =>
          o.external_id === externalId ? { ...o, saved_id: savedId } : o,
        ),
      { revalidate: false },
    );
  }

  async function toggle(option: TargetOption) {
    setSavingId(option.external_id);
    setError(null);
    try {
      if (option.saved_id) {
        await api(`/share-targets/${option.saved_id}`, { method: "DELETE" });
        flipSaved(option.external_id, null);
      } else {
        const saved = await api<ShareTarget>("/share-targets", {
          method: "POST",
          body: {
            platform,
            external_id: option.external_id,
            display_name: option.display_name,
            target_type: option.target_type,
            meta: option.meta,
          },
        });
        flipSaved(option.external_id, saved.id);
      }
      mutate(keys.shareTargets);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update the quick-share list");
    } finally {
      setSavingId(null);
    }
  }

  return (
    <div className="mt-3">
      <div className="relative">
        <span
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2"
          style={{ color: "var(--ink-faint)" }}
        >
          <SearchIcon size={14} />
        </span>
        <input
          className="input"
          style={{ paddingLeft: 34 }}
          placeholder={`Search ${PLATFORM_LABELS[platform]} channels and chats…`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      <ErrorText className="mt-2">
        {error ??
          (loadError instanceof Error ? loadError.message : loadError ? "Could not load channels" : null)}
      </ErrorText>
      {loading && options === undefined && (
        <p className="mt-2 text-body-sm" style={{ color: "var(--ink-faint)" }}>
          Loading channels…
        </p>
      )}
      {options && options.length === 0 && !loading && (
        <p className="mt-2 text-body-sm" style={{ color: "var(--ink-faint)" }}>
          Nothing matched.
        </p>
      )}
      {options && options.length > 0 && (
        <div
          className="mt-2 max-h-64 overflow-y-auto rounded-md border"
          style={{ borderColor: "var(--line)", opacity: loading ? 0.6 : 1 }}
        >
          {options.map((option) => (
            <div
              key={option.external_id}
              className="flex items-center gap-2.5 px-3.5 py-2 text-body"
            >
              <span className="min-w-0 flex-1 truncate">{option.display_name}</span>
              <span
                className="font-mono-nr shrink-0 text-caption uppercase"
                style={{ color: "var(--ink-faint)" }}
              >
                {option.target_type}
              </span>
              <button
                className="icon-btn shrink-0"
                style={{ width: 24, height: 24 }}
                disabled={savingId === option.external_id}
                title={option.saved_id ? "Remove from quick share" : "Add to quick share"}
                onClick={() => toggle(option)}
              >
                {option.saved_id ? (
                  <span style={{ color: "var(--accent)" }}>
                    <CheckIcon size={13} />
                  </span>
                ) : (
                  <PlusIcon size={13} />
                )}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SettingsContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [banner, setBanner] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [pickerPlatform, setPickerPlatform] = useState<MessagingPlatform | null>(null);

  const { data: config } = useServerConfig();
  // Messaging disabled on this deployment (e.g. single-user self-hosted):
  // skip the whole Connections/Quick-share area instead of rendering
  // "not configured" cards for platforms that can never be enabled.
  const messagingEnabled = config?.messaging_enabled === true;
  const { data: integrations } = useIntegrations(messagingEnabled);
  const { data: targets } = useShareTargets(messagingEnabled);

  // The OAuth callback redirects back here with ?connected= or ?error=.
  useEffect(() => {
    const connected = searchParams.get("connected");
    const error = searchParams.get("error");
    if (!connected && !error) return;
    setBanner(
      connected
        ? {
            kind: "ok",
            text: `${PLATFORM_LABELS[connected as MessagingPlatform] ?? connected} connected.`,
          }
        : { kind: "error", text: `Connection failed (${error}). Please try again.` },
    );
    mutateIntegrations();
    router.replace("/settings"); // drop the query so refresh doesn't re-banner
  }, [searchParams, router]);

  const connectedPlatforms =
    integrations?.filter((i) => i.connected && i.status === "active") ?? [];

  async function removeTarget(target: ShareTarget) {
    try {
      await api(`/share-targets/${target.id}`, { method: "DELETE" });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not remove the target");
    }
    mutateShareTargets();
  }

  return (
    <>
      <header
        className="sticky top-0 z-20 border-b px-4 pb-4 pt-4 sm:px-6 sm:pt-5"
        style={{
          background: "var(--bg-header)",
          backdropFilter: "blur(10px)",
          borderColor: "var(--line-soft)",
        }}
      >
        <h1 className="text-title font-semibold leading-none tracking-tight">Settings</h1>
      </header>

      <div className="fade-up mx-auto w-full max-w-[720px] px-4 py-6 sm:px-6">
        {banner && (
          <div
            className="mb-5 flex items-center gap-2.5 rounded-md border px-3.5 py-2.5 text-body"
            style={
              banner.kind === "ok"
                ? {
                    borderColor: "var(--accent-border)",
                    background: "var(--accent-soft)",
                    color: "var(--accent-bright)",
                  }
                : { borderColor: "var(--line)", color: "var(--danger)" }
            }
          >
            <span className="flex-1">{banner.text}</span>
            <button className="icon-btn" style={{ width: 20, height: 20 }} onClick={() => setBanner(null)}>
              <XIcon size={12} />
            </button>
          </div>
        )}

        {messagingEnabled && (
          <>
            <section>
              <p className="mono-label">Connections</p>
              <p className="mt-1.5 text-body" style={{ color: "var(--ink-faint)" }}>
                Link a messaging platform to share articles straight into your channels — messages
                are sent as you, from your account.
              </p>
              <div className="mt-3.5 flex flex-col gap-2.5">
                {integrations?.map((integration) => (
                  <ConnectionCard
                    key={integration.platform}
                    integration={integration}
                    onChanged={() => mutate(keys.integrations)}
                  />
                ))}
              </div>
            </section>

            <section className="mt-9">
              <p className="mono-label">Quick share</p>
              <p className="mt-1.5 text-body" style={{ color: "var(--ink-faint)" }}>
                Channels and chats saved here show up as one-tap targets in the share dialog.
              </p>

              {targets && targets.length > 0 && (
                <div className="mt-3.5 flex flex-col gap-1">
                  {targets.map((target) => (
                    <div
                      key={target.id}
                      className="group flex items-center gap-2.5 rounded-md border px-3.5 py-2 text-body"
                      style={{ background: "var(--bg-raised)", borderColor: "var(--line)" }}
                    >
                      <span style={{ color: "var(--ink-faint)" }}>
                        <PlatformIcon platform={target.platform} size={14} />
                      </span>
                      <span className="min-w-0 flex-1 truncate">{target.display_name}</span>
                      <button
                        className="icon-btn shrink-0 opacity-0 group-hover:opacity-100"
                        style={{ width: 24, height: 24 }}
                        title="Remove"
                        onClick={() => removeTarget(target)}
                      >
                        <TrashIcon size={13} />
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {connectedPlatforms.length === 0 ? (
                <p className="mt-3 text-body" style={{ color: "var(--ink-faint)" }}>
                  Connect a platform above to start saving quick-share targets.
                </p>
              ) : (
                <div className="mt-3.5">
                  <div className="flex gap-2">
                    {connectedPlatforms.map((integration) => (
                      <button
                        key={integration.platform}
                        className="btn"
                        style={
                          pickerPlatform === integration.platform
                            ? { borderColor: "var(--accent-border)", color: "var(--accent-bright)" }
                            : undefined
                        }
                        onClick={() =>
                          setPickerPlatform((p) =>
                            p === integration.platform ? null : integration.platform,
                          )
                        }
                      >
                        <PlatformIcon platform={integration.platform} size={14} />
                        {pickerPlatform === integration.platform
                          ? "Close"
                          : `Browse ${PLATFORM_LABELS[integration.platform]}`}
                      </button>
                    ))}
                  </div>
                  {pickerPlatform && <TargetPicker platform={pickerPlatform} />}
                </div>
              )}
            </section>
          </>
        )}

        <NotInterestedSection />

        <AISettingsSection />
      </div>
    </>
  );
}

export default function SettingsPage() {
  // useSearchParams needs a Suspense boundary during prerender.
  return (
    <Suspense fallback={null}>
      <SettingsContent />
    </Suspense>
  );
}
