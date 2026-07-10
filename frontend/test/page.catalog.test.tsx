import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CatalogPage from "@/app/(app)/catalog/page";
import { makeCatalogEntry, makeFeed } from "./fixtures";
import type { CatalogEntry } from "@/lib/api";

const { swrMock, mutateMock, apiMock } = vi.hoisted(() => ({
  swrMock: vi.fn(),
  mutateMock: vi.fn(),
  apiMock: vi.fn(),
}));

vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));
vi.mock("@/lib/api", () => ({ api: apiMock, fetcher: vi.fn() }));

const CATEGORIES = [
  { name: "Food", count: 1 },
  { name: "Tech", count: 2 },
];

function setSwr(entries: CatalogEntry[] | undefined) {
  swrMock.mockImplementation((key: string) => {
    if (key === "/catalog/categories") return { data: CATEGORIES };
    return { data: entries };
  });
}

function catalogKeys(): string[] {
  return swrMock.mock.calls.map((c) => c[0]).filter((k) => !k.includes("categories"));
}

describe("CatalogPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setSwr([makeCatalogEntry()]);
  });

  it("renders entries with title, category, and description", () => {
    render(<CatalogPage />);
    expect(screen.getByText("Example Blog")).toBeInTheDocument();
    expect(screen.getByText("A blog about examples")).toBeInTheDocument();
    // The chip row and the card badge both say "Tech".
    expect(screen.getAllByText("Tech").length).toBeGreaterThanOrEqual(2);
    expect(swrMock).toHaveBeenCalledWith("/catalog", expect.anything());
  });

  it("shows category chips with counts and filters on click", async () => {
    render(<CatalogPage />);
    await userEvent.click(screen.getByRole("button", { name: /Food/ }));
    await waitFor(() =>
      expect(catalogKeys()).toContain("/catalog?category=Food"),
    );
    // Clicking the active chip clears the filter.
    await userEvent.click(screen.getByRole("button", { name: /Food/ }));
    await waitFor(() => expect(catalogKeys().at(-1)).toBe("/catalog"));
  });

  it("debounces search into the swr key", async () => {
    render(<CatalogPage />);
    await userEvent.type(screen.getByPlaceholderText("Search feeds…"), "vim");
    await waitFor(
      () => expect(catalogKeys()).toContain("/catalog?q=vim"),
      { timeout: 1000 },
    );
  });

  it("subscribes via POST /feeds and flips the entry optimistically", async () => {
    const entry = makeCatalogEntry({ url: "https://sub.example/rss" });
    setSwr([entry]);
    apiMock.mockResolvedValue(makeFeed({ id: 42 }));

    render(<CatalogPage />);
    await userEvent.click(screen.getByRole("button", { name: /Subscribe/ }));

    await waitFor(() =>
      expect(apiMock).toHaveBeenCalledWith("/feeds", {
        method: "POST",
        body: { url: "https://sub.example/rss" },
      }),
    );
    expect(mutateMock).toHaveBeenCalledWith("/feeds");
    // The optimistic updater marks exactly this entry subscribed.
    const optimistic = mutateMock.mock.calls.find(
      (c) => c[0] === "/catalog" && typeof c[1] === "function",
    );
    expect(optimistic).toBeTruthy();
    const updated = optimistic![1]([entry, makeCatalogEntry({ id: 2, url: "https://x/rss" })]);
    expect(updated[0]).toMatchObject({ subscribed: true, feed_id: 42 });
    expect(updated[1].subscribed).toBe(false);
  });

  it("renders subscribed entries as a link to the feed", () => {
    setSwr([makeCatalogEntry({ subscribed: true, feed_id: 7 })]);
    render(<CatalogPage />);
    const link = screen.getByRole("link", { name: /Subscribed/ });
    expect(link).toHaveAttribute("href", "/?feed=7");
    expect(screen.queryByRole("button", { name: /^Subscribe/ })).not.toBeInTheDocument();
  });

  it("shows an error when subscribing fails", async () => {
    apiMock.mockRejectedValue(new Error("Could not fetch or parse a feed at that URL"));
    render(<CatalogPage />);
    await userEvent.click(screen.getByRole("button", { name: /Subscribe/ }));
    expect(
      await screen.findByText(/Could not subscribe to Example Blog: Could not fetch/),
    ).toBeInTheDocument();
  });

  it("shows an empty state when nothing matches", () => {
    setSwr([]);
    render(<CatalogPage />);
    expect(screen.getByText(/No feeds match/)).toBeInTheDocument();
  });
});
