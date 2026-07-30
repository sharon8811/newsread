import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TranslationSettingsSection from "@/components/TranslationSettingsSection";
import { makeUser } from "./fixtures";
import type { User } from "@/lib/api";

const { authState, updateUserMock, toastError, swrMock } = vi.hoisted(() => ({
  authState: { user: null as User | null },
  updateUserMock: vi.fn(),
  toastError: vi.fn(),
  swrMock: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: authState.user, updateUser: updateUserMock }),
}));
vi.mock("sonner", () => ({ toast: { error: toastError } }));
vi.mock("swr", () => ({ default: swrMock, mutate: vi.fn() }));

const LANGUAGES = [
  { code: "he", name: "Hebrew", native_name: "עברית", rtl: true },
  { code: "fr", name: "French", native_name: "Français", rtl: false },
];

function stub({ translation = true } = {}) {
  swrMock.mockImplementation((key: string | null) => {
    if (key === null) return { data: undefined };
    if (key === "/translation/languages") return { data: LANGUAGES };
    return { data: { configured: true, model: "m", search: false, translation } };
  });
}

function okFetch(user: User) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => user,
    headers: new Headers(),
  });
}

describe("<TranslationSettingsSection>", () => {
  beforeEach(() => {
    swrMock.mockReset();
    updateUserMock.mockClear();
    toastError.mockClear();
    authState.user = makeUser();
    stub();
  });

  it("renders nothing before the user is known", () => {
    authState.user = null;
    const { container } = render(<TranslationSettingsSection />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when no translation model is configured", () => {
    stub({ translation: false });
    const { container } = render(<TranslationSettingsSection />);
    expect(container.firstChild).toBeNull();
  });

  it("offers 'Ask me' until a default is saved", () => {
    render(<TranslationSettingsSection />);
    expect(screen.getByLabelText("Summary language")).toHaveValue("");
  });

  it("shows the saved default", () => {
    authState.user = makeUser({ translation_language: "fr" });
    render(<TranslationSettingsSection />);
    expect(screen.getByLabelText("Summary language")).toHaveValue("fr");
  });

  it("saves a new default language", async () => {
    const saved = makeUser({ translation_language: "he" });
    const fetchMock = okFetch(saved);
    vi.stubGlobal("fetch", fetchMock);
    render(<TranslationSettingsSection />);

    await userEvent.selectOptions(screen.getByLabelText("Summary language"), "he");

    // useMutation passes the mutation's arguments through after the result.
    await waitFor(() => expect(updateUserMock).toHaveBeenCalledWith(saved, "he"));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ translation_language: "he" });
  });

  it("clears the default back to asking", async () => {
    authState.user = makeUser({ translation_language: "he" });
    const fetchMock = okFetch(makeUser());
    vi.stubGlobal("fetch", fetchMock);
    render(<TranslationSettingsSection />);

    await userEvent.selectOptions(screen.getByLabelText("Summary language"), "");

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ translation_language: null });
  });

  it("reports a failed save", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => ({ detail: "nope" }),
        headers: new Headers(),
      }),
    );
    render(<TranslationSettingsSection />);

    await userEvent.selectOptions(screen.getByLabelText("Summary language"), "he");

    await waitFor(() => expect(toastError).toHaveBeenCalled());
  });
});
