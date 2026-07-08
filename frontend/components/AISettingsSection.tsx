"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";
import {
  api,
  fetcher,
  AI_PROVIDER_LABELS,
  type AIProvider,
  type AISettings,
  type AISettingsSave,
  type AITestResult,
} from "@/lib/api";

const PROVIDERS: AIProvider[] = ["openai", "anthropic", "custom"];

const MODEL_PLACEHOLDERS: Record<AIProvider, string> = {
  openai: "e.g. gpt-4o-mini",
  anthropic: "e.g. claude-sonnet-4-5",
  custom: "the model your endpoint serves",
};

// "system" = no row saved; interactive AI runs on the server-wide default.
type ProviderChoice = "system" | AIProvider;

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-[12px] font-medium" style={{ color: "var(--ink-dim)" }}>
        {label}
        {hint && (
          <span className="ml-1.5 font-normal" style={{ color: "var(--ink-faint)" }}>
            {hint}
          </span>
        )}
      </span>
      <span className="mt-1.5 block">{children}</span>
    </label>
  );
}

export default function AISettingsSection() {
  const { data: settings } = useSWR<AISettings>("/ai/settings", fetcher);
  // The form initializes its state from the stored settings, so it only
  // mounts once they're known; afterwards the user's edits win until save.
  if (!settings) return null;
  return <AISettingsForm settings={settings} />;
}

