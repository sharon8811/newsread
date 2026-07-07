import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ProjectPage from "@/app/(app)/projects/[id]/page";
import { makeProject, makeProjectArticle, makePublic, makeUser } from "./fixtures";

const { swrMock, mutateMock, routerMock } = vi.hoisted(() => ({
  swrMock: vi.fn(),
  mutateMock: vi.fn(),
  routerMock: { push: vi.fn() },
}));
vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "1" }),
  useRouter: () => routerMock,
}));

const { authState } = vi.hoisted(() => ({
  authState: { user: null as unknown },
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => authState }));

const { cardProps } = vi.hoisted(() => ({
  cardProps: [] as Record<string, unknown>[],
}));
vi.mock("@/components/ProjectPinCard", () => ({
  default: (props: Record<string, unknown>) => {
    cardProps.push(props);
    return <div data-testid="pin-card" />;
  },
  groupPins: (pins: { is_shared: boolean }[]) => pins.map((p) => [p]),
}));

const alice = makePublic({ id: 1, username: "alice", name: "Alice" });
const bob = makePublic({ id: 2, username: "bob", name: "Bob" });

const ownedProject = makeProject({
  id: 1,
  name: "AI Research",
  description: "the frontier",
  my_role: "owner",
  members: [
    { user: alice, role: "owner" },
    { user: bob, role: "member" },
  ],
});

function setSwr({
  project,
  pins,
  error,
}: {
  project?: unknown;
  pins?: unknown;
  error?: unknown;
} = {}) {
  swrMock.mockImplementation((key: string) => {
    if (key === "/projects/1") return { data: project, error };
    if (key === "/projects/1/articles") return { data: pins, isLoading: pins === undefined };
    return { data: undefined };
  });
}

function okFetch(payload: unknown = {}) {
  return vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => payload });
}

