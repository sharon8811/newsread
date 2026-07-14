import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ProjectPickerModal from "@/components/ProjectPickerModal";
import { makeArticle, makeProject, makeProjectStatus, makePublic } from "./fixtures";

const { swrMock, mutateMock } = vi.hoisted(() => ({ swrMock: vi.fn(), mutateMock: vi.fn() }));
vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));

function setSwr(projects: unknown, statuses: unknown) {
  swrMock.mockImplementation((key: string) => {
    if (key === "/projects") return { data: projects };
    return { data: statuses };
  });
}

function okFetch() {
  return vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => ({ id: 9 }) });
}

const twoMembers = makeProject({
  id: 1,
  name: "AI Research",
  members: [
    { user: makePublic({ id: 1, username: "alice", name: "Alice" }), role: "owner" },
    { user: makePublic({ id: 2, username: "bob", name: "Bob" }), role: "member" },
  ],
});

describe("<ProjectPickerModal>", () => {
  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
    localStorage.clear();
    vi.stubGlobal("fetch", okFetch());
  });

  it("renders projects with member counts and the article title", () => {
    setSwr([twoMembers, makeProject({ id: 2, name: "Solo" })], [makeProjectStatus()]);
    render(<ProjectPickerModal article={makeArticle({ title: "My Story" })} onClose={vi.fn()} />);
    expect(screen.getByText("My Story")).toBeInTheDocument();
    expect(screen.getByText("AI Research")).toBeInTheDocument();
    expect(screen.getByText("2 members")).toBeInTheDocument();
    expect(screen.getByText("1 member")).toBeInTheDocument();
  });

  it("portals the picker outside a transformed article", () => {
    setSwr([], []);
    render(
      <article data-testid="article" style={{ transform: "translateY(0)" }}>
        <ProjectPickerModal article={makeArticle()} onClose={vi.fn()} />
      </article>,
    );
    const dialog = screen.getByRole("dialog", { name: "A Great Article" });
    expect(screen.getByTestId("article")).not.toContainElement(dialog);
    expect(document.body).toContainElement(dialog);
  });

  it("shows the empty state without projects", () => {
    setSwr([], []);
    render(<ProjectPickerModal article={makeArticle()} onClose={vi.fn()} />);
    expect(screen.getByText(/No projects yet/)).toBeInTheDocument();
  });

  it("flags projects where a teammate already shared the article", () => {
    setSwr([twoMembers], [makeProjectStatus({ shared_by_others: true })]);
    render(<ProjectPickerModal article={makeArticle()} onClose={vi.fn()} />);
    expect(screen.getByText(/already shared here/)).toBeInTheDocument();
  });

  it("adds privately by default with a trimmed note", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    setSwr([twoMembers], [makeProjectStatus()]);
    render(<ProjectPickerModal article={makeArticle({ id: 7 })} onClose={vi.fn()} />);

    await userEvent.type(screen.getByPlaceholderText(/Optional note/), "  context  ");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => expect(mutateMock).toHaveBeenCalledWith("/projects/article/7"));
    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/projects/1/articles"))!;
    expect(JSON.parse(call[1].body)).toEqual({
      article_id: 7,
      is_shared: false,
      note: "context",
    });
    expect(mutateMock).toHaveBeenCalledWith("/projects");
  });

  it("toggles visibility to shared, persists it, and posts is_shared true", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    setSwr([twoMembers], [makeProjectStatus()]);
    render(<ProjectPickerModal article={makeArticle({ id: 7 })} onClose={vi.fn()} />);

    await userEvent.click(screen.getByTitle(/Only you will see it/));
    expect(screen.getByTitle(/visible to everyone in AI Research/i)).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("newsread_project_vis")!)).toEqual({ "1": true });

    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/projects/1/articles"))!;
    expect(JSON.parse(call[1].body).is_shared).toBe(true);
  });

  it("restores the remembered visibility from localStorage", async () => {
    localStorage.setItem("newsread_project_vis", JSON.stringify({ "1": true }));
    setSwr([twoMembers], [makeProjectStatus()]);
    render(<ProjectPickerModal article={makeArticle()} onClose={vi.fn()} />);
    expect(await screen.findByTitle(/visible to everyone/i)).toBeInTheDocument();
  });

  it("survives corrupt localStorage", async () => {
    localStorage.setItem("newsread_project_vis", "{nope");
    setSwr([twoMembers], [makeProjectStatus()]);
    render(<ProjectPickerModal article={makeArticle()} onClose={vi.fn()} />);
    expect(await screen.findByTitle(/Only you will see it/)).toBeInTheDocument();
  });

  it("shows the added chip and removes the pin", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    setSwr(
      [twoMembers],
      [makeProjectStatus({ project_article_id: 42, is_shared: false })],
    );
    render(<ProjectPickerModal article={makeArticle({ id: 7 })} onClose={vi.fn()} />);

    expect(screen.getByText("Only you")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByTitle("Remove from project"));
    await waitFor(() => expect(mutateMock).toHaveBeenCalledWith("/projects/article/7"));
    const call = fetchMock.mock.calls.find(([, opts]) => opts?.method === "DELETE")!;
    expect(String(call[0])).toContain("/projects/1/articles/42");
  });

  it("labels a shared pin as Shared", () => {
    setSwr([twoMembers], [makeProjectStatus({ project_article_id: 42, is_shared: true })]);
    render(<ProjectPickerModal article={makeArticle()} onClose={vi.fn()} />);
    expect(screen.getByText("Shared")).toBeInTheDocument();
  });

  it("creates a project and pins the article to it privately", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    setSwr([], []);
    render(<ProjectPickerModal article={makeArticle({ id: 7 })} onClose={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /New project/ }));
    await userEvent.type(screen.getByPlaceholderText("Project name"), "  Fresh  ");
    await userEvent.click(screen.getByRole("button", { name: /Create & add/ }));

    await waitFor(() => expect(mutateMock).toHaveBeenCalledWith("/projects"));
    const create = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/projects"))!;
    expect(JSON.parse(create[1].body)).toEqual({ name: "Fresh" });
    const pin = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/projects/9/articles"))!;
    expect(JSON.parse(pin[1].body).is_shared).toBe(false);
    // form resets back to the button
    expect(await screen.findByRole("button", { name: /New project/ })).toBeInTheDocument();
  });

  it("floats the suggested project to the top with a chip", () => {
    setSwr(
      [makeProject({ id: 1, name: "Sports" }), makeProject({ id: 2, name: "AI" })],
      [
        makeProjectStatus({ project_id: 1, project_name: "Sports" }),
        makeProjectStatus({ project_id: 2, project_name: "AI", suggested: true }),
      ],
    );
    render(<ProjectPickerModal article={makeArticle()} onClose={vi.fn()} />);
    expect(screen.getByText("Suggested")).toBeInTheDocument();
    const names = screen
      .getAllByText(/^(AI|Sports)/)
      .map((el) => el.textContent);
    expect(names[0]).toContain("AI"); // suggested first, despite list order
    expect(names[1]).toBe("Sports");
  });

  it("disables Add until pin statuses have loaded", () => {
    setSwr([twoMembers], undefined);
    render(<ProjectPickerModal article={makeArticle()} onClose={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
  });

  it("reports a created project whose pin call failed, and still revalidates", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: { method?: string }) => {
      if (String(url).endsWith("/projects") && opts?.method === "POST") {
        return Promise.resolve({
          status: 201,
          ok: true,
          json: async () => ({ id: 9, name: "Fresh" }),
        });
      }
      return Promise.resolve({
        status: 404,
        ok: false,
        json: async () => ({ detail: "Article not found" }),
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSwr([], []);
    render(<ProjectPickerModal article={makeArticle()} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /New project/ }));
    await userEvent.type(screen.getByPlaceholderText("Project name"), "Fresh");
    await userEvent.click(screen.getByRole("button", { name: /Create & add/ }));
    expect(
      await screen.findByText(/Created "Fresh", but couldn't add the article: Article not found/),
    ).toBeInTheDocument();
    expect(mutateMock).toHaveBeenCalledWith("/projects");
  });

  it("shows the API error when adding fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 409,
        ok: false,
        json: async () => ({ detail: "You already added this article" }),
      }),
    );
    setSwr([twoMembers], [makeProjectStatus()]);
    render(<ProjectPickerModal article={makeArticle()} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText("You already added this article")).toBeInTheDocument();
  });

  it("shows an error when project creation fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue("boom"),
    );
    setSwr([], []);
    render(<ProjectPickerModal article={makeArticle()} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /New project/ }));
    await userEvent.type(screen.getByPlaceholderText("Project name"), "X");
    await userEvent.click(screen.getByRole("button", { name: /Create & add/ }));
    expect(await screen.findByText("Could not create project")).toBeInTheDocument();
  });

  it("shows an error when removal fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("gone")));
    setSwr([twoMembers], [makeProjectStatus({ project_article_id: 42, is_shared: false })]);
    render(<ProjectPickerModal article={makeArticle()} onClose={vi.fn()} />);
    await userEvent.click(screen.getByTitle("Remove from project"));
    expect(await screen.findByText("gone")).toBeInTheDocument();
  });

  it("closes on Escape, backdrop and the X button, but not panel clicks", async () => {
    setSwr([], []);
    const onClose = vi.fn();
    render(
      <ProjectPickerModal article={makeArticle({ title: "T" })} onClose={onClose} />,
    );
    await userEvent.click(screen.getByText("T"));
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    await userEvent.keyboard("a");
    expect(onClose).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByTestId("modal-overlay"));
    expect(onClose).toHaveBeenCalledTimes(2);
    await userEvent.click(screen.getByRole("button", { name: "Close project picker" }));
    expect(onClose).toHaveBeenCalledTimes(3);
  });
});
