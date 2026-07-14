import type { ArticleEntity } from "./types";

/** LLM name entities: no external resource behind them, so their chips
 * navigate to the in-app entity page instead of an external URL. */
export const NAME_ENTITY_KINDS = new Set(["person", "org", "product"]);

export const KIND_LABELS: Record<string, string> = {
  person: "Person",
  org: "Org",
  product: "Product",
  github: "GitHub",
  hf_model: "HF model",
  hf_dataset: "HF dataset",
  arxiv: "arXiv",
  pypi: "PyPI",
  npm: "npm",
  youtube: "YouTube",
};

export function entityKindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind;
}

export function isNameEntity(entity: Pick<ArticleEntity, "kind">): boolean {
  return NAME_ENTITY_KINDS.has(entity.kind);
}

/** Display name: enricher badge label, then the stored display name, then
 * the canonical key — same resolution order as the backend's entity page. */
export function entityDisplayName(entity: ArticleEntity): string {
  const label = entity.badge?.label;
  if (typeof label === "string" && label) return label;
  const name = entity.data?.name;
  if (typeof name === "string" && name) return name;
  return entity.key;
}

/** SWR key for the entity page; null (no fetch) until the route param
 * resolves — same convention as relatedKey. */
export const entityKey = (id: number | string | undefined | null) =>
  id ? `/entities/${id}` : null;
