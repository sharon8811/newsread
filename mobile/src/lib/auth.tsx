import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { Platform } from "react-native";

import { api, ApiError, configureApi } from "./api";
import { registerDevicePush, unregisterDevicePush } from "./push";
import { normalizeServerUrl, probeServer } from "./server";
import type { TokenOut, User } from "./types";

const SERVER_KEY = "newsread_server_url";
const TOKEN_KEY = "newsread_token";

// SecureStore is native-only; the web target (dev convenience) falls back to
// AsyncStorage, matching the web app's localStorage behaviour.
const secureStoreAvailable = Platform.OS === "ios" || Platform.OS === "android";

async function getStoredToken(): Promise<string | null> {
  if (secureStoreAvailable) return SecureStore.getItemAsync(TOKEN_KEY);
  return AsyncStorage.getItem(TOKEN_KEY);
}

async function setStoredToken(token: string | null): Promise<void> {
  if (secureStoreAvailable) {
    if (token === null) await SecureStore.deleteItemAsync(TOKEN_KEY);
    else await SecureStore.setItemAsync(TOKEN_KEY, token);
  } else {
    if (token === null) await AsyncStorage.removeItem(TOKEN_KEY);
    else await AsyncStorage.setItem(TOKEN_KEY, token);
  }
}

export type AuthStatus = "loading" | "no-server" | "signed-out" | "signed-in";

export type RegisterFields = {
  email: string;
  username: string;
  name: string;
  password: string;
};

type AuthContextValue = {
  status: AuthStatus;
  serverUrl: string | null;
  user: User | null;
  setServer: (input: string) => Promise<void>;
  changeServer: () => Promise<void>;
  login: (identifier: string, password: string) => Promise<void>;
  register: (fields: RegisterFields) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [serverUrl, setServerUrlState] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    (async () => {
      const [url, token] = await Promise.all([AsyncStorage.getItem(SERVER_KEY), getStoredToken()]);
      configureApi({ serverUrl: url, token });
      setServerUrlState(url);
      if (!url) return setStatus("no-server");
      if (!token) return setStatus("signed-out");
      // Optimistic: the stored token keeps working offline; refresh slides the
      // 30-day expiry and only a definitive 401 signs the user out.
      setStatus("signed-in");
      try {
        const fresh = await api<TokenOut>("/auth/refresh", { method: "POST" });
        await setStoredToken(fresh.access_token);
        configureApi({ token: fresh.access_token });
        setUser(fresh.user);
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          await setStoredToken(null);
          configureApi({ token: null });
          setStatus("signed-out");
        }
      }
    })();
  }, []);

  const value = useMemo<AuthContextValue>(() => {
    const completeSignIn = async (auth: TokenOut) => {
      await setStoredToken(auth.access_token);
      configureApi({ token: auth.access_token });
      setUser(auth.user);
      setStatus("signed-in");
      registerDevicePush().catch(() => {});
    };
    return {
      status,
      serverUrl,
      user,
      setServer: async (input: string) => {
        const url = normalizeServerUrl(input);
        await probeServer(url);
        await AsyncStorage.setItem(SERVER_KEY, url);
        configureApi({ serverUrl: url });
        setServerUrlState(url);
        setStatus("signed-out");
      },
      changeServer: async () => {
        await setStoredToken(null);
        await AsyncStorage.removeItem(SERVER_KEY);
        configureApi({ serverUrl: null, token: null });
        setUser(null);
        setServerUrlState(null);
        setStatus("no-server");
      },
      login: async (identifier: string, password: string) => {
        await completeSignIn(
          await api<TokenOut>("/auth/login", { method: "POST", body: { identifier, password } }),
        );
      },
      register: async (fields: RegisterFields) => {
        await completeSignIn(
          await api<TokenOut>("/auth/register", { method: "POST", body: fields }),
        );
      },
      logout: async () => {
        await unregisterDevicePush().catch(() => {});
        await setStoredToken(null);
        configureApi({ token: null });
        setUser(null);
        setStatus("signed-out");
      },
    };
  }, [status, serverUrl, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
