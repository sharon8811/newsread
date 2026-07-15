import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FeedSettingsModal from "@/components/FeedSettingsModal";
import { makeFeed } from "./fixtures";

const { mutateMock, mutateListsMock } = vi.hoisted(() => ({
  mutateMock: vi.fn(),
  mutateListsMock: vi.fn(),
}));
vi.mock("swr", () => ({ mutate: mutateMock }));
vi.mock("@/components/ArticleList", () => ({ mutateArticleLists: mutateListsMock }));

function okFetch(body: unknown = {}) {
  return vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => body });
}

async function lastBody(fetchMock: ReturnType<typeof vi.fn>) {
  const [, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
  return JSON.parse((init as RequestInit).body as string);
}

describe("<FeedSettingsModal>", () => {
  beforeEach(() => {
    mutateMock.mockClear();
    mutateListsMock.mockClear();
  });

  it("renders the feed title and current settings", () => {
    render(
      <FeedSettingsModal
        feed={makeFeed({ title: "Tech Feed", retention_days: 30, sort_order: "oldest" })}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Tech Feed")).toBeInTheDocument();
    expect(screen.getByLabelText("Sort order")).toHaveValue("oldest");
    expect(screen.getByLabelText("Retention")).toHaveValue("30");
    expect(screen.getByLabelText("View mode")).toHaveValue("default");
  });

  it("closes without a request when nothing changed", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    const onClose = vi.fn();
    render(<FeedSettingsModal feed={makeFeed()} onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onClose).toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("patches only the changed fields", async () => {
    const fetchMock = okFetch(makeFeed());
    vi.stubGlobal("fetch", fetchMock);
    const onClose = vi.fn();
    render(<FeedSettingsModal feed={makeFeed({ id: 5 })} onClose={onClose} />);

    await userEvent.type(screen.getByLabelText("Custom name"), "  My Tech  ");
    await userEvent.selectOptions(screen.getByLabelText("Sort order"), "oldest");
    await userEvent.selectOptions(screen.getByLabelText("Retention"), "7");
    await userEvent.click(screen.getByRole("switch", { name: "Mute feed" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][0]).toContain("/feeds/5/settings");
    expect(await lastBody(fetchMock)).toEqual({
      title_override: "My Tech",
      sort_order: "oldest",
      retention_days: 7,
      is_muted: true,
    });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(mutateListsMock).toHaveBeenCalled();
  });

  it("clears overrides back to null", async () => {
    const fetchMock = okFetch(makeFeed());
    vi.stubGlobal("fetch", fetchMock);
    render(
      <FeedSettingsModal
        feed={makeFeed({
          title_override: "Renamed",
          sort_order: "oldest",
          retention_days: 30,
          view_override: "cards",
        })}
        onClose={vi.fn()}
      />,
    );
    await userEvent.clear(screen.getByLabelText("Custom name"));
    await userEvent.selectOptions(screen.getByLabelText("Sort order"), "newest");
    await userEvent.selectOptions(screen.getByLabelText("Retention"), "0");
    await userEvent.selectOptions(screen.getByLabelText("View mode"), "default");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(await lastBody(fetchMock)).toEqual({
      title_override: null,
      sort_order: null,
      retention_days: null,
      view_override: null,
    });
  });

  it("patches the global feed fields", async () => {
    const fetchMock = okFetch(makeFeed());
    vi.stubGlobal("fetch", fetchMock);
    render(<FeedSettingsModal feed={makeFeed()} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("switch", { name: "AI summaries" }));
    await userEvent.selectOptions(screen.getByLabelText("Refresh interval"), "60");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(await lastBody(fetchMock)).toEqual({
      ai_enabled: false,
      refresh_interval_minutes: 60,
    });
  });

  it("offers the feed's off-preset refresh interval as an option", () => {
    render(
      <FeedSettingsModal feed={makeFeed({ refresh_interval_minutes: 45 })} onClose={vi.fn()} />,
    );
    expect(screen.getByLabelText("Refresh interval")).toHaveValue("45");
  });

  it("shows an error and stays open when saving fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 422,
        ok: false,
        json: async () => ({ detail: "Nothing to update" }),
      }),
    );
    const onClose = vi.fn();
    render(<FeedSettingsModal feed={makeFeed()} onClose={onClose} />);
    await userEvent.selectOptions(screen.getByLabelText("Sort order"), "oldest");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByText("Nothing to update")).toBeInTheDocument());
    expect(onClose).not.toHaveBeenCalled();
  });

  it("unsubscribes only after the confirmation click", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ status: 204, ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    const onClose = vi.fn();
    const onUnsubscribed = vi.fn();
    render(
      <FeedSettingsModal
        feed={makeFeed({ id: 3 })}
        onClose={onClose}
        onUnsubscribed={onUnsubscribed}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Unsubscribe/ }));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText("Really unsubscribe?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Really unsubscribe/ }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][0]).toContain("/feeds/3");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
    expect(mutateMock).toHaveBeenCalledWith("/feeds");
    await waitFor(() => expect(onUnsubscribed).toHaveBeenCalled());
    expect(onClose).toHaveBeenCalled();
  });

  it("shows an error when unsubscribing fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 500,
        ok: false,
        json: async () => ({ detail: "boom" }),
      }),
    );
    render(<FeedSettingsModal feed={makeFeed()} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /Unsubscribe/ }));
    await userEvent.click(screen.getByRole("button", { name: /Really unsubscribe/ }));
    await waitFor(() => expect(screen.getByText("boom")).toBeInTheDocument());
  });

  it("closes on Escape and on backdrop click, not on inner clicks", async () => {
    const onClose = vi.fn();
    render(<FeedSettingsModal feed={makeFeed()} onClose={onClose} />);
    await userEvent.click(screen.getByText("Feed settings"));
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.click(screen.getByTestId("modal-overlay"));
    expect(onClose).toHaveBeenCalledTimes(1);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});

describe("<FeedSettingsModal> AI images", () => {
  it("patches the AI images toggle", async () => {
    const fetchMock = okFetch(makeFeed());
    vi.stubGlobal("fetch", fetchMock);
    render(<FeedSettingsModal feed={makeFeed()} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("switch", { name: "AI images" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(await lastBody(fetchMock)).toEqual({ image_gen_enabled: false });
  });

  it("reflects a disabled feed and can re-enable it", async () => {
    const fetchMock = okFetch(makeFeed());
    vi.stubGlobal("fetch", fetchMock);
    render(
      <FeedSettingsModal feed={makeFeed({ image_gen_enabled: false })} onClose={vi.fn()} />,
    );
    const toggle = screen.getByRole("switch", { name: "AI images" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    await userEvent.click(toggle);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(await lastBody(fetchMock)).toEqual({ image_gen_enabled: true });
  });
});