function AISettingsForm({ settings }: { settings: AISettings }) {
  const stored = settings.configured && settings.provider ? settings : null;
  const [choice, setChoice] = useState<ProviderChoice>(stored?.provider ?? "system");
  const [model, setModel] = useState(stored?.model ?? "");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(stored?.base_url ?? "");
  const [imageEnabled, setImageEnabled] = useState(stored?.image != null);
  const [imageProvider, setImageProvider] = useState<AIProvider>(
    stored?.image?.provider ?? "openai",
  );
  const [imageModel, setImageModel] = useState(stored?.image?.model ?? "");
  const [imageApiKey, setImageApiKey] = useState("");
  const [imageBaseUrl, setImageBaseUrl] = useState(stored?.image?.base_url ?? "");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const ownKey = choice !== "system";
  // The backend never reuses a stored key for a different provider.
  const keyRequired =
    ownKey && (!settings?.configured || settings.provider !== choice || !settings.key_hint);
  const storedHint =
    settings?.configured && settings.provider === choice && settings.key_hint
      ? settings.key_hint
      : null;
  const imageStored = settings?.image ?? null;
  const imageStoredHint =
    imageStored && imageStored.provider === imageProvider ? imageStored.key_hint : null;

  function buildBody(): AISettingsSave {
    const body: AISettingsSave = {
      provider: choice as AIProvider,
      model: model.trim(),
    };
    if (apiKey.trim()) body.api_key = apiKey.trim();
    if (choice === "custom") body.base_url = baseUrl.trim();
    if (imageEnabled && imageModel.trim()) {
      body.image = { provider: imageProvider, model: imageModel.trim() };
      if (imageApiKey.trim()) body.image.api_key = imageApiKey.trim();
      if (imageProvider === "custom") body.image.base_url = imageBaseUrl.trim();
    }
    return body;
  }

  async function save() {
    setBusy(true);
    setNote(null);
    try {
      if (!ownKey) {
        if (settings?.configured) await api("/ai/settings", { method: "DELETE" });
        setApiKey("");
        setImageApiKey("");
        setNote({ kind: "ok", text: "Using the system default model." });
      } else {
        await api<AISettings>("/ai/settings", { method: "PUT", body: buildBody() });
        setApiKey("");
        setImageApiKey("");
        setNote({ kind: "ok", text: "AI settings saved." });
      }
      mutate("/ai/settings");
      mutate("/ai/status");
    } catch (err) {
      setNote({
        kind: "error",
        text: err instanceof Error ? err.message : "Could not save the AI settings",
      });
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    setBusy(true);
    setNote(null);
    try {
      // With a freshly typed key the whole form is tested; otherwise the
      // backend probes the stored settings (and refuses mixed ones).
      const body = apiKey.trim()
        ? {
            provider: choice as AIProvider,
            model: model.trim(),
            api_key: apiKey.trim(),
            base_url: choice === "custom" ? baseUrl.trim() : "",
          }
        : { model: model.trim() || undefined };
      const result = await api<AITestResult>("/ai/settings/test", { method: "POST", body });
      setNote(
        result.ok
          ? { kind: "ok", text: `Connection works (${result.model}).` }
          : { kind: "error", text: `The key didn't work: ${result.detail ?? "unknown error"}` },
      );
    } catch (err) {
      setNote({
        kind: "error",
        text: err instanceof Error ? err.message : "Could not test the connection",
      });
    } finally {
      setBusy(false);
    }
  }

  const canSubmit =
    !busy &&
    (!ownKey ||
      (model.trim().length > 0 &&
        (!keyRequired || apiKey.trim().length > 0) &&
        (choice !== "custom" || baseUrl.trim().length > 0)));

  return (
    <section className="mt-9">
      <p className="mono-label">AI model</p>
      <p className="mt-1.5 text-[13px]" style={{ color: "var(--ink-faint)" }}>
        Summaries, article Q&amp;A and share messages run on the system default model for now —
        it will become a paid tier. Bring your own API key to keep AI usage on your account.
      </p>
      {settings && !settings.system_available && !settings.configured && (
        <p className="mt-2 text-[13px]" style={{ color: "var(--danger)" }}>
          No system default is configured on this server — AI features need your own key.
        </p>
      )}

      <div
        className="mt-3.5 flex flex-col gap-3.5 rounded-lg border p-4"
        style={{ background: "var(--bg-raised)", borderColor: "var(--line)" }}
      >
        <Field label="Model provider">
          <select
            className="input"
            style={{ fontSize: 13.5 }}
            aria-label="Model provider"
            value={choice}
            onChange={(e) => setChoice(e.target.value as ProviderChoice)}
          >
            <option value="system">
              System default{settings?.system_available === false ? " (not available)" : ""}
            </option>
            {PROVIDERS.map((p) => (
              <option key={p} value={p}>
                {AI_PROVIDER_LABELS[p]}
              </option>
            ))}
          </select>
        </Field>

        {ownKey && (
          <>
            <Field
              label="API key"
              hint={
                storedHint
                  ? `saved key ends in …${storedHint}; leave blank to keep it`
                  : "stored encrypted, never shown again"
              }
            >
              <input
                className="input"
                style={{ fontSize: 13.5 }}
                type="password"
                autoComplete="off"
                placeholder={storedHint ? `••••••••${storedHint}` : "sk-…"}
                aria-label="API key"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
            </Field>

            {choice === "custom" && (
              <Field label="Base URL" hint="any OpenAI-compatible endpoint">
                <input
                  className="input"
                  style={{ fontSize: 13.5 }}
                  placeholder="http://localhost:11434/v1"
                  aria-label="Base URL"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                />
              </Field>
            )}

            <Field label="Model">
              <input
                className="input"
                style={{ fontSize: 13.5 }}
                placeholder={MODEL_PLACEHOLDERS[choice as AIProvider]}
                aria-label="Model"
                value={model}
                onChange={(e) => setModel(e.target.value)}
              />
            </Field>

            <div className="border-t pt-3.5" style={{ borderColor: "var(--line-soft)" }}>
              <label className="flex items-center gap-2 text-[13px]">
                <input
                  type="checkbox"
                  checked={imageEnabled}
                  onChange={(e) => setImageEnabled(e.target.checked)}
                />
                <span>Image generation for articles without a picture (optional)</span>
              </label>
              {imageEnabled && (
                <div className="mt-3 flex flex-col gap-3">
                  <Field label="Image provider">
                    <select
                      className="input"
                      style={{ fontSize: 13.5 }}
                      aria-label="Image provider"
                      value={imageProvider}
                      onChange={(e) => setImageProvider(e.target.value as AIProvider)}
                    >
                      {PROVIDERS.map((p) => (
                        <option key={p} value={p}>
                          {AI_PROVIDER_LABELS[p]}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Image model">
                    <input
                      className="input"
                      style={{ fontSize: 13.5 }}
                      placeholder="e.g. gpt-image-1"
                      aria-label="Image model"
                      value={imageModel}
                      onChange={(e) => setImageModel(e.target.value)}
                    />
                  </Field>
                  <Field
                    label="Image API key"
                    hint={
                      imageProvider === choice
                        ? "optional — uses your main key"
                        : imageStoredHint
                          ? `saved key ends in …${imageStoredHint}; leave blank to keep it`
                          : "required for a different provider"
                    }
                  >
                    <input
                      className="input"
                      style={{ fontSize: 13.5 }}
                      type="password"
                      autoComplete="off"
                      placeholder={imageStoredHint ? `••••••••${imageStoredHint}` : "sk-…"}
                      aria-label="Image API key"
                      value={imageApiKey}
                      onChange={(e) => setImageApiKey(e.target.value)}
                    />
                  </Field>
                  {imageProvider === "custom" && (
                    <Field label="Image base URL">
                      <input
                        className="input"
                        style={{ fontSize: 13.5 }}
                        placeholder="http://localhost:11434/v1"
                        aria-label="Image base URL"
                        value={imageBaseUrl}
                        onChange={(e) => setImageBaseUrl(e.target.value)}
                      />
                    </Field>
                  )}
                </div>
              )}
            </div>
          </>
        )}

        {note && (
          <p
            className="text-[12.5px]"
            style={{ color: note.kind === "ok" ? "var(--accent-bright)" : "var(--danger)" }}
          >
            {note.text}
          </p>
        )}

        <div className="flex items-center gap-2">
          {ownKey && (
            <button
              className="btn"
              disabled={busy || (keyRequired && !apiKey.trim()) || !model.trim()}
              onClick={test}
            >
              {busy ? "Working…" : "Test connection"}
            </button>
          )}
          <button className="btn btn-accent" disabled={!canSubmit} onClick={save}>
            Save
          </button>
        </div>
      </div>
    </section>
  );
}
