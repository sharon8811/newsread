import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ShareModal from "@/components/ShareModal";
import type { ShareTarget, TargetOption } from "@/lib/api";
import { makeArticle, makeIntegration, makeShareTarget } from "./fixtures";

const { swrMock, mutateMock } = vi.hoisted(() => ({
  swrMock: vi.fn(),
  mutateMock: vi.fn(),
}));
vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));

const slackTarget = makeShareTarget({ id: 11, display_name: "#ai-news" });
const teamsTarget = makeShareTarget({
  id: 12,
  platform: "teams",
  external_id: "ch1",
  display_name: "Eng › General",
});

function mockSWRData({
  targets = [slackTarget, teamsTarget],
  ai = false,
  connected = true,
  slackOptions,
}: {
  targets?: ShareTarget[];
  ai?: boolean;
  connected?: boolean;
  slackOptions?: TargetOption[];
} = {}) {
  swrMock.mockImplementation((key: string) => {
    if (key === "/share-targets") return { data: targets };
    if (key === "/integrations") {
      return {
        data: [
          makeIntegration({ connected, status: connected ? "active" : null }),
          makeIntegration({
            platform: "teams",
            connected,
            status: connected ? "active" : null,
          }),
        ],
      };
    }
    if (key?.startsWith("/integrations/slack/targets")) {
      return {
        data:
          slackOptions ??
          targets
            .filter((target) => target.platform === "slack")
            .map((target) => ({
              external_id: target.external_id,
              display_name: target.display_name,
              target_type: target.target_type,
              meta: target.meta,
              saved_id: target.id,
            })),
        isLoading: false,
      };
    }
    if (key?.startsWith("/integrations/teams/targets")) {
      return {
        data: targets
          .filter((target) => target.platform === "teams")
          .map((target) => ({
            external_id: target.external_id,
            display_name: target.display_name,
            target_type: target.target_type,
            meta: target.meta,
            saved_id: target.id,
          })),
        isLoading: false,
      };
    }
    if (key === "/ai/status")
      return { data: { configured: ai, model: null, search: false, search_provider: null } };
    return { data: undefined };
  });
}

// fetch that records external-share and AI calls
function makeFetch(opts: { externalStatus?: number; externalDetail?: unknown } = {}) {
  const calls: { url: string; body: unknown }[] = [];
  const fn = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ url, body });
    if (url.includes("/shares/external") && opts.externalStatus) {
      return Promise.resolve({
        status: opts.externalStatus,
        ok: false,
        json: async () => ({ detail: opts.externalDetail ?? "boom" }),
      });
    }
    if (url.includes("/ai/share-message")) {
      return Promise.resolve({
        status: 200,
        ok: true,
        json: async () => ({ message: "A crisp AI note." }),
      });
    }
    return Promise.resolve({ status: 201, ok: true, json: async () => ({ id: 1 }) });
  });
  return { fn, calls };
}

