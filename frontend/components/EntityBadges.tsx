"use client";

import { type EntityBadge } from "@/lib/api";
import { humanCount } from "@/lib/format";

function asNumber(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

export function badgeParts(entity: EntityBadge): string[] {
  const b = entity.badge;
  switch (entity.kind) {
    case "github": {
      const parts: string[] = [];
      const stars = asNumber(b.stars);
      if (stars != null) parts.push(`★ ${humanCount(stars)}`);
      if (b.language) parts.push(String(b.language));
      if (b.license) parts.push(String(b.license));
      return parts;
    }
    case "hf_model":
    case "hf_dataset": {
      const parts: string[] = [];
      const downloads = asNumber(b.downloads);
      const likes = asNumber(b.likes);
      const params = asNumber(b.params);
      if (downloads != null) parts.push(`⬇ ${humanCount(downloads)}`);
      if (likes != null) parts.push(`♥ ${humanCount(likes)}`);
      if (params != null) parts.push(`${humanCount(params)} params`);
      return parts;
    }
    case "arxiv": {
      const parts: string[] = ["arXiv"];
      if (b.primary_category) parts.push(String(b.primary_category));
      if (b.authors_short) parts.push(String(b.authors_short));
      return parts;
    }
    case "pypi": {
      const parts: string[] = ["PyPI"];
      if (b.version) parts.push(`v${b.version}`);
      return parts;
    }
    case "npm": {
      const parts: string[] = ["npm"];
      if (b.version) parts.push(`v${b.version}`);
      const downloads = asNumber(b.downloads_last_week);
      if (downloads != null) parts.push(`⬇ ${humanCount(downloads)}/wk`);
      return parts;
    }
    case "youtube": {
      const parts: string[] = ["YouTube"];
      if (b.channel) parts.push(String(b.channel));
      return parts;
    }
    default:
      return [];
  }
}

export default function EntityBadges({
  entities,
  max = 1,
}: {
  entities: EntityBadge[];
  max?: number;
}) {
  const shown = entities
    .map((e) => ({ entity: e, parts: badgeParts(e) }))
    .filter(({ entity, parts }) => parts.length > 0 && entity.badge.label)
    .slice(0, max);
  if (shown.length === 0) return null;

  return (
    <span className="font-mono-nr inline-flex flex-wrap items-center gap-x-3 text-label">
      {shown.map(({ entity, parts }) => (
        <span key={entity.id} className="inline-flex items-center gap-1.5">
          <span style={{ color: "var(--accent)" }}>{parts[0]}</span>
          {parts.slice(1).map((part, i) => (
            <span key={i} style={{ color: "var(--ink-faint)" }}>
              {" · "}
              {part}
            </span>
          ))}
        </span>
      ))}
    </span>
  );
}
