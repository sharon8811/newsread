"use client";

import Link from "next/link";
import { notFound } from "next/navigation";
import { useState } from "react";
import { mutate } from "swr";
import { toast } from "sonner";
import Badge from "@/components/ui/Badge";
import ConfirmButton from "@/components/ui/ConfirmButton";
import EmptyState from "@/components/ui/EmptyState";
import { api, ApiError, type AdminUserRow, type InstanceRole } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { humanCount } from "@/lib/format";
import { keys } from "@/lib/keys";
import { useAdminUsers } from "@/lib/queries";
import { useDebouncedValue } from "@/lib/useDebouncedValue";

const PAGE = 25;
const ROLES: InstanceRole[] = ["user", "admin", "owner"];
// The seeded tier keys. "default" clears the manual assignment.
const TIERS = ["default", "free", "paid", "unlimited"];

const SORTS: Array<{ value: string; label: string }> = [
  { value: "-created_at", label: "Newest" },
  { value: "created_at", label: "Oldest" },
  { value: "username", label: "Username" },
  { value: "-last_active", label: "Recently active" },
];

function buildQuery(opts: {
  query: string;
  role: string;
  status: string;
  tier: string;
  sort: string;
  offset: number;
}): string {
  const params = new URLSearchParams();
  if (opts.query) params.set("query", opts.query);
  if (opts.role) params.set("role", opts.role);
  if (opts.status) params.set("status", opts.status);
  if (opts.tier) params.set("tier", opts.tier);
  if (opts.sort !== "-created_at") params.set("sort", opts.sort);
  params.set("limit", String(PAGE));
  if (opts.offset) params.set("offset", String(opts.offset));
  return params.toString();
}

function roleTone(role: InstanceRole): "neutral" | "accent" | "accent-strong" {
  if (role === "owner") return "accent-strong";
  if (role === "admin") return "accent";
  return "neutral";
}