describe("ProjectPage", () => {
  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
    routerMock.push.mockClear();
    cardProps.length = 0;
    authState.user = makeUser({ id: 1, username: "alice" });
    vi.stubGlobal("fetch", okFetch());
  });

  it("shows the error state", async () => {
    setSwr({ error: new Error("nope") });
    render(<ProjectPage />);
    expect(screen.getByText("This project is out of reach.")).toBeInTheDocument();
    await userEvent.click(screen.getByText("Back to projects"));
    expect(routerMock.push).toHaveBeenCalledWith("/projects");
  });

  it("shows a skeleton while loading", () => {
    setSwr({});
    const { container } = render(<ProjectPage />);
    expect(container.querySelector("h1")).toBeNull();
  });

  it("renders name, description, members and tab counts", () => {
    setSwr({
      project: ownedProject,
      pins: [
        makeProjectArticle({ id: 1 }),
        makeProjectArticle({ id: 2, is_shared: false, shared_at: null, added_by: alice }),
      ],
    });
    render(<ProjectPage />);
    expect(screen.getByText("AI Research")).toBeInTheDocument();
    expect(screen.getByText("the frontier")).toBeInTheDocument();
    expect(screen.getByTitle(/Alice.*owner/)).toBeInTheDocument();
    expect(screen.getByTitle("Bob (@bob)")).toBeInTheDocument();
    // one shared, one private
    expect(screen.getByRole("button", { name: /^Shared/ }).textContent).toContain("1");
    expect(screen.getByRole("button", { name: /Only you/ }).textContent).toContain("1");
  });

  it("renders shared cards by default and private pins on the mine tab", async () => {
    setSwr({
      project: ownedProject,
      pins: [
        makeProjectArticle({ id: 1 }),
        makeProjectArticle({ id: 2, is_shared: false, shared_at: null }),
      ],
    });
    render(<ProjectPage />);
    expect(screen.getAllByTestId("pin-card")).toHaveLength(1);
    expect(cardProps[0]).toMatchObject({ myId: 1, isOwner: true, projectName: "AI Research" });

    await userEvent.click(screen.getByRole("button", { name: /Only you/ }));
    expect(screen.getAllByTestId("pin-card")).toHaveLength(1);
  });

  it("shows both empty states", async () => {
    setSwr({ project: ownedProject, pins: [] });
    render(<ProjectPage />);
    expect(screen.getByText("Nothing shared yet.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Only you/ }));
    expect(screen.getByText("Your private pile is empty.")).toBeInTheDocument();
  });

  it("owner invites a user found via search", async () => {
    const carol = makePublic({ id: 3, username: "carol", name: "Carol" });
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/users/search")) {
        return Promise.resolve({ status: 200, ok: true, json: async () => [carol] });
      }
      return Promise.resolve({ status: 201, ok: true, json: async () => ({}) });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSwr({ project: ownedProject, pins: [] });
    render(<ProjectPage />);

    await userEvent.click(screen.getByTitle("Invite someone"));
    await userEvent.type(screen.getByPlaceholderText(/@username to invite/), "@car");
    await userEvent.click(await screen.findByText("Carol"));

    await waitFor(() => expect(mutateMock).toHaveBeenCalledWith("/projects/1"));
    const invite = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/members"))!;
    expect(JSON.parse(invite[1].body)).toEqual({ username: "carol" });
  });

  it("filters existing members out of invite results and clears on empty query", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/users/search")) {
        return Promise.resolve({ status: 200, ok: true, json: async () => [bob] });
      }
      return Promise.resolve({ status: 200, ok: true, json: async () => ({}) });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSwr({ project: ownedProject, pins: [] });
    render(<ProjectPage />);

    await userEvent.click(screen.getByTitle("Invite someone"));
    const input = screen.getByPlaceholderText(/@username to invite/);
    await userEvent.type(input, "bo");
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByText("Bob", { selector: "button *" })).not.toBeInTheDocument();
    await userEvent.clear(input);
  });

  it("swallows a failing search", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/users/search")) return Promise.reject(new Error("net"));
      return Promise.resolve({ status: 200, ok: true, json: async () => ({}) });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSwr({ project: ownedProject, pins: [] });
    render(<ProjectPage />);
    await userEvent.click(screen.getByTitle("Invite someone"));
    await userEvent.type(screen.getByPlaceholderText(/@username to invite/), "zz");
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  });

  it("shows the API error when inviting fails", async () => {
    const carol = makePublic({ id: 3, username: "carol", name: "Carol" });
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: { method?: string }) => {
      if (String(url).includes("/users/search")) {
        return Promise.resolve({ status: 200, ok: true, json: async () => [carol] });
      }
      if (opts?.method === "POST") {
        return Promise.resolve({
          status: 409,
          ok: false,
          json: async () => ({ detail: "Already a member" }),
        });
      }
      return Promise.resolve({ status: 200, ok: true, json: async () => ({}) });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSwr({ project: ownedProject, pins: [] });
    render(<ProjectPage />);
    await userEvent.click(screen.getByTitle("Invite someone"));
    await userEvent.type(screen.getByPlaceholderText(/@username to invite/), "car");
    await userEvent.click(await screen.findByText("Carol"));
    expect(await screen.findByText("Already a member")).toBeInTheDocument();
  });

  it("owner removes a member", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    setSwr({ project: ownedProject, pins: [] });
    render(<ProjectPage />);
    await userEvent.click(screen.getByTitle("Remove Bob"));
    await waitFor(() => expect(mutateMock).toHaveBeenCalledWith("/projects/1"));
    const call = fetchMock.mock.calls.find(([, o]) => o?.method === "DELETE")!;
    expect(String(call[0])).toContain("/projects/1/members/2");
    expect(routerMock.push).not.toHaveBeenCalled();
  });

  it("owner deletes the project only after the inline confirm", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    setSwr({ project: ownedProject, pins: [] });
    render(<ProjectPage />);
    await userEvent.click(screen.getByTitle("Delete project"));
    // first click only arms the confirm — nothing deleted yet
    expect(fetchMock.mock.calls.filter(([, o]) => o?.method === "DELETE")).toHaveLength(0);
    expect(screen.getByText("Delete for every member?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(routerMock.push).toHaveBeenCalledWith("/projects"));
    expect(mutateMock).toHaveBeenCalledWith("/projects");
  });

  it("cancel disarms the delete confirm", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    setSwr({ project: ownedProject, pins: [] });
    render(<ProjectPage />);
    await userEvent.click(screen.getByTitle("Delete project"));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByTitle("Delete project")).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([, o]) => o?.method === "DELETE")).toHaveLength(0);
  });

  it("surfaces a delete failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue("boom"));
    setSwr({ project: ownedProject, pins: [] });
    render(<ProjectPage />);
    await userEvent.click(screen.getByTitle("Delete project"));
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("Could not delete project")).toBeInTheDocument();
    expect(routerMock.push).not.toHaveBeenCalled();
  });

  it("member sees Leave instead of owner controls and leaving navigates away", async () => {
    authState.user = makeUser({ id: 2, username: "bob" });
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    setSwr({
      project: makeProject({
        ...ownedProject,
        my_role: "member",
      }),
      pins: [],
    });
    render(<ProjectPage />);
    expect(screen.queryByTitle("Invite someone")).not.toBeInTheDocument();
    expect(screen.queryByTitle("Delete project")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Leave" }));
    await waitFor(() => expect(routerMock.push).toHaveBeenCalledWith("/projects"));
    const call = fetchMock.mock.calls.find(([, o]) => o?.method === "DELETE")!;
    expect(String(call[0])).toContain("/projects/1/members/2");
  });

  it("posts a visit on load and revalidates the projects list", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    setSwr({ project: ownedProject, pins: [] });
    render(<ProjectPage />);
    await waitFor(() => {
      const visit = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/projects/1/visit"));
      expect(visit).toBeTruthy();
      expect(visit![1].method).toBe("POST");
    });
    await waitFor(() => expect(mutateMock).toHaveBeenCalledWith("/projects"));
  });

  it("toggles the project mute for the viewer", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    setSwr({ project: ownedProject, pins: [] });
    render(<ProjectPage />);
    await userEvent.click(screen.getByTitle(/Mute notifications/));
    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(([u]) =>
        String(u).endsWith("/projects/1/membership"),
      )!;
      expect(JSON.parse(patch[1].body)).toEqual({ is_muted: true });
    });
    expect(mutateMock).toHaveBeenCalledWith("/projects/1");
  });

  it("offers unmute when already muted", () => {
    setSwr({ project: makeProject({ ...ownedProject, is_muted: true }), pins: [] });
    render(<ProjectPage />);
    expect(screen.getByTitle(/Unmute/)).toBeInTheDocument();
  });

  it("surfaces a member-removal failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("cannot")));
    setSwr({ project: ownedProject, pins: [] });
    render(<ProjectPage />);
    await userEvent.click(screen.getByTitle("Remove Bob"));
    expect(await screen.findByText("cannot")).toBeInTheDocument();
  });
});
