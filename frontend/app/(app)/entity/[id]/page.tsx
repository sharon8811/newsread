"use client";

import { useParams, useRouter } from "next/navigation";
import { useEntityPage } from "@/lib/queries";
import { timeAgo } from "@/lib/format";
import { ExternalIcon } from "@/components/icons";

const KIND_LABELS: Record<string, string> = {
  person: "Person",
  org: "Organization",
  product: "Product",
  github: "GitHub repository",
  hf_model: "Hugging Face model",
  hf_dataset: "Hugging Face dataset",
  arxiv: "arXiv paper",
  pypi: "PyPI package",
  npm: "npm package",
  youtube: "YouTube video",
};

export default function EntityPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { data: entity, error } = useEntityPage(params?.id);

  if (error) {
    return (
      <div className="px-4 py-10 text-center sm:px-6">
        <p className="text-body-lg" style={{ color: "var(--ink-dim)" }}>
          This entity could not be loaded.
        </p>
      </div>
    );
  }
  if (!entity) return null;

  return (
    <>
      <header
        className="border-b px-4 pb-5 pt-5 sm:px-6"
        style={{ borderColor: "var(--line-soft)" }}
      >
        <button
          className="font-mono-nr mb-4 block text-label transition-colors"
          style={{ color: "var(--ink-faint)" }}
          onMouseEnter={(e) => (e.currentTarget.style.color = "var(--ink)")}
          onMouseLeave={(e) => (e.currentTarget.style.color = "var(--ink-faint)")}
          onClick={() => router.back()}
        >
          ← back
        </button>
        <p className="mono-label">{KIND_LABELS[entity.kind] ?? entity.kind}</p>
        <div className="mt-1 flex items-center gap-2">
          <h1 className="text-display font-semibold leading-tight tracking-tight">
            {entity.name}
          </h1>
          {entity.url && (
            <a href={entity.url} target="_blank" rel="noreferrer" aria-label="Open source page">
              <ExternalIcon size={14} />
            </a>
          )}
        </div>
      </header>

      <div className="px-4 py-5 sm:px-6">
        <p className="mono-label">From your feeds</p>
        {entity.articles.length === 0 ? (
          <p className="mt-3 text-body-lg" style={{ color: "var(--ink-dim)" }}>
            No articles from your feeds mention this yet.
          </p>
        ) : (
          <div className="mt-3 flex flex-col gap-2">
            {entity.articles.map((item) => (
              <div
                key={item.id}
                className="cursor-pointer rounded-md border p-3.5 transition-colors hover:bg-[var(--bg-hover)]"
                style={{ borderColor: "var(--line)", background: "var(--bg-raised)" }}
                onClick={() => router.push(`/article/${item.id}`)}
              >
                <div className="flex items-center gap-2">
                  {!item.is_read && <span className="dot-unread shrink-0" />}
                  <p className="font-serif-nr min-w-0 flex-1 truncate text-lead">
                    {item.title}
                  </p>
                </div>
                <p className="font-mono-nr mt-1 text-label" style={{ color: "var(--ink-faint)" }}>
                  {item.feed_title}
                  {item.published_at ? ` · ${timeAgo(item.published_at)}` : ""}
                </p>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
