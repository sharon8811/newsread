"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher, type Project } from "@/lib/api";
import { FolderIcon, PlusIcon, XIcon } from "@/components/icons";
import ErrorText from "@/components/ui/ErrorText";

export default function ProjectsPage() {
  const { data: projects, isLoading } = useSWR<Project[]>("/projects", fetcher);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api<Project>("/projects", {
        method: "POST",
        body: { name: name.trim(), description: description.trim() },
      });
      setName("");
      setDescription("");
      setCreating(false);
      mutate("/projects");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create project");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <header
        className="sticky top-0 z-20 flex items-center justify-between border-b px-4 pb-4 pt-4 sm:px-6 sm:pt-5"
        style={{
          background: "var(--bg-header)",
          backdropFilter: "blur(10px)",
          borderColor: "var(--line-soft)",
        }}
      >
        <h1 className="text-[20px] font-semibold leading-none tracking-tight">Projects</h1>
        <button
          className="btn"
          onClick={() => {
            setCreating((v) => !v);
            setError(null);
          }}
        >
          {creating ? <XIcon size={13} /> : <PlusIcon size={13} />}
          {creating ? "Cancel" : "New project"}
        </button>
      </header>

      {creating && (
        <form onSubmit={create} className="fade-up border-b px-4 py-4 sm:px-6" style={{ borderColor: "var(--line-soft)" }}>
          <input
            className="input"
            placeholder="Project name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
          <input
            className="input mt-2"
            placeholder="What is this project about? (optional)"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          {error && (
            <ErrorText className="mt-2">
              {error}
            </ErrorText>
          )}
          <button className="btn btn-accent mt-3" disabled={busy || !name.trim()} type="submit">
            {busy ? "Creating…" : "Create project"}
          </button>
        </form>
      )}

      {!isLoading && projects?.length === 0 && !creating && (
        <div className="flex flex-col items-center px-8 py-28 text-center">
          <p className="text-[17px] font-medium" style={{ color: "var(--ink-dim)" }}>
            No projects yet.
          </p>
          <p className="mt-2 max-w-md text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
            A project collects articles around one effort — keep it to yourself or
            invite the people working on it with you.
          </p>
        </div>
      )}

      <div className="fade-up">
        {projects?.map((project) => (
          <Link
            key={project.id}
            href={`/projects/${project.id}`}
            className="flex items-center gap-4 border-b px-5 py-5 transition-colors hover:bg-[var(--bg-raised)]"
            style={{ borderColor: "var(--line-soft)" }}
          >
            <span
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md"
              style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
            >
              <FolderIcon size={17} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="flex items-center gap-2 truncate text-[15px] font-medium">
                {project.name}
                {project.unseen_count > 0 && (
                  <span
                    className="font-mono-nr rounded-full px-1.5 text-[10.5px] leading-[18px]"
                    style={{
                      background: "var(--accent)",
                      color: "var(--accent-ink)",
                      fontWeight: 600,
                    }}
                  >
                    {project.unseen_count}
                  </span>
                )}
              </p>
              {project.description && (
                <p className="mt-0.5 truncate text-[13px]" style={{ color: "var(--ink-dim)" }}>
                  {project.description}
                </p>
              )}
            </div>
            <p
              className="font-mono-nr shrink-0 text-right text-[11px] leading-relaxed"
              style={{ color: "var(--ink-faint)" }}
            >
              {project.article_count} {project.article_count === 1 ? "article" : "articles"}
              <br />
              {project.members.length} {project.members.length === 1 ? "member" : "members"}
            </p>
          </Link>
        ))}
      </div>
    </>
  );
}
