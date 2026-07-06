import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ShareModal from "@/components/ShareModal";
import { makeArticle, makePublic } from "./fixtures";

const { mutateMock } = vi.hoisted(() => ({ mutateMock: vi.fn() }));
vi.mock("swr", () => ({ mutate: mutateMock }));

const bob = makePublic({ id: 2, username: "bob", name: "Bob" });
const cara = makePublic({ id: 3, username: "cara", name: "Cara" });

// fetch that routes user-search vs. /shares POST
function makeFetch(opts: {
  users?: unknown[];
  shares?: () => Promise<{ status: number; ok: boolean; json: () => Promise<unknown> }>;
} = {}) {
  const users = opts.users ?? [bob, cara];
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes("/users/search")) {
      return Promise.resolve({ status: 200, ok: true, json: async () => users });
    }
    if (opts.shares) return opts.shares();
    return Promise.resolve({ status: 200, ok: true, json: async () => ({ id: 1 }) });
  });
}

async function addBob() {
  const input = screen.getByPlaceholderText(/who should read this/);
  await userEvent.type(input, "@bo");
  const option = await screen.findByText("Bob");
  await userEvent.click(option);
}

describe("<ShareModal>", () => {
  beforeEach(() => {
    mutateMock.mockClear();
  });

  it("renders the article title and an empty initial state", () => {
    vi.stubGlobal("fetch", makeFetch());
    render(<ShareModal article={makeArticle({ title: "My Story" })} onClose={vi.fn()} />);
    expect(screen.getByText("My Story")).toBeInTheDocument();
    expect(screen.getByText("Add at least one reader")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Send/ })).toBeDisabled();
  });

  it("closes on the X button", async () => {
    vi.stubGlobal("fetch", makeFetch());
    const onClose = vi.fn();
    const { container } = render(<ShareModal article={makeArticle()} onClose={onClose} />);
    const xBtn = container.querySelector("button.icon-btn")!;
    await userEvent.click(xBtn);
    expect(onClose).toHaveBeenCalled();
  });

  it("closes on Escape and clicking the backdrop, but not the panel", async () => {
    vi.stubGlobal("fetch", makeFetch());
    const onClose = vi.fn();
    const { container } = render(<ShareModal article={makeArticle()} onClose={onClose} />);

    // clicking the inner panel does not close
    await userEvent.click(screen.getByText("A Great Article"));
    expect(onClose).not.toHaveBeenCalled();

    // Escape closes
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);

    // a non-Escape key does nothing
    await userEvent.keyboard("a");
    expect(onClose).toHaveBeenCalledTimes(1);

    // clicking the backdrop closes
    await userEvent.click(container.firstChild as Element);
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("searches users (debounced), strips the @, and adds a recipient", async () => {
    const fetchMock = makeFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);

    const input = screen.getByPlaceholderText(/who should read this/);
    await userEvent.type(input, "@bo");
    await screen.findByText("Bob");

    // the @ was stripped before hitting the API
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/users/search?q=bo"))).toBe(true);

    await userEvent.click(screen.getByText("Bob"));

    // recipient chip appears, query & results cleared
    expect(screen.getByText("@bob")).toBeInTheDocument();
    expect((input as HTMLInputElement).value).toBe("");
    expect(screen.queryByText("Cara")).not.toBeInTheDocument();
    expect(screen.getByText("1 reader")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Send/ })).toBeEnabled();
  });

  it("excludes already-added recipients from results and pluralises the count", async () => {
    vi.stubGlobal("fetch", makeFetch());
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);

    await addBob();

    // search again: bob is filtered out, cara remains
    const input = screen.getByPlaceholderText(/who should read this/);
    await userEvent.type(input, "ca");
    await screen.findByText("Cara");
    expect(screen.queryByText("Bob")).not.toBeInTheDocument();

    await userEvent.click(screen.getByText("Cara"));
    expect(screen.getByText("2 readers")).toBeInTheDocument();
  });

  it("removes a recipient", async () => {
    vi.stubGlobal("fetch", makeFetch());
    const { container } = render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);

    await addBob();
    const chip = screen.getByText("@bob").closest("span")!;
    const removeBtn = chip.querySelector("button")!;
    await userEvent.click(removeBtn);

    expect(screen.queryByText("@bob")).not.toBeInTheDocument();
    expect(screen.getByText("Add at least one reader")).toBeInTheDocument();
  });

  it("clears results when the query is emptied", async () => {
    vi.stubGlobal("fetch", makeFetch());
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);

    const input = screen.getByPlaceholderText(/who should read this/);
    await userEvent.type(input, "bo");
    await screen.findByText("Bob");
    await userEvent.clear(input);
    await waitFor(() => expect(screen.queryByText("Bob")).not.toBeInTheDocument());
  });

  it("clears results when a search request fails", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/users/search")) return Promise.reject(new Error("network"));
      return Promise.resolve({ status: 200, ok: true, json: async () => ({}) });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);

    const input = screen.getByPlaceholderText(/who should read this/);
    await userEvent.type(input, "bo");
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    // no results ever render
    expect(screen.queryByText("Bob")).not.toBeInTheDocument();
  });

  it("submits the share with a trimmed note and closes after the confirmation", async () => {
    const fetchMock = makeFetch();
    vi.stubGlobal("fetch", fetchMock);
    const onClose = vi.fn();
    render(<ShareModal article={makeArticle({ id: 7 })} onClose={onClose} />);

    await addBob();
    await userEvent.type(screen.getByPlaceholderText(/Why are you sharing/), "  read this  ");
    await userEvent.click(screen.getByRole("button", { name: /Send/ }));

    await screen.findByText("Shared.");
    const postCall = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/shares"))!;
    const body = JSON.parse(postCall[1].body);
    expect(body).toEqual({ article_id: 7, recipients: ["bob"], note: "read this" });
    expect(mutateMock).toHaveBeenCalledWith("/shares/sent");

    await waitFor(() => expect(onClose).toHaveBeenCalled(), { timeout: 2000 });
  });

  it("sends a null note when the note is blank", async () => {
    const fetchMock = makeFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);

    await addBob();
    await userEvent.click(screen.getByRole("button", { name: /Send/ }));
    await screen.findByText("Shared.");

    const postCall = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/shares"))!;
    expect(JSON.parse(postCall[1].body).note).toBeNull();
  });

  it("shows the API error message when the share fails", async () => {
    vi.stubGlobal(
      "fetch",
      makeFetch({
        shares: () =>
          Promise.resolve({ status: 400, ok: false, json: async () => ({ detail: "Blocked" }) }),
      }),
    );
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);

    await addBob();
    await userEvent.click(screen.getByRole("button", { name: /Send/ }));

    expect(await screen.findByText("Blocked")).toBeInTheDocument();
    // busy reset -> button enabled again
    expect(screen.getByRole("button", { name: /Send/ })).toBeEnabled();
  });

  it("falls back to a generic message when the failure is not an Error", async () => {
    vi.stubGlobal(
      "fetch",
      makeFetch({ shares: () => Promise.reject("boom") as never }),
    );
    render(<ShareModal article={makeArticle()} onClose={vi.fn()} />);

    await addBob();
    await userEvent.click(screen.getByRole("button", { name: /Send/ }));

    expect(await screen.findByText("Could not share")).toBeInTheDocument();
  });
});
