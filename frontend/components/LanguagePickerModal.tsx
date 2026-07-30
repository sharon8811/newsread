"use client";

import { useState } from "react";
import useSWR from "swr";
import { fetcher, type TranslationLanguage } from "@/lib/api";
import { keys } from "@/lib/keys";
import { CheckIcon } from "./icons";
import Modal, { ModalHeader } from "./Modal";

/** Target-language picker for summary translation. Doubles as the "set my
 * default" surface: the first translate opens it, and the choice is saved as
 * the reader's default unless they explicitly ask for a one-off. */
export default function LanguagePickerModal({
  current,
  onPick,
  onClose,
  allowOneOff = false,
}: {
  current: string | null;
  onPick: (code: string, makeDefault: boolean) => void;
  onClose: () => void;
  /** Offer "just this once" — only meaningful once a default exists. */
  allowOneOff?: boolean;
}) {
  const { data: languages } = useSWR<TranslationLanguage[]>(keys.translationLanguages, fetcher);
  // Opened from "another language", so this pick is a one-off unless the
  // reader says otherwise. (On first use there is no checkbox: that pick
  // always becomes the default.)
  const [makeDefault, setMakeDefault] = useState(false);

  return (
    <Modal onClose={onClose} contentClassName="p-5">
      <ModalHeader eyebrow="Translate summary" title="Choose a language" />

      <ul className="mt-4 max-h-[52vh] overflow-y-auto">
        {(languages ?? []).map((language) => (
          <li key={language.code}>
            <button
              className="flex w-full items-center gap-3 rounded px-2.5 py-2 text-left transition-colors hover:bg-[var(--bg-hover)]"
              onClick={() => onPick(language.code, allowOneOff ? makeDefault : true)}
            >
              <span className="text-body-lg" style={{ color: "var(--ink)" }}>
                {language.name}
              </span>
              <span
                className="text-body"
                style={{ color: "var(--ink-faint)" }}
                dir={language.rtl ? "rtl" : "ltr"}
              >
                {language.native_name}
              </span>
              {language.code === current && (
                <CheckIcon size={14} className="ml-auto" aria-label="Current default" />
              )}
            </button>
          </li>
        ))}
      </ul>

      {allowOneOff && (
        <label className="mt-3 flex items-center gap-2 border-t pt-3" style={{ borderColor: "var(--line)" }}>
          <input
            type="checkbox"
            checked={makeDefault}
            onChange={(event) => setMakeDefault(event.target.checked)}
          />
          <span className="text-body" style={{ color: "var(--ink-dim)" }}>
            Make this my default language
          </span>
        </label>
      )}
    </Modal>
  );
}