describe("<ShareModal> external targets", () => {
  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
  });

  afterEach(() => {
    Reflect.deleteProperty(navigator, "share");
    Reflect.deleteProperty(navigator, "clipboard");
    vi.unstubAllGlobals();
  });

  it("shows saved Slack and Teams destinations in the default dropdown", async () => {
    mockSWRData();
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("combobox", { name: /Share to/ }));
    expect(await screen.findByText("ai-news")).toBeInTheDocument();
    expect(screen.queryByText("#ai-news")).not.toBeInTheDocument();
    expect(screen.getByText("Eng › General")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Slack" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Microsoft Teams" })).toBeInTheDocument();
    expect(screen.getByText("WhatsApp")).toBeInTheDocument();
  });

  it("sends to a selected target with the note as message", async () => {
    mockSWRData();
    const { fn, calls } = makeFetch();
    vi.stubGlobal("fetch", fn);
    const onClose = vi.fn();
    render(<ShareModal article={makeArticle({ id: 7 })} onClose={onClose} />);

    await userEvent.click(screen.getByRole("combobox", { name: /Share to/ }));
    await userEvent.click(await screen.findByText("ai-news"));
    await userEvent.type(
      screen.getByLabelText(/Message/),
      "worth a read",
    );
    await userEvent.click(screen.getByRole("button", { name: /Send/ }));

    await screen.findByText("Shared.");
    const external = calls.filter((c) => c.url.includes("/shares/external"));
    expect(external).toHaveLength(1);
    expect(external[0].body).toEqual({
      article_id: 7,
      message: "worth a read",
      target_id: 11,
    });
    vi.unstubAllGlobals();
  });

  it("sends a live autocomplete result without requiring it to be saved first", async () => {
    mockSWRData({
      targets: [],
      slackOptions: [
        {
          external_id: "C99",
          display_name: "#launch-room",
          target_type: "channel",
          meta: { team_id: "T1" },
          saved_id: null,
        },
      ],
    });
    const { fn, calls } = makeFetch();
    vi.stubGlobal("fetch", fn);
    render(<ShareModal article={makeArticle({ id: 8 })} onClose={vi.fn()} />);

    await userEvent.click(screen.getByRole("combobox", { name: /Share to/ }));
    await userEvent.click(await screen.findByText("launch-room"));
    await userEvent.click(screen.getByRole("button", { name: /Send/ }));

    await screen.findByText("Shared.");
    const external = calls.find((call) => call.url.includes("/shares/external"));
    expect(external?.body).toEqual({
      article_id: 8,
      message: "",
      target: {
        platform: "slack",
        external_id: "C99",
        display_name: "#launch-room",
        target_type: "channel",
        meta: { team_id: "T1" },
      },
    });
  });

  it("keeps the modal open and shows the target name when a send fails", async () => {
    mockSWRData();
    const { fn } = makeFetch({
      externalStatus: 502,
      externalDetail: { message: "not a member", reconnect: false },
    });
    vi.stubGlobal("fetch", fn);
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);

    await userEvent.click(screen.getByRole("combobox", { name: /Share to/ }));
    await userEvent.click(await screen.findByText("ai-news"));
    await userEvent.click(screen.getByRole("button", { name: /Send/ }));

    expect(await screen.findByText(/#ai-news: not a member/)).toBeInTheDocument();
    expect(screen.queryByText("Shared.")).not.toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("opens WhatsApp with the prefilled message and url", async () => {
    mockSWRData({ targets: [] });
    const { fn } = makeFetch();
    vi.stubGlobal("fetch", fn);
    const open = vi.fn();
    vi.stubGlobal("open", open);
    render(
      <ShareModal
        article={makeArticle({ url: "https://a.example/x" })}
        onClose={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByText("WhatsApp"));
    await userEvent.type(
      screen.getByLabelText(/Message/),
      "look at this",
    );
    await userEvent.click(screen.getByRole("button", { name: /Send/ }));

    await screen.findByText("Shared.");
    expect(open).toHaveBeenCalledWith(
      `https://wa.me/?text=${encodeURIComponent("look at this\nhttps://a.example/x")}`,
      "_blank",
    );
    vi.unstubAllGlobals();
  });

  it("opens the native app picker with the title, note, and url", async () => {
    mockSWRData({ targets: [] });
    const share = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "share", {
      configurable: true,
      value: share,
    });
    render(
      <ShareModal
        article={
          makeArticle({
            title: "A useful story",
            url: "https://a.example/native",
          })
        }
        onClose={vi.fn()}
      />,
    );

    await userEvent.type(
      screen.getByLabelText(/Message/),
      "Worth your time",
    );
    await userEvent.click(screen.getByRole("button", { name: "Share to app" }));

    expect(share).toHaveBeenCalledWith({
      title: "A useful story",
      text: "Worth your time",
      url: "https://a.example/native",
    });
    expect(await screen.findByText("Shared.")).toBeInTheDocument();
  });

  it("omits text from the native payload when the note is blank", async () => {
    mockSWRData({ targets: [] });
    const share = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "share", {
      configurable: true,
      value: share,
    });
    render(
      <ShareModal
        article={
          makeArticle({
            title: "A useful story",
            url: "https://a.example/native",
          })
        }
        onClose={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Share to app" }));

    expect(share).toHaveBeenCalledWith({
      title: "A useful story",
      url: "https://a.example/native",
    });
    expect(await screen.findByText("Shared.")).toBeInTheDocument();
  });

  it("shows an opening state while the native picker is pending", async () => {
    mockSWRData({ targets: [] });
    let resolveShare!: () => void;
    const share = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveShare = resolve;
        }),
    );
    Object.defineProperty(navigator, "share", {
      configurable: true,
      value: share,
    });
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Share to app" }));

    expect(screen.getByRole("button", { name: "Opening…" })).toBeDisabled();
    expect(share).toHaveBeenCalledTimes(1);

    resolveShare();
    expect(await screen.findByText("Shared.")).toBeInTheDocument();
  });

  it("keeps the modal open without an error when native sharing is cancelled", async () => {
    mockSWRData({ targets: [] });
    const share = vi.fn().mockRejectedValue(new DOMException("cancelled", "AbortError"));
    Object.defineProperty(navigator, "share", {
      configurable: true,
      value: share,
    });
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Share to app" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Share to app" })).toBeEnabled(),
    );
    expect(screen.queryByText("Shared.")).not.toBeInTheDocument();
    expect(screen.queryByText("cancelled")).not.toBeInTheDocument();
  });

  it("shows the native share error and allows a retry", async () => {
    mockSWRData({ targets: [] });
    const share = vi.fn().mockRejectedValue(new Error("Native share failed"));
    Object.defineProperty(navigator, "share", {
      configurable: true,
      value: share,
    });
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Share to app" }));

    expect(await screen.findByText("Native share failed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Share to app" })).toBeEnabled();
  });

  it("falls back to a generic error for a non-Error native rejection", async () => {
    mockSWRData({ targets: [] });
    const share = vi.fn().mockRejectedValue("failed");
    Object.defineProperty(navigator, "share", {
      configurable: true,
      value: share,
    });
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Share to app" }));

    expect(await screen.findByText("Could not open the app picker")).toBeInTheDocument();
  });

  it("groups the app picker with Send instead of the AI drafting action", () => {
    mockSWRData({ ai: true });
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);

    const chooseApp = screen.getByRole("button", { name: "Share to app" });
    const send = screen.getByRole("button", { name: "Send" });
    const draft = screen.getByRole("button", { name: "Draft with AI" });

    expect(chooseApp.parentElement).toBe(send.parentElement);
    expect(draft.parentElement).not.toBe(chooseApp.parentElement);
  });

  it("copies the message and link when native app sharing is unavailable", async () => {
    mockSWRData({ targets: [] });
    Reflect.deleteProperty(navigator, "share");
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    render(
      <ShareModal
        article={makeArticle({ url: "https://a.example/fallback" })}
        onClose={vi.fn()}
      />,
    );

    await userEvent.type(
      screen.getByLabelText(/Message/),
      "Read this",
    );
    await userEvent.click(screen.getByRole("button", { name: "Share to app" }));

    expect(writeText).toHaveBeenCalledWith("Read this\nhttps://a.example/fallback");
    expect(
      await screen.findByText("Message and link copied. Paste them into any app."),
    ).toBeInTheDocument();
  });

  it("copies only the link when native sharing is unavailable and the note is blank", async () => {
    mockSWRData({ targets: [] });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    render(
      <ShareModal
        article={makeArticle({ url: "https://a.example/fallback" })}
        onClose={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Share to app" }));

    expect(writeText).toHaveBeenCalledWith("https://a.example/fallback");
    expect(
      await screen.findByText("Message and link copied. Paste them into any app."),
    ).toBeInTheDocument();
  });

  it("shows an unsupported message when neither native sharing nor clipboard exists", async () => {
    mockSWRData({ targets: [] });
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Share to app" }));

    expect(
      await screen.findByText("App sharing is not supported in this browser"),
    ).toBeInTheDocument();
  });

  it("shows an error when the clipboard fallback fails", async () => {
    mockSWRData({ targets: [] });
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Share to app" }));

    expect(
      await screen.findByText("Could not open the app picker or copy the link"),
    ).toBeInTheDocument();
  });

  it("hides the AI button when the LLM is not configured", () => {
    mockSWRData({ ai: false });
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);
    expect(screen.queryByText(/Draft with AI/)).not.toBeInTheDocument();
  });

  it("drafts a message with AI and fills the note", async () => {
    mockSWRData({ ai: true });
    const { fn, calls } = makeFetch();
    vi.stubGlobal("fetch", fn);
    render(<ShareModal article={makeArticle({ id: 7 })} onClose={vi.fn()} />);

    await userEvent.click(screen.getByText("Draft with AI"));
    await waitFor(() =>
      expect(screen.getByLabelText(/Message/)).toHaveValue(
        "A crisp AI note.",
      ),
    );
    const ai = calls.filter((c) => c.url.includes("/ai/share-message"));
    expect(ai[0].body).toEqual({ article_id: 7, draft: "" });
    // With a draft present the button flips to refine mode.
    expect(screen.getByText("Refine with AI")).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("focuses the message first and requires a destination before sending", () => {
    mockSWRData();
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);
    expect(screen.getByLabelText(/Message/)).toHaveFocus();
    expect(screen.getByLabelText(/Message/)).toHaveClass("text-[16px]");
    expect(screen.getByRole("combobox", { name: /Share to/ })).toHaveClass("text-[16px]");
    expect(screen.getByRole("button", { name: /Send/ })).toBeDisabled();
  });
});
