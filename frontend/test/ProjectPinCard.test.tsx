import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ProjectPinCard, { groupPins } from "@/components/ProjectPinCard";
import { makeArticle, makeProjectArticle, makeProjectComment, makePublic } from "./fixtures";

const { pushMock, mutateMock, swrMock } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  mutateMock: vi.fn(),
  swrMock: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }));
vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));

const me = makePublic({ id: 1, username: "alice", name: "Alice" });
const bob = makePublic({ id: 2, username: "bob", name: "Bob" });

function okFetch() {
  return vi.fn().mockResolvedValue({ status: 204, ok: true, json: async () => ({}) });
}

function renderCard(pins: Parameters<typeof ProjectPinCard>[0]["pins"], props = {}) {
  return render(
    <ProjectPinCard pins={pins} myId={1} isOwner={false} projectName="AI Research" {...props} />,
  );
}

describe("groupPins", () => {
  it("groups shared pins of the same article and keeps private pins separate", () => {
    const sharedA1 = makeProjectArticle({ id: 1, article: makeArticle({ id: 10 }) });
    const sharedA2 = makeProjectArticle({ id: 2, article: makeArticle({ id: 10 }), added_by: bob });
    const sharedB = makeProjectArticle({ id: 3, article: makeArticle({ id: 20 }) });
    const privateA = makeProjectArticle({
      id: 4,
      article: makeArticle({ id: 10 }),
      is_shared: false,
      shared_at: null,
    });
    const groups = groupPins([sharedA1, privateA, sharedA2, sharedB]);
    expect(groups).toEqual([[sharedA1, sharedA2], [privateA], [sharedB]]);
  });
});

