"use client";

import Link from "next/link";
import { useState } from "react";
import { mutate } from "swr";
import { api, type Project } from "@/lib/api";
import { keys } from "@/lib/keys";
import { useProjects } from "@/lib/queries";
import { useMutation } from "@/lib/useMutation";
import { FolderIcon, PlusIcon, XIcon } from "@/components/icons";
import EmptyState from "@/components/ui/EmptyState";
import ErrorText from "@/components/ui/ErrorText";

export default function ProjectsPage() {
  const { data: projects, isLoading } = useProjects();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const { run: createProject, busy, error, setError } = useMutation(
    () =>
      api<Project>("/projects", {
        method: "POST",
        body: { name: name.trim(), description: description.trim() },
      }),
    {
      fallbackError: "Could not create project",
      onSuccess() {
        setName("");
        setDescription("");
        setCreating(false);
        mutate(keys.projects);
      },
    },
  );

  function create(e: React.FormEvent) {
    e.preventDefault();
    if (name.trim()) createProject();
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
        <h1 className="text-title font-semibold leading-none tracking-tight">Projects</h1>
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
          <ErrorText className="mt-2">{error}</ErrorText>
          <button className="btn btn-accent mt-3" disabled={busy || !name.trim()} type="submit">
            {busy ? "Creating…" : "Create project"}
          </button>
        </form>
      )}

      {!isLoading && projects?.length === 0 && !creating && (
        <EmptyState
          title="No projects yet."
          subtitle="A project collects articles around one effort — keep it to yourself or invite the people working on it with you."
        />
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
              <p className="flex items-center gap-2 truncate text-body-lg font-medium">
                {project.name}
                {project.unseen_count > 0 && (
                  <span
                    className="font-mono-nr rounded-full px-1.5 text-caption leading-[18px]"
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
                <p className="mt-0.5 truncate text-body" style={{ color: "var(--ink-dim)" }}>
                  {project.description}
                </p>
              )}
            </div>
            <p
              className="font-mono-nr shrink-0 text-right text-label leading-relaxed"
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
