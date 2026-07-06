"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import useSWR, { mutate } from "swr";
import ProjectPinCard, { groupPins } from "@/components/ProjectPinCard";
import { LockIcon, PlusIcon, TrashIcon, XIcon } from "@/components/icons";
import {
  api,
  fetcher,
  type Project,
  type ProjectArticle,
  type UserPublic,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { user } = useAuth();
  const projectKey = `/projects/${id}`;
  const { data: project, error } = useSWR<Project>(projectKey, fetcher);
  const listKey = `/projects/${id}/articles`;
  const { data: pins, isLoading } = useSWR<ProjectArticle[]>(listKey, fetcher);

  const [tab, setTab] = useState<"shared" | "mine">("shared");
  const [inviting, setInviting] = useState(false);
  const [invitee, setInvitee] = useState("");
  const [results, setResults] = useState<UserPublic[]>([]);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isOwner = project?.my_role === "owner";
  const sharedGroups = groupPins((pins ?? []).filter((p) => p.is_shared));
  const privatePins = (pins ?? []).filter((p) => !p.is_shared);

  // Debounced like ShareModal's search; the cleanup also cancels the pending
  // request's effect, so a slow early response can't overwrite newer results.
  useEffect(() => {
    const q = invitee.trim().replace(/^@/, "");
    if (!q) {
      setResults([]);
      return;
    }
    let stale = false;
    const t = setTimeout(() => {
      api<UserPublic[]>(`/users/search?q=${encodeURIComponent(q)}`)
        .then((users) => {
          if (stale) return;
          setResults(users.filter((u) => !project?.members.some((m) => m.user.id === u.id)));
        })
        .catch(() => {
          if (!stale) setResults([]);
        });
    }, 200);
    return () => {
      stale = true;
      clearTimeout(t);
    };
  }, [invitee, project]);

  async function invite(username: string) {
    if (busy) return;
    setBusy(true);
    setActionError(null);
    try {
      await api(`/projects/${id}/members`, { method: "POST", body: { username } });
      setInvitee("");
      setResults([]);
      setInviting(false);
      mutate(projectKey);
      mutate("/projects");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not invite");
    } finally {
      setBusy(false);
    }
  }

  async function removeMember(userId: number) {
    if (busy) return;
    setBusy(true);
    setActionError(null);
    try {
      await api(`/projects/${id}/members/${userId}`, { method: "DELETE" });
      if (userId === user?.id) {
        router.push("/projects");
        mutate("/projects");
        return;
      }
      mutate(projectKey);
      mutate("/projects");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not remove member");
    } finally {
      setBusy(false);
    }
  }

  async function deleteProject() {
    if (busy) return;
    setBusy(true);
    try {
      await api(`/projects/${id}`, { method: "DELETE" });
      mutate("/projects");
      router.push("/projects");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not delete project");
      setBusy(false);
    }
  }

  if (error) {
    return (
      <div className="flex flex-col items-center px-8 py-28 text-center">
        <p className="text-[17px] font-medium" style={{ color: "var(--ink-dim)" }}>
          This project is out of reach.
        </p>
        <button className="btn mt-5" onClick={() => router.push("/projects")}>
          Back to projects
        </button>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="px-6 py-10">
        <div className="h-8 w-1/3 rounded-md" style={{ background: "var(--bg-hover)" }} />
      </div>
    );
  }

  return (
    <>
      <header
        className="sticky top-0 z-20 border-b px-4 pb-0 pt-4 sm:px-6 sm:pt-5"
        style={{
          background: "var(--bg-header)",
          backdropFilter: "blur(10px)",
          borderColor: "var(--line-soft)",
        }}
      >
        <div className="flex items-center gap-3">
          <h1 className="text-[20px] font-semibold leading-none tracking-tight">
            {project.name}
          </h1>
          <div className="ml-auto flex items-center gap-1">
            {project.members.map((m) => (
              <span
                key={m.user.id}
                className="group/member relative flex h-7 w-7 items-center justify-center rounded-full text-[12px] font-semibold"
                style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
                title={`${m.user.name} (@${m.user.username})${m.role === "owner" ? " · owner" : ""}`}
              >
                {m.user.name[0]?.toUpperCase()}
                {isOwner && m.role !== "owner" && (
                  <button
                    className="icon-btn absolute -right-1 -top-1 h-4 w-4 opacity-0 group-hover/member:opacity-100"
                    style={{ width: 16, height: 16, background: "var(--bg-raised)" }}
                    title={`Remove ${m.user.name}`}
                    onClick={() => removeMember(m.user.id)}
                  >
                    <XIcon size={9} />
                  </button>
                )}
              </span>
            ))}
            {isOwner && (
              <button
                className="icon-btn"
                title="Invite someone"
                onClick={() => setInviting((v) => !v)}
              >
                <PlusIcon size={14} />
              </button>
            )}
            {isOwner ? (
              confirmingDelete ? (
                <span className="fade-up flex items-center gap-1.5">
                  <span className="text-[12px]" style={{ color: "var(--ink-dim)" }}>
                    Delete for every member?
                  </span>
                  <button
                    className="btn"
                    style={{ fontSize: 12, color: "var(--danger)" }}
                    disabled={busy}
                    onClick={deleteProject}
                  >
                    Delete
                  </button>
                  <button
                    className="btn"
                    style={{ fontSize: 12 }}
                    onClick={() => setConfirmingDelete(false)}
                  >
                    Cancel
                  </button>
                </span>
              ) : (
                <button
                  className="icon-btn"
                  title="Delete project"
                  onClick={() => setConfirmingDelete(true)}
                >
                  <TrashIcon size={14} />
                </button>
              )
            ) : (
              <button
                className="btn"
                style={{ fontSize: 12 }}
                onClick={() => user && removeMember(user.id)}
              >
                Leave
              </button>
            )}
          </div>
        </div>

        {project.description && (
          <p className="mt-1.5 text-[13px]" style={{ color: "var(--ink-dim)" }}>
            {project.description}
          </p>
        )}

        {inviting && (
          <div className="relative mt-3 max-w-[320px]">
            <input
              className="input"
              style={{ fontSize: 13, padding: "7px 10px" }}
              placeholder="@username to invite"
              value={invitee}
              onChange={(e) => setInvitee(e.target.value)}
              autoFocus
            />
            {results.length > 0 && (
              <div
                className="absolute left-0 right-0 top-full z-10 mt-1.5 overflow-hidden rounded-md border"
                style={{ background: "var(--bg-raised)", borderColor: "var(--line)" }}
              >
                {results.map((u) => (
                  <button
                    key={u.id}
                    className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left transition-colors hover:bg-[var(--bg-hover)]"
                    onClick={() => invite(u.username)}
                  >
                    <span className="text-[13.5px]">{u.name}</span>
                    <span className="font-mono-nr text-[11.5px]" style={{ color: "var(--ink-faint)" }}>
                      @{u.username}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {actionError && (
          <p className="mt-2 text-[12.5px]" style={{ color: "var(--danger)" }}>
            {actionError}
          </p>
        )}

        <div className="mt-3 flex gap-5">
          {(["shared", "mine"] as const).map((t) => (
            <button
              key={t}
              className="flex items-center gap-1.5 border-b-2 pb-2.5 text-[13px] transition-colors"
              style={{
                borderColor: tab === t ? "var(--accent)" : "transparent",
                color: tab === t ? "var(--ink)" : "var(--ink-dim)",
              }}
              onClick={() => setTab(t)}
            >
              {t === "mine" && <LockIcon size={12} />}
              {t === "shared" ? "Shared" : "Only you"}
              <span className="font-mono-nr text-[11px]" style={{ color: "var(--ink-faint)" }}>
                {t === "shared" ? sharedGroups.length : privatePins.length}
              </span>
            </button>
          ))}
        </div>
      </header>

      {!isLoading && tab === "shared" && sharedGroups.length === 0 && (
        <div className="flex flex-col items-center px-8 py-24 text-center">
          <p className="text-[17px] font-medium" style={{ color: "var(--ink-dim)" }}>
            Nothing shared yet.
          </p>
          <p className="mt-2 max-w-md text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
            Pin an article from your reading and share it — it lands here for
            everyone in the project.
          </p>
        </div>
      )}
      {!isLoading && tab === "mine" && privatePins.length === 0 && (
        <div className="flex flex-col items-center px-8 py-24 text-center">
          <p className="text-[17px] font-medium" style={{ color: "var(--ink-dim)" }}>
            Your private pile is empty.
          </p>
          <p className="mt-2 max-w-md text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
            Articles you add privately are visible only to you until you share them.
          </p>
        </div>
      )}

      <div className="fade-up">
        {(tab === "shared" ? sharedGroups : privatePins.map((p) => [p])).map((group) => (
          <ProjectPinCard
            key={group[0].id}
            pins={group}
            myId={user?.id ?? 0}
            isOwner={isOwner}
            projectName={project.name}
          />
        ))}
      </div>
    </>
  );
}