describe("<ProjectPinCard>", () => {
  beforeEach(() => {
    pushMock.mockClear();
    mutateMock.mockClear();
    swrMock.mockReset();
    swrMock.mockReturnValue({ data: undefined }); // thread not loaded
    vi.stubGlobal("fetch", okFetch());
  });

  it("shows You for the viewer's pin and the Only you chip when private", () => {
    renderCard([makeProjectArticle({ added_by: me, is_shared: false, shared_at: null })]);
    expect(screen.getByText("You")).toBeInTheDocument();
    expect(screen.getByText("Only you")).toBeInTheDocument();
  });

  it("shows adder names without the private chip when shared", () => {
    renderCard([makeProjectArticle({ added_by: bob })]);
    expect(screen.getByText("Bob")).toBeInTheDocument();
    expect(screen.queryByText("Only you")).not.toBeInTheDocument();
  });

  it("shows the Done chip with attribution when the ticket is done", () => {
    renderCard([
      makeProjectArticle({ added_by: bob, status: "done", status_updated_by: bob }),
    ]);
    const chip = screen.getByTitle("Marked done by @bob");
    expect(chip).toHaveTextContent("Done");
    expect(screen.getByLabelText("Status")).toHaveValue("done");
  });

  it("opens the article on card click", async () => {
    renderCard([makeProjectArticle({ article: makeArticle({ id: 5, title: "Story" }) })]);
    await userEvent.click(screen.getByText("Story"));
    expect(pushMock).toHaveBeenCalledWith("/article/5");
  });

  it("publishes a private pin only after the inline confirm", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderCard([
      makeProjectArticle({ id: 7, added_by: me, is_shared: false, shared_at: null }),
    ]);

    await userEvent.click(screen.getByRole("button", { name: /Share with project/ }));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText(/Members of AI Research will see this\./)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Share" }));
    await waitFor(() => expect(mutateMock).toHaveBeenCalledWith("/projects/1/articles"));
    const call = fetchMock.mock.calls[0];
    expect(String(call[0])).toContain("/projects/1/articles/7");
    expect(JSON.parse(call[1].body)).toEqual({ is_shared: true });
    expect(mutateMock).toHaveBeenCalledWith("/projects");
    expect(mutateMock).toHaveBeenCalledWith("/projects/1");
  });

  it("cancel backs out of the publish confirm", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderCard([makeProjectArticle({ added_by: me, is_shared: false, shared_at: null })]);
    await userEvent.click(screen.getByRole("button", { name: /Share with project/ }));
    expect(screen.getByText(/Members of AI Research will see this\./)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Share with project/ })).toBeInTheDocument();
  });

  it("makes a shared pin private again", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderCard([makeProjectArticle({ id: 7, added_by: me })]);
    await userEvent.click(screen.getByRole("button", { name: /Make private/ }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ is_shared: false });
  });

  it("removes the article with one by-article call", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderCard([makeProjectArticle({ id: 7, added_by: me, article: makeArticle({ id: 5 }) })]);
    await userEvent.click(screen.getByTitle("Remove from project"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, opts] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/projects/1/articles/by-article/5");
    expect(opts.method).toBe("DELETE");
  });

  it("owner removes a multi-adder group with a single call", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderCard(
      [
        makeProjectArticle({ id: 7, added_by: bob }),
        makeProjectArticle({ id: 8, added_by: makePublic({ id: 3, username: "c", name: "C" }) }),
      ],
      { isOwner: true },
    );
    await userEvent.click(screen.getByTitle("Remove from project"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(String(fetchMock.mock.calls[0][0])).toContain("/articles/by-article/");
  });

  it("offers no actions to a member on someone else's shared pin", () => {
    renderCard([makeProjectArticle({ added_by: bob })]);
    expect(screen.queryByTitle("Remove from project")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Make private/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Share with project/ })).not.toBeInTheDocument();
  });

  it("surfaces API errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("nope")));
    renderCard([makeProjectArticle({ id: 7, added_by: me })]);
    await userEvent.click(screen.getByTitle("Remove from project"));
    expect(await screen.findByText("nope")).toBeInTheDocument();
  });

  it("falls back to a generic error message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue("boom"));
    renderCard([makeProjectArticle({ id: 7, added_by: me })]);
    await userEvent.click(screen.getByTitle("Remove from project"));
    expect(await screen.findByText("Something went wrong")).toBeInTheDocument();
  });

  // --- ticket status ---

  it("changing the status dropdown arms the form instead of applying", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderCard([makeProjectArticle({ added_by: bob })]);
    await userEvent.selectOptions(screen.getByLabelText("Status"), "done");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByPlaceholderText(/Optional closing note/)).toBeInTheDocument();
  });

  it("marks done with the note and link in one PUT", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderCard([makeProjectArticle({ added_by: bob, article: makeArticle({ id: 5 }) })]);
    await userEvent.selectOptions(screen.getByLabelText("Status"), "done");
    await userEvent.type(screen.getByPlaceholderText(/Optional closing note/), "merged");
    await userEvent.type(
      screen.getByPlaceholderText(/Optional link/),
      "https://github.com/o/r/pull/7",
    );
    await userEvent.click(screen.getByRole("button", { name: "Mark done" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, opts] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/projects/1/articles/by-article/5/status");
    expect(opts.method).toBe("PUT");
    expect(JSON.parse(opts.body)).toEqual({
      status: "done",
      comment: "merged",
      link_url: "https://github.com/o/r/pull/7",
    });
    expect(mutateMock).toHaveBeenCalledWith("/projects/1/articles");
    expect(mutateMock).toHaveBeenCalledWith("/projects/1/articles/by-article/5/comments");
  });

  it("reopens a done ticket without a note", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderCard([
      makeProjectArticle({ added_by: bob, status: "done", article: makeArticle({ id: 5 }) }),
    ]);
    await userEvent.selectOptions(screen.getByLabelText("Status"), "open");
    await userEvent.click(screen.getByRole("button", { name: "Reopen" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      status: "open",
      comment: null,
      link_url: null,
    });
  });

  it("cancel disarms the status form", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderCard([makeProjectArticle({ added_by: bob })]);
    await userEvent.selectOptions(screen.getByLabelText("Status"), "done");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByPlaceholderText(/Optional closing note/)).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // --- comment thread ---

  it("expands the thread and renders comments with link chips", async () => {
    swrMock.mockImplementation((key: string | null) =>
      key
        ? {
            data: [
              makeProjectComment({ id: 1, author: me, body: "my take" }),
              makeProjectComment({
                id: 2,
                author: bob,
                body: "wrapped up",
                link_url: "https://github.com/o/repo/pull/42",
              }),
            ],
          }
        : { data: undefined },
    );
    renderCard([makeProjectArticle({ added_by: bob, comment_count: 2 })]);
    // collapsed: count shown, thread not fetched
    expect(swrMock).toHaveBeenCalledWith(null, expect.anything());
    await userEvent.click(screen.getByRole("button", { name: "2" }));
    expect(screen.getByText("my take")).toBeInTheDocument();
    expect(screen.getByText("wrapped up")).toBeInTheDocument();
    const chip = screen.getByRole("link", { name: /repo#42/ });
    expect(chip).toHaveAttribute("href", "https://github.com/o/repo/pull/42");
  });

  it("posts a comment with an attached link", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    swrMock.mockImplementation((key: string | null) => ({ data: key ? [] : undefined }));
    renderCard([makeProjectArticle({ added_by: bob, article: makeArticle({ id: 5 }) })]);
    await userEvent.click(screen.getByRole("button", { name: "Comment" }));
    expect(screen.getByText("No comments yet — start the thread.")).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText("Add a comment…"), "on it");
    await userEvent.click(screen.getByTitle("Attach a link"));
    await userEvent.type(
      screen.getByPlaceholderText(/link to attach/),
      "https://youtu.be/x",
    );
    await userEvent.click(screen.getByRole("button", { name: "Post" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, opts] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/projects/1/articles/by-article/5/comments");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body)).toEqual({ body: "on it", link_url: "https://youtu.be/x" });
  });

  it("posts a comment on Enter", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    swrMock.mockImplementation((key: string | null) => ({ data: key ? [] : undefined }));
    renderCard([makeProjectArticle({ added_by: bob })]);
    await userEvent.click(screen.getByRole("button", { name: "Comment" }));
    await userEvent.type(screen.getByPlaceholderText("Add a comment…"), "quick note{Enter}");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      body: "quick note",
      link_url: null,
    });
  });

  it("lets the author delete their comment but not others'", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    swrMock.mockImplementation((key: string | null) =>
      key
        ? {
            data: [
              makeProjectComment({ id: 1, author: me, body: "mine" }),
              makeProjectComment({ id: 2, author: bob, body: "bob's" }),
            ],
          }
        : { data: undefined },
    );
    renderCard([makeProjectArticle({ added_by: bob, comment_count: 2 })]);
    await userEvent.click(screen.getByRole("button", { name: "2" }));
    // only the viewer's own comment offers delete (non-owner viewer)
    expect(screen.getAllByTitle("Delete comment")).toHaveLength(1);
    await userEvent.click(screen.getByTitle("Delete comment"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, opts] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/projects/1/comments/1");
    expect(opts.method).toBe("DELETE");
  });

  it("owner can delete any comment", async () => {
    swrMock.mockImplementation((key: string | null) =>
      key ? { data: [makeProjectComment({ id: 2, author: bob, body: "bob's" })] } : { data: undefined },
    );
    renderCard([makeProjectArticle({ added_by: bob, comment_count: 1 })], { isOwner: true });
    await userEvent.click(screen.getByRole("button", { name: "1" }));
    expect(screen.getByTitle("Delete comment")).toBeInTheDocument();
  });
});
