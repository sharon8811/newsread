import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LoginPage from "@/app/login/page";
import { makeUser } from "./fixtures";

const { pushMock, replaceMock, authState } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  replaceMock: vi.fn(),
  authState: { user: null as unknown, ready: true, authed: false, login: vi.fn() },
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

describe("LoginPage", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    authState.user = null;
    authState.ready = true;
    authState.authed = false;
    authState.login = vi.fn().mockResolvedValue(undefined);
  });

  it("renders the sign-in form", () => {
    render(<LoginPage />);
    expect(screen.getByText("Sign in")).toBeInTheDocument();
    expect(screen.getByText("Email or username")).toBeInTheDocument();
  });

  function fields(container: HTMLElement) {
    const inputs = container.querySelectorAll("input");
    return { identifier: inputs[0], password: inputs[1] };
  }

  it("logs in and redirects on submit", async () => {
    const { container } = render(<LoginPage />);
    const { identifier, password } = fields(container);
    await userEvent.type(identifier, "  alice  ");
    await userEvent.type(password, "secret12");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(authState.login).toHaveBeenCalledWith("alice", "secret12"));
    expect(pushMock).toHaveBeenCalledWith("/");
  });

  it("shows an error when login fails", async () => {
    authState.login = vi.fn().mockRejectedValue(new Error("Invalid credentials"));
    const { container } = render(<LoginPage />);
    const { identifier, password } = fields(container);
    await userEvent.type(identifier, "alice");
    await userEvent.type(password, "wrongpass");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByText("Invalid credentials")).toBeInTheDocument());
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("shows a generic error for non-Error rejections", async () => {
    authState.login = vi.fn().mockRejectedValue("boom");
    const { container } = render(<LoginPage />);
    const { identifier, password } = fields(container);
    await userEvent.type(identifier, "a");
    await userEvent.type(password, "b");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByText("Could not sign in")).toBeInTheDocument());
  });

  it("redirects to home when already authenticated", async () => {
    authState.user = makeUser();
    authState.authed = true;
    render(<LoginPage />);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/"));
  });
});
