import type { ServerInfo } from "./types";

/** Turn whatever the user typed into a canonical origin (+ optional base path):
 * add https:// when the scheme is missing, drop trailing slashes and a
 * trailing /api (people paste API URLs), keep everything else. */
export function normalizeServerUrl(input: string): string {
  let raw = input.trim();
  if (!raw) throw new Error("Enter your server address");
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) raw = `https://${raw}`;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("That doesn't look like a valid URL");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("The address must start with http:// or https://");
  }
  let path = url.pathname.replace(/\/+$/, "");
  if (path.toLowerCase().endsWith("/api")) path = path.slice(0, -4);
  return `${url.protocol}//${url.host}${path}`;
}

/** Confirm the URL points at a NewsRead server before asking for credentials. */
export async function probeServer(serverUrl: string, timeoutMs = 8000): Promise<ServerInfo> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${serverUrl}/api/health`, { signal: controller.signal });
  } catch {
    throw new Error("Could not reach the server. Check the address and your connection.");
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) throw new Error(`The server responded with an error (HTTP ${res.status})`);
  const info = (await res.json().catch(() => null)) as ServerInfo | null;
  if (info?.app !== "newsread") {
    throw new Error("That URL responds, but it doesn't look like a NewsRead server.");
  }
  return info;
}
