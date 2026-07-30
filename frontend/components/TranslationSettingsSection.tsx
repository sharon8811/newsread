"use client";

import useSWR from "swr";
import { api, fetcher, type AiStatus, type TranslationLanguage, type User } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { keys } from "@/lib/keys";
import { useMutation } from "@/lib/useMutation";

/** The saved default for the Translate action on summaries. It is set the
 * first time a reader translates something; this is where they change it. */
export default function TranslationSettingsSection() {
  const { user, updateUser } = useAuth();
  const { data: status } = useSWR<AiStatus>(keys.aiStatus, fetcher);
  const { data: languages } = useSWR<TranslationLanguage[]>(
    status?.translation ? keys.translationLanguages : null,
    fetcher,
  );

  const save = useMutation(
    (code: string) =>
      api<User>("/users/me", {
        method: "PATCH",
        body: { translation_language: code || null },
      }),
    {
      onSuccess: updateUser,
      surface: "toast",
      fallbackError: "Could not save the language",
    },
  );

  if (!user || !status?.translation) return null;

  return (
    <section className="mt-9">
      <p className="mono-label">Translation</p>
      <div
        className="mt-3.5 flex items-center gap-3.5 rounded-lg border p-4"
        style={{ background: "var(--bg-raised)", borderColor: "var(--line)" }}
      >
        <div className="min-w-0 flex-1 leading-tight">
          <p className="text-body-lg font-medium">Summary language</p>
          <p className="mt-0.5 text-body-sm" style={{ color: "var(--ink-faint)" }}>
            Translating a summary sends it to this language. Summaries are still written in
            the source’s own language — nothing is translated until you ask.
          </p>
        </div>
        <select
          className="input w-auto shrink-0"
          aria-label="Summary language"
          value={user.translation_language ?? ""}
          disabled={save.busy}
          onChange={(event) => save.run(event.target.value)}
        >
          <option value="">Ask me</option>
          {(languages ?? []).map((language) => (
            <option key={language.code} value={language.code}>
              {language.name}
            </option>
          ))}
        </select>
      </div>
    </section>
  );
}
