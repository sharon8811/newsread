import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RegisterPage from "@/app/register/page";
import { makeUser } from "./fixtures";

const { pushMock, replaceMock, authState } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  replaceMock: vi.fn(),
  authState: { user: null as unknown, ready: true, authed: false, register: vi.fn() },
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock }),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => authState }));
vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

describe("RegisterPage", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    authState.user = null;
    authState.ready = true;
    authState.authed = false;
    authState.register = vi.fn().mockResolvedValue(undefined);
  });

  function fields(container: HTMLElement) {
    const i = container.querySelectorAll("input");
    return { name: i[0], username: i[1], email: i[2], password: i[3] };
  }

  it("renders all fields", () => {
    render(<RegisterPage />);
    expect(screen.getByText("Name")).toBeInTheDocument();
    expect(screen.getByText("Username")).toBeInTheDocument();
    expect(screen.getByText("Email")).toBeInTheDocument();
    expect(screen.getByText("Password")).toBeInTheDocument();
  });

  it("registers and redirects on submit", async () => {
    const { container } = render(<RegisterPage />);
    const f = fields(container);
    await userEvent.type(f.name, " Alice ");
    await userEvent.type(f.username, "alice");
    await userEvent.type(f.email, "a@b.c");
    await userEvent.type(f.password, "password123");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() =>
      expect(authState.register).toHaveBeenCalledWith({
        name: "Alice",
        username: "alice",
        email: "a@b.c",
        password: "password123",
      }),
    );
    expect(pushMock).toHaveBeenCalledWith("/");
  });

  it("shows an error when registration fails", async () => {
    authState.register = vi.fn().mockRejectedValue(new Error("username taken"));
    const { container } = render(<RegisterPage />);
    const f = fields(container);
    await userEvent.type(f.name, "Al");
    await userEvent.type(f.username, "alice");
    await userEvent.type(f.email, "a@b.c");
    await userEvent.type(f.password, "password123");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(screen.getByText("username taken")).toBeInTheDocument());
  });

  it("shows a generic error for non-Error rejections", async () => {
    authState.register = vi.fn().mockRejectedValue("x");
    const { container } = render(<RegisterPage />);
    const f = fields(container);
    await userEvent.type(f.name, "Al");
    await userEvent.type(f.username, "alice");
    await userEvent.type(f.email, "a@b.c");
    await userEvent.type(f.password, "password123");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() =>
      expect(screen.getByText("Could not create account")).toBeInTheDocument(),
    );
  });

  it("redirects when already authenticated", async () => {
    authState.user = makeUser();
    authState.authed = true;
    render(<RegisterPage />);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/"));
  });
});
