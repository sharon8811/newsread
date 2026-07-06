import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ViewSwitcher from "@/components/ViewSwitcher";
import { makeFeed, makeUser } from "./fixtures";

const { mutateMock, authState } = vi.hoisted(() => ({
  mutateMock: vi.fn(),
  authState: { user: null as unknown, updateUser: vi.fn() },
}));
vi.mock("swr", () => ({ mutate: mutateMock }));
vi.mock("@/lib/auth", () => ({ useAuth: () => authState }));

function okFetch(body: unknown = {}) {
  return vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => body });
}

describe("<ViewSwitcher>", () => {
  beforeEach(() => {
    mutateMock.mockClear();
    authState.user = makeUser({ default_view: "list" });
    authState.updateUser = vi.fn();
  });

  it("renders the three view buttons", () => {
    render(<ViewSwitcher view="list" feed={null} />);
    expect(screen.getByLabelText("List view")).toBeInTheDocument();
    expect(screen.getByLabelText(/Zen view/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Stories view/)).toBeInTheDocument();
  });

  it("ignores clicking the active view", async () => {
    const onSwitch = vi.fn();
    render(<ViewSwitcher view="list" feed={null} onSwitch={onSwitch} />);
    await userEvent.click(screen.getByLabelText("List view"));
    expect(onSwitch).not.toHaveBeenCalled();
  });

  it("sets the default view when no feed is given", async () => {
    const fetchMock = okFetch(makeUser({ default_view: "zen" }));
    vi.stubGlobal("fetch", fetchMock);
    const onSwitch = vi.fn();
    render(<ViewSwitcher view="list" feed={null} onSwitch={onSwitch} />);
    await userEvent.click(screen.getByLabelText(/Zen view/));
    expect(onSwitch).toHaveBeenCalledWith("zen");
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][0]).toContain("/users/me");
  });

  it("sets a feed override when a feed is given", async () => {
    const fetchMock = okFetch(makeFeed({ view_override: "zen" }));
    vi.stubGlobal("fetch", fetchMock);
    // Invoke the optimistic-update callback so its cache-map branch runs.
    mutateMock.mockImplementation((_key: string, fn?: unknown) => {
      if (typeof fn === "function") {
        (fn as (feeds: unknown) => unknown)([makeFeed({ id: 1 }), makeFeed({ id: 2 })]);
        (fn as (feeds: unknown) => unknown)(undefined);
      }
    });
    render(<ViewSwitcher view="list" feed={makeFeed({ id: 1 })} onSwitch={vi.fn()} />);
    await userEvent.click(screen.getByLabelText(/Zen view/));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][0]).toContain("/feeds/1/settings");
    expect(mutateMock).toHaveBeenCalled();
  });

  it("shows override controls when the feed differs from the default", () => {
    render(<ViewSwitcher view="zen" feed={makeFeed({ view_override: "zen" })} />);
    expect(screen.getByText("reset")).toBeInTheDocument();
    expect(screen.getByText("make default")).toBeInTheDocument();
  });

  it("reset clears the override", async () => {
    vi.stubGlobal("fetch", okFetch(makeFeed({ view_override: null })));
    const onSwitch = vi.fn();
    render(<ViewSwitcher view="zen" feed={makeFeed({ view_override: "zen" })} onSwitch={onSwitch} />);
    await userEvent.click(screen.getByText("reset"));
    expect(onSwitch).toHaveBeenCalledWith("list");
  });

  it("make default persists the current view and clears the override", async () => {
    const fetchMock = okFetch(makeUser({ default_view: "zen" }));
    vi.stubGlobal("fetch", fetchMock);
    const onSwitch = vi.fn();
    render(<ViewSwitcher view="zen" feed={makeFeed({ view_override: "zen" })} onSwitch={onSwitch} />);
    await userEvent.click(screen.getByText("make default"));
    expect(onSwitch).toHaveBeenCalledWith("zen");
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(1));
  });

  it("does not show override controls without a feed", () => {
    render(<ViewSwitcher view="list" feed={null} />);
    expect(screen.queryByText("reset")).not.toBeInTheDocument();
  });
});
