import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import React from "react";
import { AuthProvider, useAuth } from "@/lib/auth";
import { setToken, getToken } from "@/lib/api";

function okResponse(body: unknown, status = 200) {
  return {
    status,
    ok: status >= 200 && status < 300,
    statusText: "x",
    json: async () => body,
  } as Response;
}

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <AuthProvider>{children}</AuthProvider>
);

const USER = { id: 1, email: "a@b.c", username: "alice", name: "Alice", default_view: "list" };

describe("useAuth", () => {
  beforeEach(() => setToken(null));

  it("throws when used outside a provider", () => {
    expect(() => renderHook(() => useAuth())).toThrow(
      "useAuth must be used inside AuthProvider",
    );
  });

  it("is ready with no user when there is no token", async () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.user).toBeNull();
  });

  it("loads the current user when a token exists", async () => {
    setToken("tok");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(okResponse(USER)));
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.user?.username).toBe("alice");
  });

  it("clears the token when /auth/me fails", async () => {
    setToken("bad");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(okResponse({ detail: "nope" }, 401)));
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.user).toBeNull();
    expect(getToken()).toBeNull();
  });

  it("logs in and stores token + user", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(okResponse({ access_token: "newtok", user: USER })),
    );
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.ready).toBe(true));
    await act(async () => {
      await result.current.login("alice", "password");
    });
    expect(result.current.user?.username).toBe("alice");
    expect(getToken()).toBe("newtok");
  });

  it("registers and stores token + user", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(okResponse({ access_token: "regtok", user: USER })),
    );
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.ready).toBe(true));
    await act(async () => {
      await result.current.register({
        email: "a@b.c",
        username: "alice",
        name: "Alice",
        password: "password",
      });
    });
    expect(getToken()).toBe("regtok");
    expect(result.current.user?.name).toBe("Alice");
  });

  it("logs out and clears everything", async () => {
    setToken("tok");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(okResponse(USER)));
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.user).not.toBeNull());
    act(() => result.current.logout());
    expect(result.current.user).toBeNull();
    expect(getToken()).toBeNull();
  });

  it("updateUser replaces the user", async () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.ready).toBe(true));
    act(() => result.current.updateUser({ ...USER, default_view: "cards" }));
    expect(result.current.user?.default_view).toBe("cards");
  });
});
