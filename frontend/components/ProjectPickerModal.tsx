"use client";

import { useEffect, useState } from "react";
import useSWR, { mutate } from "swr";
import {
  api,
  fetcher,
  type Article,
  type ArticleProjectStatus,
  type Project,
  type ProjectArticle,
} from "@/lib/api";
import { CheckIcon, FolderIcon, LockIcon, PlusIcon, UsersIcon, XIcon } from "./icons";

const VIS_KEY = "newsread_project_vis";

function loadVisibility(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(VIS_KEY) ?? "{}");
  } catch {
    return {};
  }
}

export default function ProjectPickerModal({
  article,
  onClose,
}: {
  article: Article;
  onClose: () => void;
}) {
  const { data: projects } = useSWR<Project[]>("/projects", fetcher);
  const statusKey = `/projects/article/${article.id}`;
  const { data: statuses } = useSWR<ArticleProjectStatus[]>(statusKey, fetcher);

  const [note, setNote] = useState("");
  // Last-used visibility per project; default is private ("only you").
  const [visibility, setVisibility] = useState<Record<string, boolean>>({});
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setVisibility(loadVisibility());
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const statusFor = (projectId: number) =>
    statuses?.find((s) => s.project_id === projectId);

  function setVis(projectId: number, shared: boolean) {
    const next = { ...visibility, [projectId]: shared };
    setVisibility(next);
    localStorage.setItem(VIS_KEY, JSON.stringify(next));
  }

  async function add(projectId: number) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api<ProjectArticle>(`/projects/${projectId}/articles`, {
        method: "POST",
        body: {
          article_id: article.id,
          is_shared: visibility[projectId] ?? false,
          note: note.trim() || null,
        },
      });
      mutate(statusKey);
      mutate("/projects");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add");
    } finally {
      setBusy(false);
    }
  }

  async function remove(projectId: number, pinId: number) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/projects/${projectId}/articles/${pinId}`, { method: "DELETE" });
      mutate(statusKey);
      mutate("/projects");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove");
    } finally {
      setBusy(false);
    }
  }

  async function createProject(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim() || busy) return;
    setBusy(true);
    setError(null);
    let project: Project | null = null;
    try {
      project = await api<Project>("/projects", {
        method: "POST",
        body: { name: newName.trim() },
      });
      // A brand-new project has only you in it — add privately right away.
      await api<ProjectArticle>(`/projects/${project.id}/articles`, {
        method: "POST",
        body: { article_id: article.id, is_shared: false, note: note.trim() || null },
      });
      setNewName("");
      setCreating(false);
      mutate(statusKey);
    } catch (err) {
      const detail = err instanceof Error ? err.message : null;
      // The project may exist even when the pin call failed — say so instead
      // of inviting a retry that would create a duplicate.
      setError(
        project
          ? `Created "${project.name}", but couldn't add the article${detail ? `: ${detail}` : ""}`
          : (detail ?? "Could not create project"),
      );
    } finally {
      // Revalidate even on failure: a half-completed create must show up.
      if (project) mutate("/projects");
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-6"
      style={{ background: "var(--bg-scrim)", backdropFilter: "blur(4px)" }}
      onClick={onClose}
    >
      <div
        className="fade-up w-full max-w-[480px] rounded-lg border p-6"
        style={{
          background: "var(--bg-raised)",
          borderColor: "var(--line)",
          boxShadow: "var(--shadow-modal)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="mono-label">Add to project</p>
            <h2 className="font-serif-nr mt-1.5 text-[19px] leading-snug">
              {article.title}
            </h2>
          </div>
          <button className="icon-btn shrink-0" onClick={onClose}>
            <XIcon size={16} />
          </button>
        </div>

        <div className="mt-5 flex max-h-[320px] flex-col gap-1 overflow-y-auto">
          {projects?.length === 0 && !creating && (
            <p className="py-4 text-center text-[13px]" style={{ color: "var(--ink-faint)" }}>
              No projects yet — create your first below.
            </p>
          )}
          {projects?.map((project) => {
            const status = statusFor(project.id);
            const added = status?.project_article_id != null;
            const shared = visibility[project.id] ?? false;
            return (
              <div
                key={project.id}
                className="flex items-center gap-3 rounded-md border px-3.5 py-2.5"
                style={{ borderColor: "var(--line-soft)" }}
              >
                <span style={{ color: "var(--ink-faint)" }}>
                  <FolderIcon size={15} />
                </span>
                <div className="min-w-0 flex-1 leading-tight">
                  <p className="truncate text-[13.5px]">{project.name}</p>
                  <p className="font-mono-nr text-[10.5px]" style={{ color: "var(--ink-faint)" }}>
                    {project.members.length}{" "}
                    {project.members.length === 1 ? "member" : "members"}
                    {status?.shared_by_others ? " · already shared here" : ""}
                  </p>
                </div>
                {added ? (
                  <>
                    <span
                      className="font-mono-nr flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px]"
                      style={{
                        borderColor: "var(--accent-border)",
                        background: "var(--accent-soft)",
                        color: "var(--accent-bright)",
                      }}
                    >
                      <CheckIcon size={11} />
                      {status?.is_shared ? "Shared" : "Only you"}
                    </span>
                    <button
                      className="icon-btn"
                      title="Remove from project"
                      onClick={() => remove(project.id, status!.project_article_id!)}
                    >
                      <XIcon size={13} />
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      className="icon-btn"
                      title={
                        shared
                          ? `Will be visible to everyone in ${project.name} — click for only you`
                          : "Only you will see it — click to share with members"
                      }
                      onClick={() => setVis(project.id, !shared)}
                    >
                      {shared ? <UsersIcon size={14} /> : <LockIcon size={14} />}
                    </button>
                    <button
                      className="btn"
                      style={{ padding: "4px 12px", fontSize: 12.5 }}
                      // Until statuses load we can't tell "not added" from
                      // "already added" — don't invite a duplicate 409.
                      disabled={busy || !statuses}
                      onClick={() => add(project.id)}
                    >
                      Add
                    </button>
                  </>
                )}
              </div>
            );
          })}
        </div>

        {creating ? (
          <form onSubmit={createProject} className="fade-up mt-3 flex gap-2">
            <input
              className="input flex-1"
              style={{ fontSize: 13, padding: "7px 10px" }}
              placeholder="Project name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              autoFocus
            />
            <button className="btn btn-accent" disabled={busy || !newName.trim()} type="submit">
              Create & add
            </button>
          </form>
        ) : (
          <button
            className="btn mt-3"
            onClick={() => setCreating(true)}
            style={{ fontSize: 12.5 }}
          >
            <PlusIcon size={13} />
            New project
          </button>
        )}

        <textarea
          className="input mt-3 resize-none font-serif-nr italic"
          style={{ fontSize: 15, minHeight: 72 }}
          placeholder="Optional note — why does this belong here?"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />

        {error && (
          <p className="mt-2 text-[12.5px]" style={{ color: "var(--danger)" }}>
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
