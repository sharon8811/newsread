"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { api, setToken, getToken, type User } from "./api";
import { clearReadingSessions } from "./readingSession";

type AuthState = {
  user: User | null;
  ready: boolean;
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

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      setReady(true);
      return;
    }
    api<User>("/auth/me")
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setReady(true));
  }, []);

  const login = useCallback(async (identifier: string, password: string) => {
    clearReadingSessions();
    const res = await api<TokenResponse>("/auth/login", {
      method: "POST",
      body: { identifier, password },
    });
    setToken(res.access_token);
    setUser(res.user);
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
    },
    [],
  );

  const logout = useCallback(() => {
    clearReadingSessions();
    setToken(null);
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{ user, ready, login, register, logout, updateUser: setUser }}
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
