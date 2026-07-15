"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { mutate } from "swr";
import ProjectPinCard, { groupPins } from "@/components/ProjectPinCard";
import QAPanel from "@/components/QAPanel";
import { LockIcon, MuteIcon, PlusIcon, TrashIcon, XIcon } from "@/components/icons";
import { api, streamProjectQA } from "@/lib/api";
import { keys } from "@/lib/keys";
import {
  mutateProject,
  useProject,
  useProjectArticles,
  useUserSearch,
} from "@/lib/queries";
import { useDebouncedValue } from "@/lib/useDebouncedValue";
import { useMutation } from "@/lib/useMutation";
import { useAuth } from "@/lib/auth";
import Avatar from "@/components/ui/Avatar";
import Button from "@/components/ui/Button";
import ErrorText from "@/components/ui/ErrorText";
import Skeleton from "@/components/ui/Skeleton";

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { user } = useAuth();
  const { data: project, error } = useProject(id);
  const { data: pins, isLoading } = useProjectArticles(id);

  const [tab, setTab] = useState<"shared" | "mine" | "ask">("shared");
  // The working view: done tickets step aside until you flip the switch.
  const [statusFilter, setStatusFilter] = useState<"active" | "done">("active");
  const [inviting, setInviting] = useState(false);
  const [invitee, setInvitee] = useState("");
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  // One mutation slot for every header action: same shared busy flag and
  // error line the hand-rolled handlers kept, without four copies of the
  // try/catch/finally machinery.
  const {
    run: runAction,
    busy,
    error: actionError,
  } = useMutation((action: () => Promise<unknown>) => action());

  const isOwner = project?.my_role === "owner";
  const filtered = (pins ?? []).filter(
    (p) => (p.status === "done") === (statusFilter === "done"),
  );
  const sharedGroups = groupPins(filtered.filter((p) => p.is_shared));
  const privatePins = filtered.filter((p) => !p.is_shared);
  const donePins = (pins ?? []).filter((p) => p.status === "done");
  // Cards, not pins: matches the tab counts, which count grouped cards.
  const doneCount =
    groupPins(donePins.filter((p) => p.is_shared)).length +
    donePins.filter((p) => !p.is_shared).length;

  // Opening the project marks it visited: the unseen badge measures from here.
  const visitedRef = useRef(false);
  useEffect(() => {
    if (project && !visitedRef.current) {
      visitedRef.current = true;
      // Background bookkeeping: a lost visit ping only delays the unseen
      // badge reset, so failures are deliberately not surfaced.
      api(`/projects/${id}/visit`, { method: "POST" })
        .then(() => mutate(keys.projects))
        .catch(() => {});
    }
  }, [project, id]);

  // SWR keyed on the debounced query replaces the hand-rolled stale-guard
  // effect; member filtering happens at render time.
  const inviteQuery = useDebouncedValue(invitee.trim().replace(/^@/, ""), 200);
  const { data: userMatches } = useUserSearch(inviting ? inviteQuery : "");
  const results = (userMatches ?? []).filter(
    (u) => !project?.members.some((m) => m.user.id === u.id),
  );

  const invite = (username: string) =>
    runAction(async () => {
      await api(`/projects/${id}/members`, { method: "POST", body: { username } });
      setInvitee("");
      setInviting(false);
      mutateProject(id);
    });

  const removeMember = (userId: number) =>
    runAction(async () => {
      await api(`/projects/${id}/members/${userId}`, { method: "DELETE" });
      if (userId === user?.id) {
        router.push("/projects");
        mutate(keys.projects);
        return;
      }
      mutateProject(id);
    });

  const toggleMute = () =>
    runAction(async () => {
      if (!project) return;
      await api(`/projects/${id}/membership`, {
        method: "PATCH",
        body: { is_muted: !project.is_muted },
      });
      mutateProject(id);
    });

  const deleteProject = () =>
    runAction(async () => {
      await api(`/projects/${id}`, { method: "DELETE" });
      mutate(keys.projects);
      router.push("/projects");
    });

  if (error) {
    return (
      <div className="flex flex-col items-center px-8 py-28 text-center">
        <p className="text-lead font-medium" style={{ color: "var(--ink-dim)" }}>
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
        <Skeleton className="h-8 w-1/3" />
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
          <h1 className="text-title font-semibold leading-none tracking-tight">
            {project.name}
          </h1>
          <div className="ml-auto flex items-center gap-1">
            {project.members.map((m) => (
              <Avatar
                key={m.user.id}
                name={m.user.name}
                className="group/member relative"
                title={`${m.user.name} (@${m.user.username})${m.role === "owner" ? " · owner" : ""}`}
              >
                {isOwner && m.role !== "owner" && (
                  <button
                    className="icon-btn absolute -right-1 -top-1 h-4 w-4 bg-raised opacity-0 group-hover/member:opacity-100"
                    title={`Remove ${m.user.name}`}
                    onClick={() => removeMember(m.user.id)}
                  >
                    <XIcon size={9} />
                  </button>
                )}
              </Avatar>
            ))}
            <button
              className={`icon-btn ${project.is_muted ? "active" : ""}`}
              title={
                project.is_muted
                  ? "Unmute — get notified when members share here"
                  : "Mute notifications from this project"
              }
              disabled={busy}
              onClick={toggleMute}
            >
              <MuteIcon size={13} />
            </button>
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
                  <span className="text-body-sm" style={{ color: "var(--ink-dim)" }}>
                    Delete for every member?
                  </span>
                  <Button
                    variant="danger"
                    size="sm"
                    className="border-line"
                    disabled={busy}
                    onClick={deleteProject}
                  >
                    Delete
                  </Button>
                  <Button size="sm" onClick={() => setConfirmingDelete(false)}>
                    Cancel
                  </Button>
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
          <p className="mt-1.5 text-body" style={{ color: "var(--ink-dim)" }}>
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
                    <span className="text-body">{u.name}</span>
                    <span className="font-mono-nr text-label" style={{ color: "var(--ink-faint)" }}>
                      @{u.username}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {actionError && (
          <ErrorText className="mt-2">
            {actionError}
          </ErrorText>
        )}

        <div className="mt-3 flex items-center gap-5">
          {(["shared", "mine", "ask"] as const).map((t) => (
            <button
              key={t}
              className="flex items-center gap-1.5 border-b-2 pb-2.5 text-body transition-colors"
              style={{
                borderColor: tab === t ? "var(--accent)" : "transparent",
                color: tab === t ? "var(--ink)" : "var(--ink-dim)",
              }}
              onClick={() => setTab(t)}
            >
              {t === "mine" && <LockIcon size={12} />}
              {t === "shared" ? "Shared" : t === "mine" ? "Only you" : "Ask"}
              {t !== "ask" && (
                <span className="font-mono-nr text-label" style={{ color: "var(--ink-faint)" }}>
                  {t === "shared" ? sharedGroups.length : privatePins.length}
                </span>
              )}
            </button>
          ))}
          {tab !== "ask" && (
            <div
              className="ml-auto mb-2 flex overflow-hidden rounded-md border"
              style={{ borderColor: "var(--line)" }}
            >
              {(["active", "done"] as const).map((f) => (
                <button
                  key={f}
                  className="px-2.5 py-1 text-label transition-colors"
                  style={{
                    background: statusFilter === f ? "var(--accent-soft)" : "transparent",
                    color: statusFilter === f ? "var(--accent)" : "var(--ink-dim)",
                  }}
                  onClick={() => setStatusFilter(f)}
                >
                  {f === "active" ? "Active" : `Done${doneCount ? ` ${doneCount}` : ""}`}
                </button>
              ))}
            </div>
          )}
        </div>
      </header>

      {!isLoading && statusFilter === "done" &&
        (tab === "shared" ? sharedGroups : privatePins).length === 0 && tab !== "ask" && (
        <div className="flex flex-col items-center px-8 py-24 text-center">
          <p className="text-lead font-medium" style={{ color: "var(--ink-dim)" }}>
            Nothing marked done yet.
          </p>
          <p className="mt-2 max-w-md text-body" style={{ color: "var(--ink-faint)" }}>
            When an article is handled, mark it done — it moves here with its
            closing note.
          </p>
        </div>
      )}
      {!isLoading && statusFilter === "active" && tab === "shared" && sharedGroups.length === 0 && (
        <div className="flex flex-col items-center px-8 py-24 text-center">
          <p className="text-lead font-medium" style={{ color: "var(--ink-dim)" }}>
            Nothing shared yet.
          </p>
          <p className="mt-2 max-w-md text-body" style={{ color: "var(--ink-faint)" }}>
            Pin an article from your reading and share it — it lands here for
            everyone in the project.
          </p>
        </div>
      )}
      {!isLoading && statusFilter === "active" && tab === "mine" && privatePins.length === 0 && (
        <div className="flex flex-col items-center px-8 py-24 text-center">
          <p className="text-lead font-medium" style={{ color: "var(--ink-dim)" }}>
            Your private pile is empty.
          </p>
          <p className="mt-2 max-w-md text-body" style={{ color: "var(--ink-faint)" }}>
            Articles you add privately are visible only to you until you share them.
          </p>
        </div>
      )}

      {tab === "ask" ? (
        <div className="mx-auto w-full max-w-[680px] px-5 pb-16 sm:px-8">
          <QAPanel
            qaKey={`/projects/${project.id}/qa`}
            stream={(q, onEvent) => streamProjectQA(project.id, q, onEvent)}
            heading="Ask across this project"
            placeholder="Ask anything about the collected articles…"
            suggestions={[
              "What are the themes across these articles?",
              "What changed most recently?",
              "What should I read first?",
            ]}
          />
        </div>
      ) : (
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
      )}
    </>
  );
}
