import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import useSWR from "swr";
import { SWRProvider } from "@/lib/swr";
import { setToken } from "@/lib/api";

const { replaceMock, logoutMock } = vi.hoisted(() => ({
  replaceMock: vi.fn(),
  logoutMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
}));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ logout: logoutMock }),
}));

function statusFetch(status: number, body: unknown = { detail: "nope" }) {
  return vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
  });
}

// Unique key per test: SWR's global cache would otherwise dedupe across tests.
function Consumer({ swrKey }: { swrKey: string }) {
  const { data, error } = useSWR<{ ok: boolean }>(swrKey);
  if (error) return <p>error state</p>;
  return <p>{data ? "loaded" : "loading"}</p>;
}

describe("SWRProvider", () => {
  beforeEach(() => {
    logoutMock.mockClear();
    replaceMock.mockClear();
  });

  it("provides the shared fetcher as the global default", async () => {
    setToken("tok");
    const fetchMock = statusFetch(200, { ok: true });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <SWRProvider>
        <Consumer swrKey="/provider-ok" />
      </SWRProvider>,
    );

    await screen.findByText("loaded");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/provider-ok"),
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer tok" }),
      }),
    );
  });

  it("logs out and redirects to /login when a request 401s with a token", async () => {
    setToken("expired");
    vi.stubGlobal("fetch", statusFetch(401));

    render(
      <SWRProvider>
        <Consumer swrKey="/provider-401" />
      </SWRProvider>,
    );

    await waitFor(() => expect(logoutMock).toHaveBeenCalledTimes(1));
    expect(replaceMock).toHaveBeenCalledWith("/login");
  });

  it("leaves non-401 errors to the caller", async () => {
    setToken("tok");
    vi.stubGlobal("fetch", statusFetch(500));

    render(
      <SWRProvider>
        <Consumer swrKey="/provider-500" />
      </SWRProvider>,
    );

    await screen.findByText("error state");
    expect(logoutMock).not.toHaveBeenCalled();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("does not logout on 401 when no token is present", async () => {
    setToken(null);
    vi.stubGlobal("fetch", statusFetch(401));

    render(
      <SWRProvider>
        <Consumer swrKey="/provider-401-anon" />
      </SWRProvider>,
    );

    await screen.findByText("error state");
    expect(logoutMock).not.toHaveBeenCalled();
  });
});
