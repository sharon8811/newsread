import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SettingsPage from "@/app/(app)/settings/page";
import { makeIntegration, makeShareTarget } from "./fixtures";

const { swrMock, mutateMock, replaceMock, router, searchParams } = vi.hoisted(() => {
  const replaceMock = vi.fn();
  return {
    swrMock: vi.fn(),
    mutateMock: vi.fn(),
    replaceMock,
    // Stable identity, like the real useRouter — a fresh object per render
    // would re-fire the page's [searchParams, router] effect forever.
    router: { replace: replaceMock, push: vi.fn() },
    searchParams: { value: new URLSearchParams() },
  };
});
vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useSearchParams: () => searchParams.value,
}));

function mockSWRData({
  integrations = [
    makeIntegration({
      platform: "slack",
      connected: true,
      status: "active",
      workspace_name: "Acme",
      account_name: "sharon",
    }),
    makeIntegration({ platform: "teams" }),
  ],
  targets = [makeShareTarget({ id: 5, display_name: "#ai-news" })],
} = {}) {
  swrMock.mockImplementation((key: string) => {
    if (key === "/integrations") return { data: integrations };
    if (key === "/share-targets") return { data: targets };
    return { data: undefined };
  });
}

describe("SettingsPage", () => {
  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
    replaceMock.mockClear();
    searchParams.value = new URLSearchParams();
  });

  it("renders connection cards with their state", () => {
    mockSWRData();
    render(<SettingsPage />);
    expect(screen.getByText("Slack")).toBeInTheDocument();
    expect(screen.getByText(/Connected to Acme as sharon/)).toBeInTheDocument();
    expect(screen.getByText("Microsoft Teams")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeInTheDocument();
  });

  it("shows unconfigured platforms without a connect button", () => {
    mockSWRData({
      integrations: [
        makeIntegration({ platform: "slack", configured: false }),
        makeIntegration({ platform: "teams", configured: false }),
      ],
      targets: [],
    });
    render(<SettingsPage />);
    expect(screen.getAllByText("Not configured on the server")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Connect" })).not.toBeInTheDocument();
  });

  it("offers reconnect when a connection is broken", () => {
    mockSWRData({
      integrations: [
        makeIntegration({ platform: "slack", connected: true, status: "error" }),
        makeIntegration({ platform: "teams" }),
      ],
    });
    render(<SettingsPage />);
    expect(screen.getByText(/Connection broken/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reconnect" })).toBeInTheDocument();
  });

  it("shows a success banner after the OAuth callback and clears the query", async () => {
    mockSWRData();
    searchParams.value = new URLSearchParams("connected=slack");
    render(<SettingsPage />);
    expect(await screen.findByText("Slack connected.")).toBeInTheDocument();
    expect(mutateMock).toHaveBeenCalledWith("/integrations");
    expect(replaceMock).toHaveBeenCalledWith("/settings");
  });

  it("shows an error banner when the callback failed", async () => {
    mockSWRData();
    searchParams.value = new URLSearchParams("error=slack:access_denied");
    render(<SettingsPage />);
    expect(
      await screen.findByText(/Connection failed \(slack:access_denied\)/),
    ).toBeInTheDocument();
  });

  it("lists saved quick-share targets and removes one", async () => {
    mockSWRData();
    const fetchMock = vi.fn().mockResolvedValue({
      status: 204,
      ok: true,
      json: async () => ({}),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SettingsPage />);
    expect(screen.getByText("#ai-news")).toBeInTheDocument();

    await userEvent.click(screen.getByTitle("Remove"));
    await waitFor(() => expect(mutateMock).toHaveBeenCalledWith("/share-targets"));
    expect(fetchMock.mock.calls[0][0]).toContain("/share-targets/5");
    expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
    vi.unstubAllGlobals();
  });

  it("starts the OAuth flow from the connect button", async () => {
    mockSWRData();
    const fetchMock = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({ url: "https://login.example/authorize" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const original = window.location;
    Object.defineProperty(window, "location", {
      writable: true,
      value: { ...original, href: "http://front.test/settings" },
    });

    render(<SettingsPage />);
    await userEvent.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() =>
      expect(window.location.href).toBe("https://login.example/authorize"),
    );
    expect(fetchMock.mock.calls[0][0]).toContain("/integrations/teams/authorize");

    Object.defineProperty(window, "location", { writable: true, value: original });
    vi.unstubAllGlobals();
  });

  it("disconnects after confirmation and refreshes both lists", async () => {
    mockSWRData();
    const fetchMock = vi.fn().mockResolvedValue({ status: 204, ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    render(<SettingsPage />);

    await userEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    await userEvent.click(screen.getByRole("button", { name: "Really disconnect?" }));
    await waitFor(() => expect(mutateMock).toHaveBeenCalledWith("/integrations"));
    expect(mutateMock).toHaveBeenCalledWith("/share-targets");
    expect(fetchMock.mock.calls[0][0]).toContain("/integrations/slack");
    expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
    vi.unstubAllGlobals();
  });

  it("does nothing until the armed disconnect is confirmed", async () => {
    mockSWRData();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<SettingsPage />);

    // First click only arms the button; no request goes out.
    await userEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(screen.getByRole("button", { name: "Really disconnect?" })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("shows the error when starting the OAuth flow fails", async () => {
    mockSWRData();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 503,
        ok: false,
        json: async () => ({ detail: "slack credentials are not configured" }),
      }),
    );
    render(<SettingsPage />);
    await userEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(
      await screen.findByText("slack credentials are not configured"),
    ).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("dismisses the banner", async () => {
    mockSWRData();
    searchParams.value = new URLSearchParams("connected=slack");
    render(<SettingsPage />);
    const banner = await screen.findByText("Slack connected.");
    await userEvent.click(banner.parentElement!.querySelector("button")!);
    expect(screen.queryByText("Slack connected.")).not.toBeInTheDocument();
  });

  it("browses a platform, saves and unsaves targets", async () => {
    mockSWRData();
    const options = [
      { external_id: "C1", display_name: "#general", target_type: "channel", meta: {}, saved_id: null },
      { external_id: "C2", display_name: "#random", target_type: "channel", meta: {}, saved_id: 9 },
    ];
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/integrations/slack/targets"))
        return Promise.resolve({ status: 200, ok: true, json: async () => options });
      if (init?.method === "POST")
        return Promise.resolve({ status: 201, ok: true, json: async () => ({ id: 31 }) });
      return Promise.resolve({ status: 204, ok: true, json: async () => ({}) });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SettingsPage />);

    // Only the active (slack) connection is browsable.
    await userEvent.click(screen.getByRole("button", { name: "Browse Slack" }));
    expect(await screen.findByText("#general")).toBeInTheDocument();

    // Save the unsaved one…
    await userEvent.click(screen.getByTitle("Add to quick share"));
    await waitFor(() => expect(mutateMock).toHaveBeenCalledWith("/share-targets"));
    const post = fetchMock.mock.calls.find((c) => c[1]?.method === "POST");
    expect(JSON.parse(post![1].body)).toMatchObject({ platform: "slack", external_id: "C1" });
    // …its button flips to saved (joining the already-saved #random).
    expect(await screen.findAllByTitle("Remove from quick share")).toHaveLength(2);

    // Unsave the already-saved one.
    mutateMock.mockClear();
    const removeButtons = screen.getAllByTitle("Remove from quick share");
    await userEvent.click(removeButtons[removeButtons.length - 1]);
    await waitFor(() => expect(mutateMock).toHaveBeenCalledWith("/share-targets"));
    const del = fetchMock.mock.calls.find(
      (c) => c[1]?.method === "DELETE" && String(c[0]).includes("/share-targets/9"),
    );
    expect(del).toBeTruthy();

    // The browse button now closes the picker.
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByPlaceholderText(/Search Slack/)).not.toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("searches targets with the typed query", async () => {
    mockSWRData();
    const fetchMock = vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => [] });
    vi.stubGlobal("fetch", fetchMock);
    render(<SettingsPage />);

    await userEvent.click(screen.getByRole("button", { name: "Browse Slack" }));
    expect(await screen.findByText("Nothing matched.")).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText(/Search Slack/), "ai");
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((c) => String(c[0]).includes("targets?q=ai")),
      ).toBe(true),
    );
    vi.unstubAllGlobals();
  });

  it("shows a picker error when the platform listing fails", async () => {
    mockSWRData();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 502,
        ok: false,
        json: async () => ({ detail: { message: "token revoked", reconnect: true } }),
      }),
    );
    render(<SettingsPage />);
    await userEvent.click(screen.getByRole("button", { name: "Browse Slack" }));
    expect(await screen.findByText("token revoked")).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("prompts to connect before saving targets when nothing is connected", () => {
    mockSWRData({
      integrations: [
        makeIntegration({ platform: "slack" }),
        makeIntegration({ platform: "teams" }),
      ],
      targets: [],
    });
    render(<SettingsPage />);
    expect(
      screen.getByText(/Connect a platform above to start saving quick-share targets/),
    ).toBeInTheDocument();
  });
});
