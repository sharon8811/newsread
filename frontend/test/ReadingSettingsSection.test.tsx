import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ReadingSettingsSection from "@/components/ReadingSettingsSection";
import { makeUser } from "./fixtures";
import type { User } from "@/lib/api";

const { authState, updateUserMock, toastError } = vi.hoisted(() => ({
  authState: { user: null as User | null },
  updateUserMock: vi.fn(),
  toastError: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: authState.user, updateUser: updateUserMock }),
}));
vi.mock("sonner", () => ({ toast: { error: toastError } }));

function okFetch(user: User) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => user,
    headers: new Headers(),
  });
}

describe("<ReadingSettingsSection>", () => {
  beforeEach(() => {
    updateUserMock.mockClear();
    toastError.mockClear();
    authState.user = makeUser({ assisted_scroll: true });
  });

  it("renders nothing before the user is known", () => {
    authState.user = null;
    const { container } = render(<ReadingSettingsSection />);
    expect(container.firstChild).toBeNull();
  });

  it("reflects the current preference", () => {
    render(<ReadingSettingsSection />);
    expect(screen.getByRole("switch", { name: "Assisted scrolling" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("saves the new value and updates the session optimistically", async () => {
    const saved = makeUser({ assisted_scroll: false });
    const fetchMock = okFetch(saved);
    vi.stubGlobal("fetch", fetchMock);

    render(<ReadingSettingsSection />);
    await userEvent.click(screen.getByRole("switch", { name: "Assisted scrolling" }));

    expect(updateUserMock).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ assisted_scroll: false }),
    );
    await waitFor(() => expect(updateUserMock).toHaveBeenCalledTimes(2));
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/users/me");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ assisted_scroll: false });
  });

  it("rolls back and reports a failed save", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => ({ detail: "boom" }),
        headers: new Headers(),
      }),
    );

    render(<ReadingSettingsSection />);
    await userEvent.click(screen.getByRole("switch", { name: "Assisted scrolling" }));

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(updateUserMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ assisted_scroll: true }),
    );
  });
});
