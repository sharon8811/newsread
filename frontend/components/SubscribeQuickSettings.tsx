"use client";

import type { SubscribeOptions } from "@/lib/api";
import Chip from "./ui/Chip";

// Quick settings offered at subscribe time; everything else lives in the full
// feed settings modal after subscribing.
export type SubscribeSettings = {
  ai_enabled: boolean;
  image_gen_enabled: boolean;
  is_muted: boolean;
};

export const DEFAULT_SUBSCRIBE_SETTINGS: SubscribeSettings = {
  ai_enabled: true,
  image_gen_enabled: true,
  is_muted: false,
};

/** Only deviations from the defaults go on the wire: ai/image toggles are
 * global per-feed, so an untouched checkbox must not reset another
 * subscriber's choice on an existing feed. */
export function toSubscribeOptions(settings: SubscribeSettings): SubscribeOptions {
  const options: SubscribeOptions = {};
  if (!settings.ai_enabled) options.ai_enabled = false;
  if (!settings.image_gen_enabled) options.image_gen_enabled = false;
  if (settings.is_muted) options.is_muted = true;
  return options;
}

const FIELDS: { key: keyof SubscribeSettings; label: string; hint: string }[] = [
  { key: "ai_enabled", label: "AI summaries", hint: "Summarize new articles automatically" },
  { key: "image_gen_enabled", label: "AI images", hint: "Generate images for stories missing one" },
  { key: "is_muted", label: "Mute", hint: "Hide from Inbox and unread counts" },
];

export default function SubscribeQuickSettings({
  value,
  onChange,
  disabled,
}: {
  value: SubscribeSettings;
  onChange: (value: SubscribeSettings) => void;
  disabled?: boolean;
}) {
  return (
    <fieldset className="min-w-0" disabled={disabled}>
      <legend className="mono-label">Quick settings</legend>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {FIELDS.map(({ key, label, hint }) => (
          <Chip
            key={key}
            active={value[key]}
            title={hint}
            onClick={() => onChange({ ...value, [key]: !value[key] })}
          >
            {label}
          </Chip>
        ))}
      </div>
    </fieldset>
  );
}
