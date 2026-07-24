import { describe, it, expect, vi, beforeEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SettingsPage from "@/app/(app)/settings/page";
import { makeIntegration, makeShareTarget } from "./fixtures";

const {
  swrMock,
  mutateMock,
  replaceMock,
  router,
  searchParams,
  toastSuccessMock,
  toastErrorMock,
} = vi.hoisted(() => {
  const replaceMock = vi.fn();
  return {
    swrMock: vi.fn(),
    mutateMock: vi.fn(),
    replaceMock,
    // Stable identity, like the real useRouter — a fresh object per render
    // would re-fire the page's [searchParams, router] effect forever.
    router: { replace: replaceMock, push: vi.fn() },
    searchParams: { value: new URLSearchParams() },
    toastSuccessMock: vi.fn(),
    toastErrorMock: vi.fn(),
  };
});
vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));
vi.mock("sonner", () => ({
  toast: { success: toastSuccessMock, error: toastErrorMock },
  Toaster: () => null,
}));
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
  pickerOptions = undefined as unknown[] | Error | undefined,
  serverConfig = {
    allow_signup: true,
    messaging_enabled: true,
    browser_history_enabled: false,
  } as unknown,
  historyConnections = [] as unknown,
  historyConnectionsLoading = false,
  historySettings = { retention_days: 90, sync_revision: 0 } as unknown,
  historySummary = {
    active_connection_count: 0,
    total_connection_count: 0,
    history_count: 0,
    has_active_connection: false,
    has_history: false,
  } as unknown,
  historyRules = [] as unknown[],
  historyExtension = { available: true, version: "0.1.0" } as unknown,
} = {}) {
  // The picker's bound mutate writes back into the holder; the re-render
  // caused by the component's own setState picks the new value up.
  const holder = { options: pickerOptions };
  swrMock.mockImplementation((key: string) => {
    if (key === "/config") return { data: serverConfig };
    if (key === "/integrations") return { data: integrations };
    if (key === "/share-targets") return { data: targets };
    if (key === "/history/connections") {
      return { data: historyConnections, isLoading: historyConnectionsLoading };
    }
    if (key === "/history/settings") return { data: historySettings };
    if (key === "/history/summary") return { data: historySummary };
    if (key === "/history/domain-rules") return { data: historyRules };
    if (key === "/history/extension") return { data: historyExtension };
    if (typeof key === "string" && key.includes("/targets?q=")) {
      if (holder.options instanceof Error) return { error: holder.options };
      return {
        data: holder.options,
        isLoading: false,
        mutate: (updater: unknown) => {
          if (typeof updater === "function") {
            holder.options = (updater as (o: unknown) => unknown[])(holder.options);
          } else {
            holder.options = updater as unknown[];
          }
        },
      };
    }
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

  it("hides the messaging sections when the deployment disables messaging", () => {
    mockSWRData({ serverConfig: { allow_signup: true, messaging_enabled: false } });
    render(<SettingsPage />);
    expect(screen.queryByText("Connections")).not.toBeInTheDocument();
    expect(screen.queryByText("Quick share")).not.toBeInTheDocument();
    // The rest of the settings page still renders.
    expect(screen.getByText("Settings")).toBeInTheDocument();
  });

  it("hides the messaging sections while server flags load", () => {
    // null stands in for SWR's "no data yet" (undefined would hit the
    // destructuring default above and mean flags-loaded-and-enabled).
    mockSWRData({ serverConfig: null });
    render(<SettingsPage />);
    expect(screen.queryByText("Connections")).not.toBeInTheDocument();
  });

  it("renders browser-history controls only when the feature is enabled", () => {
    mockSWRData({
      serverConfig: {
        allow_signup: true,
        messaging_enabled: false,
        browser_history_enabled: true,
      },
    });
    const { rerender } = render(<SettingsPage />);
    expect(screen.getByText("Browser history")).toBeInTheDocument();
    expect(screen.getByLabelText("Browser history retention")).toHaveValue("90");

    mockSWRData({
      serverConfig: {
        allow_signup: true,
        messaging_enabled: false,
        browser_history_enabled: false,
      },
    });
    rerender(<SettingsPage />);
    expect(screen.queryByText("Browser history")).not.toBeInTheDocument();
  });

  it("creates and reveals a one-time browser pairing token", async () => {
    mockSWRData({
      serverConfig: {
        allow_signup: true,
        messaging_enabled: false,
        browser_history_enabled: true,
      },
    });
    const fetchMock = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({
        id: 2,
        name: "Work Chrome",
        token: "nrh_secret_once",
        token_prefix: "nrh_secr",
        created_at: "2026-07-24T10:00:00Z",
        last_seen_at: null,
        revoked_at: null,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SettingsPage />);
    await userEvent.type(screen.getByLabelText("Browser name"), "Work Chrome");
    await userEvent.click(screen.getByRole("button", { name: "Create token" }));

    expect(await screen.findByText("nrh_secret_once")).toBeInTheDocument();
    expect(screen.getByText(/shown once and cannot be recovered/i)).toBeInTheDocument();
    const request = fetchMock.mock.calls[0];
    expect(request[0]).toContain("/history/connections");
    expect(JSON.parse(request[1].body)).toEqual({ name: "Work Chrome" });
    expect(mutateMock).toHaveBeenCalledWith("/history/connections");
    await userEvent.click(screen.getByLabelText("Dismiss pairing token"));
    expect(screen.queryByText("nrh_secret_once")).not.toBeInTheDocument();
  });

  it("revokes a paired browser after confirmation", async () => {
    mockSWRData({
      serverConfig: {
        allow_signup: true,
        messaging_enabled: false,
        browser_history_enabled: true,
      },
      historyConnections: [
        {
          id: 9,
          name: "Home Chrome",
          token_prefix: "nrh_home",
          created_at: "2026-07-20T10:00:00Z",
          last_seen_at: null,
          revoked_at: null,
        },
      ],
    });
    const fetchMock = vi.fn().mockResolvedValue({
      status: 204,
      ok: true,
      json: async () => ({}),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SettingsPage />);
    await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
    await userEvent.click(screen.getByRole("button", { name: "Really revoke?" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][0]).toContain("/history/connections/9");
    expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
  });

  it("updates retention and clears all captured history after confirmation", async () => {
    mockSWRData({
      serverConfig: {
        allow_signup: true,
        messaging_enabled: false,
        browser_history_enabled: true,
      },
      historySummary: {
        active_connection_count: 1,
        total_connection_count: 1,
        history_count: 4,
        has_active_connection: true,
        has_history: true,
      },
    });
    const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) =>
      Promise.resolve({
        status: 200,
        ok: true,
        json: async () =>
          init?.method === "DELETE"
            ? { deleted_count: 4, sync_revision: 3 }
            : { retention_days: 365, sync_revision: 2 },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<SettingsPage />);

    await userEvent.selectOptions(
      screen.getByLabelText("Browser history retention"),
      "365",
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          (call) =>
            call[1]?.method === "PATCH" &&
            JSON.parse(call[1].body).retention_days === 365,
        ),
      ).toBe(true),
    );

    await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
    await userEvent.click(
      screen.getByRole("button", { name: "Really clear all?" }),
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          (call) =>
            call[1]?.method === "DELETE" &&
            JSON.parse(call[1].body).confirm === "DELETE",
        ),
      ).toBe(true),
    );
    expect(mutateMock).toHaveBeenCalledWith("/history/summary");
  });

  it("renders loading, connection health, rules, and forever retention states", () => {
    const enabledConfig = {
      allow_signup: true,
      messaging_enabled: false,
      browser_history_enabled: true,
    };
    mockSWRData({
      serverConfig: enabledConfig,
      historyConnections: null,
      historyConnectionsLoading: true,
      historySettings: null,
      historySummary: null,
    });
    const { rerender } = render(<SettingsPage />);
    expect(
      screen.getByText("Create a one-time token for each browser."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Browser history retention")).toBeDisabled();
    expect(screen.getByLabelText("Browser history retention")).toHaveValue(
      "forever",
    );

    mockSWRData({
      serverConfig: enabledConfig,
      historyConnections: [
        {
          id: 1,
          name: "Synced Chrome",
          token_prefix: "nrh_sync",
          created_at: "2026-07-20T10:00:00Z",
          last_seen_at: "2026-07-24T10:00:00Z",
          revoked_at: null,
        },
        {
          id: 2,
          name: "Old Chrome",
          token_prefix: "nrh_old",
          created_at: "2026-07-18T10:00:00Z",
          last_seen_at: null,
          revoked_at: "2026-07-23T10:00:00Z",
        },
      ],
      historySummary: {
        active_connection_count: 1,
        total_connection_count: 2,
        history_count: 3,
        has_active_connection: true,
        has_history: true,
      },
      historyRules: [
        {
          id: 4,
          hostname: "private.example",
          match_subdomains: true,
          mode: "metadata_only",
          created_at: "2026-07-20T10:00:00Z",
          updated_at: "2026-07-20T10:00:00Z",
        },
        {
          id: 5,
          hostname: "blocked.example",
          match_subdomains: false,
          mode: "exclude",
          created_at: "2026-07-20T10:00:00Z",
          updated_at: "2026-07-20T10:00:00Z",
        },
      ],
    });
    rerender(<SettingsPage />);

    expect(screen.getByRole("link", { name: "Open history" })).toHaveAttribute(
      "href",
      "/history",
    );
    expect(screen.getByText(/Last synced/)).toBeInTheDocument();
    expect(screen.getByText(/Revoked/)).toBeInTheDocument();
    expect(screen.getByText(/private\.example and subdomains/)).toHaveTextContent(
      "metadata only",
    );
    expect(screen.getByText("blocked.example")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Revoke" })).toHaveLength(1);
  });

  it("downloads the packaged extension with its versioned filename", async () => {
    mockSWRData({
      serverConfig: {
        allow_signup: true,
        messaging_enabled: false,
        browser_history_enabled: true,
      },
      historyExtension: { available: true, version: "1.2.3" },
    });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Headers({
        "content-disposition": 'attachment; filename="newsread-history-extension-1.2.3.zip"',
      }),
      blob: async () => new Blob(["zip-bytes"]),
    });
    vi.stubGlobal("fetch", fetchMock);
    const createObjectURL = vi.fn(() => "blob:extension");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL }));
    const clicked: string[] = [];
    const originalClick = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function () {
      clicked.push(this.download);
    };
    try {
      render(<SettingsPage />);
      expect(screen.getByText(/\(v1\.2\.3\)/)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /Download extension/ }));
      await waitFor(() => expect(clicked).toEqual(["newsread-history-extension-1.2.3.zip"]));
      expect(fetchMock.mock.calls[0][0]).toContain("/history/extension/download");
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:extension");
    } finally {
      HTMLAnchorElement.prototype.click = originalClick;
      vi.unstubAllGlobals();
    }
  });

  it("reports a failed extension download and falls back when unavailable", async () => {
    mockSWRData({
      serverConfig: {
        allow_signup: true,
        messaging_enabled: false,
        browser_history_enabled: true,
      },
      historyExtension: { available: true, version: null },
    });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: async () => ({ detail: "Extension package is not available" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<SettingsPage />);
      fireEvent.click(screen.getByRole("button", { name: /Download extension/ }));
      await waitFor(() =>
        expect(toastErrorMock).toHaveBeenCalledWith("Extension package is not available"),
      );
    } finally {
      vi.unstubAllGlobals();
    }

    cleanup();
    mockSWRData({
      serverConfig: {
        allow_signup: true,
        messaging_enabled: false,
        browser_history_enabled: true,
      },
      historyExtension: { available: false, version: null },
    });
    render(<SettingsPage />);
    expect(screen.getByText(/build it from the repository/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Download extension/ })).toBeNull();
  });

  it("surfaces browser-history management failures", async () => {
    mockSWRData({
      serverConfig: {
        allow_signup: true,
        messaging_enabled: false,
        browser_history_enabled: true,
      },
      historyConnections: [
        {
          id: 9,
          name: "Home Chrome",
          token_prefix: "nrh_home",
          created_at: "2026-07-20T10:00:00Z",
          last_seen_at: null,
          revoked_at: null,
        },
      ],
      historyRules: [
        {
          id: 4,
          hostname: "blocked.example",
          match_subdomains: false,
          mode: "exclude",
          created_at: "2026-07-20T10:00:00Z",
          updated_at: "2026-07-20T10:00:00Z",
        },
      ],
    });
    const apiFailure = (detail: string) =>
      Promise.resolve({
        status: 400,
        ok: false,
        statusText: "Bad Request",
        json: async () => ({ detail }),
      });
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => apiFailure("Pairing denied"))
      .mockImplementationOnce(() => apiFailure("Revoke denied"))
      .mockRejectedValueOnce("offline")
      .mockRejectedValueOnce("offline")
      .mockImplementationOnce(() => apiFailure("Clear denied"));
    vi.stubGlobal("fetch", fetchMock);
    render(<SettingsPage />);

    await userEvent.type(screen.getByLabelText("Browser name"), "Work Chrome");
    await userEvent.click(screen.getByRole("button", { name: "Create token" }));
    expect(await screen.findByText("Pairing denied")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
    await userEvent.click(screen.getByRole("button", { name: "Really revoke?" }));
    await waitFor(() =>
      expect(toastErrorMock).toHaveBeenCalledWith("Revoke denied"),
    );

    await userEvent.selectOptions(
      screen.getByLabelText("Browser history retention"),
      "forever",
    );
    await waitFor(() =>
      expect(toastErrorMock).toHaveBeenCalledWith(
        "Could not update retention",
      ),
    );

    await userEvent.click(
      screen.getByLabelText("Remove rule for blocked.example"),
    );
    await waitFor(() =>
      expect(toastErrorMock).toHaveBeenCalledWith("Could not remove the rule"),
    );

    await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
    await userEvent.click(
      screen.getByRole("button", { name: "Really clear all?" }),
    );
    await waitFor(() =>
      expect(toastErrorMock).toHaveBeenCalledWith("Clear denied"),
    );
  });

  it("copies the one-time token and reports clipboard failures", async () => {
    mockSWRData({
      serverConfig: {
        allow_signup: true,
        messaging_enabled: false,
        browser_history_enabled: true,
      },
    });
    const fetchMock = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({
        id: 2,
        name: "Work Chrome",
        token: "nrh_copy_once",
        token_prefix: "nrh_copy",
        created_at: "2026-07-24T10:00:00Z",
        last_seen_at: null,
        revoked_at: null,
      }),
    });
    const writeText = vi
      .fn()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error("denied"));
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SettingsPage />);
    await userEvent.type(screen.getByLabelText("Browser name"), "Work Chrome");
    await userEvent.click(screen.getByRole("button", { name: "Create token" }));

    const copyButton = await screen.findByRole("button", { name: "Copy" });
    await userEvent.click(copyButton);
    await waitFor(() =>
      expect(toastSuccessMock).toHaveBeenCalledWith("Pairing token copied"),
    );
    await userEvent.click(copyButton);
    await waitFor(() =>
      expect(toastErrorMock).toHaveBeenCalledWith(
        "Could not copy. Select the token and copy it manually.",
      ),
    );
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
    const options = [
      { external_id: "C1", display_name: "#general", target_type: "channel", meta: {}, saved_id: null },
      { external_id: "C2", display_name: "#random", target_type: "channel", meta: {}, saved_id: 9 },
    ];
    mockSWRData({ pickerOptions: options });
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
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

  it("searches targets with the debounced typed query", async () => {
    mockSWRData({ pickerOptions: [] });
    render(<SettingsPage />);

    await userEvent.click(screen.getByRole("button", { name: "Browse Slack" }));
    expect(await screen.findByText("Nothing matched.")).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText(/Search Slack/), "ai");
    await waitFor(() =>
      expect(
        swrMock.mock.calls.some((c) => String(c[0]).includes("targets?q=ai")),
      ).toBe(true),
    );
  });

  it("shows a picker error when the platform listing fails", async () => {
    mockSWRData({ pickerOptions: new Error("token revoked") });
    render(<SettingsPage />);
    await userEvent.click(screen.getByRole("button", { name: "Browse Slack" }));
    expect(await screen.findByText("token revoked")).toBeInTheDocument();
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