function UserRow({
  row,
  meId,
  meRole,
  onChanged,
}: {
  row: AdminUserRow;
  meId: number;
  meRole: InstanceRole;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const self = row.id === meId;
  const suspended = row.status === "suspended";
  // Status changes on admins/owners are the owner's call (the API enforces
  // it); hiding the control for admins keeps the UI honest.
  const canTouchStatus = !self && (row.role === "user" || meRole === "owner");

  async function run(action: () => Promise<unknown>, okNote: string) {
    setBusy(true);
    try {
      await action();
      toast.success(okNote);
      onChanged();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "The change failed — try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="flex flex-col gap-2.5 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-body font-semibold">{row.name}</span>
        <span className="font-mono-nr text-label" style={{ color: "var(--ink-faint)" }}>
          @{row.username} · {row.email}
        </span>
        <Badge tone={roleTone(row.role)}>{row.role}</Badge>
        {suspended && <Badge tone="neutral">suspended</Badge>}
        <Badge tone="neutral" title={row.tier_assigned ? "manually assigned" : "instance default"}>
          {row.tier_name}
          {row.quota_allowance != null && ` · ${row.quota_used}/${row.quota_allowance}`}
        </Badge>
      </div>
      <p className="font-mono-nr text-label" style={{ color: "var(--ink-faint)" }}>
        joined {row.created_at.slice(0, 10)}
        {row.last_active_day ? ` · active ${row.last_active_day}` : " · never active"} ·{" "}
        {row.subscription_count} feeds · {row.articles_read} read ·{" "}
        {humanCount(row.llm_tokens)} tok ({humanCount(row.llm_tokens_system)} system)
      </p>
      <div className="flex flex-wrap items-center gap-2">
        {meRole === "owner" && !self && (
          <label className="flex items-center gap-1.5 text-label" style={{ color: "var(--ink-faint)" }}>
            Role
            <select
              className="input w-auto"
              style={{ fontSize: 12.5 }}
              aria-label={`Role for ${row.username}`}
              value={row.role}
              disabled={busy}
              onChange={(e) =>
                run(
                  () =>
                    api(`/admin/users/${row.id}/role`, {
                      method: "PATCH",
                      body: { role: e.target.value },
                    }),
                  `${row.username} is now ${e.target.value}`,
                )
              }
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="flex items-center gap-1.5 text-label" style={{ color: "var(--ink-faint)" }}>
          Tier
          <select
            className="input w-auto"
            style={{ fontSize: 12.5 }}
            aria-label={`Tier for ${row.username}`}
            value={row.tier_assigned ? row.tier_key : "default"}
            disabled={busy}
            onChange={(e) =>
              run(
                () =>
                  api(`/admin/users/${row.id}/tier`, {
                    method: "PATCH",
                    body: { tier: e.target.value === "default" ? null : e.target.value },
                  }),
                `${row.username}'s tier updated`,
              )
            }
          >
            {TIERS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        {canTouchStatus &&
          (suspended ? (
            <button
              className="btn"
              style={{ fontSize: 12.5 }}
              disabled={busy}
              onClick={() =>
                run(
                  () =>
                    api(`/admin/users/${row.id}/status`, {
                      method: "PATCH",
                      body: { status: "active" },
                    }),
                  `${row.username} reactivated`,
                )
              }
            >
              Reactivate
            </button>
          ) : (
            <ConfirmButton
              size="sm"
              disabled={busy}
              confirmLabel="Really suspend?"
              onConfirm={() =>
                run(
                  () =>
                    api(`/admin/users/${row.id}/status`, {
                      method: "PATCH",
                      body: { status: "suspended" },
                    }),
                  `${row.username} suspended`,
                )
              }
            >
              Suspend
            </ConfirmButton>
          ))}
      </div>
    </li>
  );
}

export default function AdminUsersPage() {
  const { user: me } = useAuth();
  const [query, setQuery] = useState("");
  const [role, setRole] = useState("");
  const [status, setStatus] = useState("");
  const [tier, setTier] = useState("");
  const [sort, setSort] = useState("-created_at");
  const [offset, setOffset] = useState(0);
  const debounced = useDebouncedValue(query.trim(), 250);

  const qs = buildQuery({ query: debounced, role, status, tier, sort, offset });
  const { data } = useAdminUsers(qs);

  if (me && me.role !== "owner" && me.role !== "admin") notFound();

  function setFilter(setter: (v: string) => void) {
    return (value: string) => {
      setter(value);
      setOffset(0);
    };
  }

  const refresh = () => mutate(keys.adminUsers(qs));

  return (
    <>
      <header
        className="sticky top-0 z-20 border-b px-4 pb-4 pt-4 sm:px-6 sm:pt-5"
        style={{
          background: "var(--bg-header)",
          backdropFilter: "blur(10px)",
          borderColor: "var(--line-soft)",
        }}
      >
        <div className="flex items-center gap-3">
          <h1 className="text-title font-semibold leading-none tracking-tight">Users</h1>
          <Link href="/admin" className="btn ml-1" style={{ fontSize: 12.5 }}>
            Overview
          </Link>
          {data && (
            <span className="font-mono-nr ml-auto text-label" style={{ color: "var(--ink-faint)" }}>
              {data.total} {data.total === 1 ? "account" : "accounts"}
            </span>
          )}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input
            className="input w-[220px]"
            style={{ fontSize: 13 }}
            placeholder="Search email, username, name"
            aria-label="Search users"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setOffset(0);
            }}
          />
          <select
            className="input w-auto"
            style={{ fontSize: 12.5 }}
            aria-label="Filter by role"
            value={role}
            onChange={(e) => setFilter(setRole)(e.target.value)}
          >
            <option value="">Any role</option>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          <select
            className="input w-auto"
            style={{ fontSize: 12.5 }}
            aria-label="Filter by status"
            value={status}
            onChange={(e) => setFilter(setStatus)(e.target.value)}
          >
            <option value="">Any status</option>
            <option value="active">active</option>
            <option value="suspended">suspended</option>
          </select>
          <select
            className="input w-auto"
            style={{ fontSize: 12.5 }}
            aria-label="Filter by tier"
            value={tier}
            onChange={(e) => setFilter(setTier)(e.target.value)}
          >
            <option value="">Any tier</option>
            {TIERS.filter((t) => t !== "default").map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <select
            className="input w-auto"
            style={{ fontSize: 12.5 }}
            aria-label="Sort users"
            value={sort}
            onChange={(e) => setFilter(setSort)(e.target.value)}
          >
            {SORTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
      </header>

      <div className="mx-auto max-w-[980px] px-5 py-6 sm:px-8">
        {!data ? (
          <div className="flex flex-col gap-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-[96px] rounded-lg" style={{ background: "var(--bg-hover)" }} />
            ))}
          </div>
        ) : data.users.length === 0 ? (
          <EmptyState title="No accounts match" subtitle="Loosen the search or filters." />
        ) : (
          <>
            <ul className="flex flex-col divide-y divide-[color:var(--line-soft)]">
              {data.users.map((row) => (
                <UserRow
                  key={row.id}
                  row={row}
                  meId={me?.id ?? 0}
                  meRole={(me?.role ?? "user") as InstanceRole}
                  onChanged={refresh}
                />
              ))}
            </ul>
            {(offset > 0 || offset + PAGE < data.total) && (
              <nav className="mt-5 flex items-center gap-2" aria-label="User pages">
                <button
                  className="btn"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE))}
                >
                  Previous
                </button>
                <span className="font-mono-nr text-label" style={{ color: "var(--ink-faint)" }}>
                  {offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}
                </span>
                <button
                  className="btn"
                  disabled={offset + PAGE >= data.total}
                  onClick={() => setOffset(offset + PAGE)}
                >
                  Next
                </button>
              </nav>
            )}
          </>
        )}
      </div>
    </>
  );
}
