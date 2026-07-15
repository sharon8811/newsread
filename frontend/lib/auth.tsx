"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { api, ApiError, setToken, getToken, type User } from "./api";
import { clearReadingSessions } from "./readingSession";

type AuthState = {
  user: User | null;
  ready: boolean;
  authed: boolean;
  login: (identifier: string, password: string) => Promise<void>;
  register: (data: {
    email: string;
    username: string;
    name: string;
    password: string;
  }) => Promise<void>;
  logout: () => void;
  updateUser: (user: User) => void;
};

const AuthContext = createContext<AuthState | null>(null);

type TokenResponse = { access_token: string; user: User };

type AuthStatus = "checking" | "authed" | "anon";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<AuthStatus>("checking");

  useEffect(() => {
    if (!getToken()) {
      setStatus("anon");
      return;
    }
    // A stored token means authed for rendering purposes — pages mount and
    // their data fetches start in parallel with /auth/me instead of behind it.
    // Only a 401 demotes to anon; transient failures keep the session alive
    // (user stays null and the global SWR 401 handler covers a revoked token).
    setStatus("authed");
    api<User>("/auth/me")
      .then(setUser)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          setToken(null);
          setStatus("anon");
        }
      });
  }, []);

  const login = useCallback(async (identifier: string, password: string) => {
    clearReadingSessions();
    const res = await api<TokenResponse>("/auth/login", {
      method: "POST",
      body: { identifier, password },
    });
    setToken(res.access_token);
    setUser(res.user);
    setStatus("authed");
  }, []);

  const register = useCallback(
    async (data: {
      email: string;
      username: string;
      name: string;
      password: string;
    }) => {
      clearReadingSessions();
      const res = await api<TokenResponse>("/auth/register", {
        method: "POST",
        body: data,
      });
      setToken(res.access_token);
      setUser(res.user);
      setStatus("authed");
    },
    [],
  );

  const logout = useCallback(() => {
    clearReadingSessions();
    setToken(null);
    setUser(null);
    setStatus("anon");
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        ready: status !== "checking",
        authed: status === "authed",
        login,
        register,
        logout,
        updateUser: setUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
