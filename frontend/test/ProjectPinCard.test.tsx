import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ProjectPinCard, { groupPins } from "@/components/ProjectPinCard";
import { makeArticle, makeProjectArticle, makePublic } from "./fixtures";

const { pushMock, mutateMock } = vi.hoisted(() => ({ pushMock: vi.fn(), mutateMock: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }));
vi.mock("swr", () => ({ mutate: mutateMock }));

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

  it("renders each pin's note, attributed when there are several adders", () => {
    renderCard([
      makeProjectArticle({ id: 1, added_by: me, note: "my take" }),
      makeProjectArticle({ id: 2, added_by: bob, note: "bob's take" }),
    ]);
    expect(screen.getByText(/my take/)).toBeInTheDocument();
    expect(screen.getByText(/bob's take/)).toBeInTheDocument();
    expect(screen.getByText("— @bob")).toBeInTheDocument();
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
      makeProjectArticle({ id: 7, added_by: me, is_shared: false, shared_at: null, note: "n" }),
    ]);

    await userEvent.click(screen.getByRole("button", { name: /Share with project/ }));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText(/Members of AI Research will see this and your note/)).toBeInTheDocument();

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
    // note-less confirm copy has no "and your note"
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
});
