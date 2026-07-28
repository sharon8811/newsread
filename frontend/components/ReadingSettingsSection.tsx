"use client";

import { api, type User } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useMutation } from "@/lib/useMutation";
import Toggle from "./ui/Toggle";

/** Settings block for how the reading views behave. Assisted scrolling is on
 * by default, so the toggle is the escape hatch for people who want the plain
 * free-scrolling list back. */
export default function ReadingSettingsSection() {
  const { user, updateUser } = useAuth();
  if (!user) return null;
  return (
    <section className="mt-9">
      <p className="mono-label">Reading</p>
      <AssistedScrollRow user={user} updateUser={updateUser} />
    </section>
  );
}

function AssistedScrollRow({
  user,
  updateUser,
}: {
  user: User;
  updateUser: (user: User) => void;
}) {
  // Optimistic, because the session's user object is also the live setting for
  // any list already on screen; a failed save puts it straight back.
  const save = useMutation(
    async (value: boolean) => {
      updateUser({ ...user, assisted_scroll: value });
      try {
        return await api<User>("/users/me", {
          method: "PATCH",
          body: { assisted_scroll: value },
        });
      } catch (err) {
        updateUser({ ...user, assisted_scroll: !value });
        throw err;
      }
    },
    {
      onSuccess: updateUser,
      surface: "toast",
      fallbackError: "Could not save the setting",
    },
  );

  return (
    <div
      className="mt-3.5 flex items-center gap-3.5 rounded-lg border p-4"
      style={{ background: "var(--bg-raised)", borderColor: "var(--line)" }}
    >
      <div className="min-w-0 flex-1 leading-tight">
        <p className="text-body-lg font-medium">Assisted scrolling</p>
        <p className="mt-0.5 text-body-sm" style={{ color: "var(--ink-faint)" }}>
          In cards view, one scroll or swipe moves to the next article and lines it up
          under the header. Turn this off for ordinary free scrolling.
        </p>
      </div>
      <Toggle
        checked={user.assisted_scroll}
        onChange={save.run}
        disabled={save.busy}
        label="Assisted scrolling"
      />
    </div>
  );
}
