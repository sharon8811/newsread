import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ShareModal from "@/components/ShareModal";
import { makeArticle, makeShareTarget } from "./fixtures";

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

function mockSWRData({ targets = [slackTarget, teamsTarget], ai = false } = {}) {
  swrMock.mockImplementation((key: string) => {
    if (key === "/share-targets") return { data: targets };
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

  it("renders saved quick-share chips without a duplicate Slack hash", () => {
    mockSWRData();
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);
    expect(screen.getByText("ai-news")).toBeInTheDocument();
    expect(screen.queryByText("#ai-news")).not.toBeInTheDocument();
    expect(screen.getByText("Eng › General")).toBeInTheDocument();
    expect(screen.getByText("WhatsApp")).toBeInTheDocument();
  });

  it("sends to a selected target with the note as message", async () => {
    mockSWRData();
    const { fn, calls } = makeFetch();
    vi.stubGlobal("fetch", fn);
    const onClose = vi.fn();
    render(<ShareModal article={makeArticle({ id: 7 })} onClose={onClose} />);

    await userEvent.click(screen.getByText("ai-news"));
    await userEvent.type(
      screen.getByPlaceholderText(/Why are you sharing this/),
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

  it("keeps the modal open and shows the target name when a send fails", async () => {
    mockSWRData();
    const { fn } = makeFetch({
      externalStatus: 502,
      externalDetail: { message: "not a member", reconnect: false },
    });
    vi.stubGlobal("fetch", fn);
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);

    await userEvent.click(screen.getByText("ai-news"));
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
      screen.getByPlaceholderText(/Why are you sharing this/),
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
      screen.getByPlaceholderText(/Why are you sharing this/),
      "Worth your time",
    );
    await userEvent.click(screen.getByRole("button", { name: "Choose an app" }));

    expect(share).toHaveBeenCalledWith({
      title: "A useful story",
      text: "Worth your time",
      url: "https://a.example/native",
    });
    expect(await screen.findByText("Shared.")).toBeInTheDocument();
    Reflect.deleteProperty(navigator, "share");
  });

  it("groups the app picker with Send instead of the AI drafting action", () => {
    mockSWRData({ ai: true });
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);

    const chooseApp = screen.getByRole("button", { name: "Choose an app" });
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
      screen.getByPlaceholderText(/Why are you sharing this/),
      "Read this",
    );
    await userEvent.click(screen.getByRole("button", { name: "Choose an app" }));

    expect(writeText).toHaveBeenCalledWith("Read this\nhttps://a.example/fallback");
    expect(
      await screen.findByText("Message and link copied. Paste them into any app."),
    ).toBeInTheDocument();
    Reflect.deleteProperty(navigator, "clipboard");
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
      expect(screen.getByPlaceholderText(/Why are you sharing this/)).toHaveValue(
        "A crisp AI note.",
      ),
    );
    const ai = calls.filter((c) => c.url.includes("/ai/share-message"));
    expect(ai[0].body).toEqual({ article_id: 7, draft: "" });
    // With a draft present the button flips to refine mode.
    expect(screen.getByText("Refine with AI")).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("requires a reader or a channel before sending", () => {
    mockSWRData();
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);
    expect(screen.getByText("Select a reader or channel")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Send/ })).toBeDisabled();
  });
});
