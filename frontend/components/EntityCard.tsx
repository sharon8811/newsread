"use client";

import Link from "next/link";
import { type EntityFull } from "@/lib/api";
import { humanCount, timeAgo } from "@/lib/format";
import { ExternalIcon } from "./icons";

const KIND_LABELS: Record<string, string> = {
  github: "GitHub",
  hf_model: "Hugging Face model",
  hf_dataset: "Hugging Face dataset",
  arxiv: "arXiv",
  pypi: "PyPI",
  npm: "npm",
  youtube: "YouTube",
  person: "Person",
  org: "Org",
  product: "Product",
};

// LLM name entities: no external resource behind them, so they render as
// chips linking to their in-app coverage page, never as the resource card.
const NER_KINDS = new Set(["person", "org", "product"]);

const SPARK_METRIC: Record<string, string> = {
  github: "stargazers_count",
  hf_model: "downloads",
  hf_dataset: "downloads",
};

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function Sparkline({ entity }: { entity: EntityFull }) {
  const metric = SPARK_METRIC[entity.kind];
  if (!metric) return null;
  const points = entity.snapshots
    .map((s) => num(s.data[metric]))
    .filter((v): v is number => v != null)
    .reverse(); // snapshots arrive newest-first
  if (points.length < 3) return null;

  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const coords = points
    .map((v, i) => {
      const x = (i / (points.length - 1)) * 96 + 2;
      const y = 22 - ((v - min) / range) * 18;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg width="100" height="26" aria-hidden className="shrink-0">
      <polyline
        points={coords}
        fill="none"
        stroke="var(--accent)"
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
        opacity="0.85"
      />
    </svg>
  );
}

function statLine(entity: EntityFull): string[] {
  const d = entity.data;
  switch (entity.kind) {
    case "github": {
      const parts: string[] = [];
      const stars = num(d.stargazers_count);
      if (stars != null) {
        const delta = entity.deltas.stargazers_count_delta_7d;
        parts.push(
          `★ ${stars.toLocaleString()}${delta ? ` (${delta > 0 ? "+" : ""}${humanCount(delta)} this week)` : ""}`,
        );
      }
      const forks = num(d.forks_count);
      if (forks != null) parts.push(`⑂ ${humanCount(forks)}`);
      const issues = num(d.open_issues_count);
      if (issues != null) parts.push(`${humanCount(issues)} issues`);
      return parts;
    }
    case "hf_model":
    case "hf_dataset": {
      const parts: string[] = [];
      const downloads = num(d.downloads);
      if (downloads != null) {
        const delta = entity.deltas.downloads_delta_7d;
        parts.push(
          `⬇ ${downloads.toLocaleString()}${delta ? ` (+${humanCount(delta)} this week)` : ""}`,
        );
      }
      const likes = num(d.likes);
      if (likes != null) parts.push(`♥ ${humanCount(likes)}`);
      const params = num(d.params);
      if (params != null) parts.push(`${humanCount(params)} params`);
      if (str(d.pipeline_tag)) parts.push(String(d.pipeline_tag));
      return parts;
    }
    case "arxiv": {
      const parts: string[] = [];
      const authors = Array.isArray(d.authors) ? (d.authors as string[]) : [];
      if (authors.length)
        parts.push(authors.length > 3 ? `${authors.slice(0, 3).join(", ")} et al.` : authors.join(", "));
      if (str(d.primary_category)) parts.push(String(d.primary_category));
      return parts;
    }
    case "pypi":
    case "npm": {
      const parts: string[] = [];
      if (str(d.version)) parts.push(`v${d.version}`);
      const downloads = num(d.downloads_last_week);
      if (downloads != null) parts.push(`⬇ ${humanCount(downloads)}/week`);
      if (str(d.requires_python)) parts.push(`Python ${d.requires_python}`);
      return parts;
    }
    case "youtube": {
      const parts: string[] = [];
      if (str(d.channel)) parts.push(String(d.channel));
      return parts;
    }
    default:
      return [];
  }
}

function footerLine(entity: EntityFull): string[] {
  const d = entity.data;
  const parts: string[] = [];
  if (str(d.language)) parts.push(String(d.language));
  if (str(d.license)) parts.push(String(d.license));
  if (str(d.pushed_at)) parts.push(`updated ${timeAgo(String(d.pushed_at))}`);
  else if (str(d.last_modified)) parts.push(`updated ${timeAgo(String(d.last_modified))}`);
  else if (str(d.released_at)) parts.push(`released ${timeAgo(String(d.released_at))}`);
  else if (str(d.published)) parts.push(`published ${timeAgo(String(d.published))}`);
  return parts;
}

function title(entity: EntityFull): string {
  return (
    str(entity.data.full_name) ??
    str(entity.data.title) ??
    str(entity.data.id) ??
    str(entity.data.name) ??
    entity.key
  );
}

function description(entity: EntityFull): string | null {
  return str(entity.data.description) ?? str(entity.data.summary) ?? str(entity.data.abstract);
}

const chipClass =
  "font-mono-nr inline-flex max-w-full items-center gap-2 rounded-md border px-2.5 py-1 text-[11px] transition-colors";
const chipStyle = {
  borderColor: "var(--line)",
  color: "var(--ink-dim)",
  background: "var(--bg-inset)",
} as const;

function ChipBody({ entity }: { entity: EntityFull }) {
  const stats = statLine(entity);
  return (
    <>
      <span style={{ color: "var(--accent)" }}>{KIND_LABELS[entity.kind] ?? entity.kind}</span>
      <span className="truncate">{title(entity)}</span>
      {stats[0] && <span className="shrink-0" style={{ color: "var(--ink-faint)" }}>{stats[0]}</span>}
    </>
  );
}

function Chip({ entity }: { entity: EntityFull }) {
  if (NER_KINDS.has(entity.kind)) {
    return (
      <Link href={`/entity/${entity.id}`} className={chipClass} style={chipStyle} title={title(entity)}>
        <ChipBody entity={entity} />
      </Link>
    );
  }
  return (
    <a
      href={entity.url}
      target="_blank"
      rel="noreferrer"
      className={chipClass}
      style={chipStyle}
      title={title(entity)}
    >
      <ChipBody entity={entity} />
    </a>
  );
}

export default function EntityCard({ entities }: { entities: EntityFull[] }) {
  // Only enricher entities (with fetched data) qualify for the resource
  // card; name entities are always chips.
  const cardCandidates = entities.filter(
    (e) => !NER_KINDS.has(e.kind) && Object.keys(e.data).length > 0,
  );
  const nameChips = entities.filter((e) => NER_KINDS.has(e.kind));
  if (cardCandidates.length === 0 && nameChips.length === 0) return null;
  const [primary, ...enricherRest] = cardCandidates;
  const rest = [...enricherRest, ...nameChips];
  if (!primary) {
    return (
      <div className="mt-6 flex flex-wrap gap-2">
        {rest.map((entity) => (
          <Chip key={entity.id} entity={entity} />
        ))}
      </div>
    );
  }
  const stats = statLine(primary);
  const footer = footerLine(primary);
  const desc = description(primary);

  return (
    <div className="mt-6">
      <div
        className="rounded-md border p-4"
        style={{ borderColor: "var(--line)", background: "var(--bg-raised)" }}
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="mono-label">{KIND_LABELS[primary.kind] ?? primary.kind}</p>
            <a
              href={primary.url}
              target="_blank"
              rel="noreferrer"
              className="mt-1 flex max-w-full items-center gap-1.5 text-[16px] font-medium hover:underline"
              style={{ color: "var(--ink)" }}
            >
              <span className="truncate">{title(primary)}</span>
              <ExternalIcon size={13} className="shrink-0" />
            </a>
            {desc && (
              <p
                className="mt-1.5 line-clamp-2 text-[13px] leading-relaxed"
                style={{ color: "var(--ink-dim)" }}
              >
                {desc}
              </p>
            )}
          </div>
          <span className="hidden sm:block">
            <Sparkline entity={primary} />
          </span>
        </div>
        {stats.length > 0 && (
          <p className="font-mono-nr mt-3 text-[12px]" style={{ color: "var(--ink)" }}>
            {stats.join("  ·  ")}
          </p>
        )}
        {footer.length > 0 && (
          <p className="font-mono-nr mt-1.5 text-[11px]" style={{ color: "var(--ink-faint)" }}>
            {footer.join(" · ")}
          </p>
        )}
      </div>
      {rest.length > 0 && (
        <div className="mt-2.5 flex flex-wrap gap-2">
          {rest.map((entity) => (
            <Chip key={entity.id} entity={entity} />
          ))}
        </div>
      )}
    </div>
  );
}
