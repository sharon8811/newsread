import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ImportUrlModal from "@/components/ImportUrlModal";
import { makeArticleDetail } from "./fixtures";

const { pushMock, mutateListsMock } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  mutateListsMock: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }));
vi.mock("@/components/ArticleList", () => ({ mutateArticleLists: mutateListsMock }));

function jsonResponse(body: unknown, status = 201) {
  return { status, ok: status < 400, json: async () => body };
}

describe("<ImportUrlModal>", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let onClose: () => void;

  beforeEach(() => {
    pushMock.mockClear();
    mutateListsMock.mockClear();
    onClose = vi.fn<() => void>();
    fetchMock = vi.fn().mockResolvedValue(jsonResponse(makeArticleDetail({ id: 42 })));
    vi.stubGlobal("fetch", fetchMock);
  });

  it("renders the form with a disabled submit until a URL is typed", () => {
    render(<ImportUrlModal onClose={onClose} />);
    expect(screen.getByText("Add a link")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();
  });

  it("posts the URL, refreshes lists, closes, and opens the article", async () => {
    render(<ImportUrlModal onClose={onClose} />);
    await userEvent.type(
      screen.getByPlaceholderText("https://example.com/article"),
      "  https://site.example/story  ",
    );
    await userEvent.click(screen.getByRole("button", { name: "Import" }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/article/42"));
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/imports");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      url: "https://site.example/story",
    });
    expect(mutateListsMock).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("surfaces the API error and stays open", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: "Enter a public http(s) page URL" }, 400),
    );
    render(<ImportUrlModal onClose={onClose} />);
    await userEvent.type(
      screen.getByPlaceholderText("https://example.com/article"),
      "http://10.0.0.5/x",
    );
    await userEvent.click(screen.getByRole("button", { name: "Import" }));

    expect(await screen.findByText("Enter a public http(s) page URL")).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("shows a busy label while the request is in flight", async () => {
    let resolve!: (value: unknown) => void;
    fetchMock.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<ImportUrlModal onClose={onClose} />);
    await userEvent.type(
      screen.getByPlaceholderText("https://example.com/article"),
      "https://site.example/a",
    );
    await userEvent.click(screen.getByRole("button", { name: "Import" }));
    expect(screen.getByRole("button", { name: "Importing…" })).toBeDisabled();
    resolve(jsonResponse(makeArticleDetail({ id: 7 })));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/article/7"));
  });

  it("closes from the header close button", async () => {
    render(<ImportUrlModal onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });
});
