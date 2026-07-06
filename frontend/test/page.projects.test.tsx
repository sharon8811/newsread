import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ProjectsPage from "@/app/(app)/projects/page";
import { makeProject } from "./fixtures";

const { swrMock, mutateMock } = vi.hoisted(() => ({ swrMock: vi.fn(), mutateMock: vi.fn() }));
vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));

function okFetch() {
  return vi.fn().mockResolvedValue({ status: 201, ok: true, json: async () => ({ id: 1 }) });
}

describe("ProjectsPage", () => {
  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
    vi.stubGlobal("fetch", okFetch());
  });

  it("shows the empty state", () => {
    swrMock.mockReturnValue({ data: [], isLoading: false });
    render(<ProjectsPage />);
    expect(screen.getByText("No projects yet.")).toBeInTheDocument();
  });

  it("hides the empty state while loading", () => {
    swrMock.mockReturnValue({ data: undefined, isLoading: true });
    render(<ProjectsPage />);
    expect(screen.queryByText("No projects yet.")).not.toBeInTheDocument();
  });

  it("lists projects with counts, description and links", () => {
    swrMock.mockReturnValue({
      data: [
        makeProject({ id: 3, name: "AI", description: "the good stuff", article_count: 2 }),
        makeProject({ id: 4, name: "Solo", article_count: 1 }),
      ],
      isLoading: false,
    });
    render(<ProjectsPage />);
    expect(screen.getByText("AI").closest("a")).toHaveAttribute("href", "/projects/3");
    expect(screen.getByText("the good stuff")).toBeInTheDocument();
    const ai = screen.getByText("AI").closest("a")!;
    expect(ai.textContent).toContain("2 articles");
    const solo = screen.getByText("Solo").closest("a")!;
    expect(solo.textContent).toContain("1 article");
    expect(solo.textContent).toContain("1 member");
  });

  it("shows an unseen badge on projects with new shared articles", () => {
    swrMock.mockReturnValue({
      data: [makeProject({ id: 3, name: "AI", unseen_count: 4 })],
      isLoading: false,
    });
    render(<ProjectsPage />);
    expect(screen.getByText("4")).toBeInTheDocument();
  });

  it("creates a project and revalidates", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    swrMock.mockReturnValue({ data: [], isLoading: false });
    render(<ProjectsPage />);

    await userEvent.click(screen.getByRole("button", { name: /New project/ }));
    await userEvent.type(screen.getByPlaceholderText("Project name"), " Reading Club ");
    await userEvent.type(screen.getByPlaceholderText(/What is this project about/), " books ");
    await userEvent.click(screen.getByRole("button", { name: /Create project/ }));

    await waitFor(() => expect(mutateMock).toHaveBeenCalledWith("/projects"));
    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/projects"))!;
    expect(JSON.parse(call[1].body)).toEqual({ name: "Reading Club", description: "books" });
    // form closed again
    expect(screen.queryByPlaceholderText("Project name")).not.toBeInTheDocument();
  });

  it("toggle button flips between New project and Cancel", async () => {
    swrMock.mockReturnValue({ data: [], isLoading: false });
    render(<ProjectsPage />);
    await userEvent.click(screen.getByRole("button", { name: /New project/ }));
    expect(screen.getByRole("button", { name: /Cancel/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Cancel/ }));
    expect(screen.queryByPlaceholderText("Project name")).not.toBeInTheDocument();
    // empty state suppressed while the form is open? closed again now:
    expect(screen.getByText("No projects yet.")).toBeInTheDocument();
  });

  it("shows the API error and keeps the form open on failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 400,
        ok: false,
        json: async () => ({ detail: "Too many projects" }),
      }),
    );
    swrMock.mockReturnValue({ data: [], isLoading: false });
    render(<ProjectsPage />);
    await userEvent.click(screen.getByRole("button", { name: /New project/ }));
    await userEvent.type(screen.getByPlaceholderText("Project name"), "X");
    await userEvent.click(screen.getByRole("button", { name: /Create project/ }));
    expect(await screen.findByText("Too many projects")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Project name")).toBeInTheDocument();
  });

  it("falls back to a generic error message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue("boom"));
    swrMock.mockReturnValue({ data: [], isLoading: false });
    render(<ProjectsPage />);
    await userEvent.click(screen.getByRole("button", { name: /New project/ }));
    await userEvent.type(screen.getByPlaceholderText("Project name"), "X");
    await userEvent.click(screen.getByRole("button", { name: /Create project/ }));
    expect(await screen.findByText("Could not create project")).toBeInTheDocument();
  });
});
