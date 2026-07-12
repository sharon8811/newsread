import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CatalogPage from "@/app/(app)/catalog/page";
import { makeCatalogEntry, makeCatalogPreview, makeFeed, makeSmartFeed } from "./fixtures";
import type { CatalogEntry, CatalogPreview, SmartFeed, SmartFeedResolve } from "@/lib/api";

const { swrMock, mutateMock, apiMock } = vi.hoisted(() => ({
  swrMock: vi.fn(),
  mutateMock: vi.fn(),
  apiMock: vi.fn(),
}));

vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: apiMock,
  fetcher: vi.fn(),
}));

const CATEGORIES = [
  { name: "Food", count: 1 },
  { name: "Tech", count: 2 },
];

function setSwr(
  entries: CatalogEntry[] | undefined,
  categories = CATEGORIES,
  state: { error?: Error; isLoading?: boolean } = {},
  preview: { data?: CatalogPreview; error?: Error; isLoading?: boolean } = {},
  smart: SmartFeed[] = [],
) {
  swrMock.mockImplementation((key: string | null) => {
    if (key === null) return {};
    if (key === "/catalog/categories") return { data: categories };
    if (key === "/catalog/smart") return { data: smart };
    if (key.endsWith("/preview")) return preview;
    return { data: entries, ...state };
  });
}

function catalogKeys(): string[] {
  return swrMock.mock.calls
    .map((c) => c[0])
    .filter((k) => k && !k.includes("categories") && k !== "/catalog/smart");
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
    expect(screen.getByText(/example.com/)).toBeInTheDocument();
    expect(screen.getByText("12 recent items")).toBeInTheDocument();
    // The chip row and the card badge both say "Tech".
    expect(screen.getAllByText("Tech").length).toBeGreaterThanOrEqual(2);
    expect(swrMock).toHaveBeenCalledWith("/catalog", expect.anything());
  });

  it("renders feed types, previews, freshness, match reasons, and singular counts", () => {
    const now = Date.now();
    setSwr([
      makeCatalogEntry({
        id: 1,
        title: "Atom Feed",
        content_type: "application/atom+xml",
        item_count: 1,
        subscriber_count: 1,
        latest_item_at: new Date(now).toISOString(),
        match_reason: "Semantic match",
        preview_items: [{ title: "Latest story", url: "https://x/1", published_at: null }],
      }),
      makeCatalogEntry({
        id: 2,
        title: "JSON Feed",
        content_type: "application/feed+json",
        latest_item_at: new Date(now - 45 * 86_400_000).toISOString(),
      }),
      makeCatalogEntry({
        id: 3,
        title: "Old RSS",
        content_type: null,
        latest_item_at: new Date(now - 400 * 86_400_000).toISOString(),
      }),
    ]);
    render(<CatalogPage />);
    expect(screen.getByText(/example.com · Atom/)).toBeInTheDocument();
    expect(screen.getByText(/example.com · JSON Feed/)).toBeInTheDocument();
    expect(screen.getByText("1 recent item")).toBeInTheDocument();
    expect(screen.getByText("1 reader")).toBeInTheDocument();
    expect(screen.getByText("Semantic match")).toBeInTheDocument();
    expect(screen.getByText(/Updated 1 month ago/)).toBeInTheDocument();
    expect(screen.getByText(/Updated 1 year ago/)).toBeInTheDocument();
  });

  it("shows loading and load errors", () => {
    setSwr(undefined, CATEGORIES, { isLoading: true });
    const { rerender } = render(<CatalogPage />);
    expect(screen.getByLabelText("Loading catalog")).toBeInTheDocument();
    setSwr(undefined, CATEGORIES, { error: new Error("offline") });
    rerender(<CatalogPage />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load the catalog");
  });

  it("switches to personalized and popularity ranking", async () => {
    render(<CatalogPage />);
    await userEvent.click(screen.getByRole("button", { name: "For you" }));
    await waitFor(() => expect(catalogKeys()).toContain("/catalog?sort=recommended"));
    await userEvent.click(screen.getByRole("button", { name: "Popular" }));
    await waitFor(() => expect(catalogKeys()).toContain("/catalog?sort=popular"));
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

  it("expands topics and incrementally renders a large catalog", async () => {
    const manyCategories = Array.from({ length: 14 }, (_, index) => ({ name: `Topic ${index}`, count: 1 }));
    const manyEntries = Array.from({ length: 61 }, (_, index) => makeCatalogEntry({ id: index + 1, title: `Feed ${index + 1}` }));
    setSwr(manyEntries, manyCategories);
    render(<CatalogPage />);
    expect(screen.getAllByRole("article")).toHaveLength(60);
    await userEvent.click(screen.getByRole("button", { name: "Load more feeds" }));
    expect(screen.getAllByRole("article")).toHaveLength(61);
    await userEvent.click(screen.getByRole("button", { name: "More topics (2)" }));
    expect(screen.getByRole("button", { name: "Fewer topics" })).toBeInTheDocument();
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

  it("validates and submits a suggested feed", async () => {
    apiMock.mockResolvedValue({ id: 1, status: "pending" });
    render(<CatalogPage />);
    await userEvent.click(screen.getByRole("button", { name: /Suggest feed/ }));
    await userEvent.type(screen.getByLabelText("Feed URL"), "https://new.example/rss");
    await userEvent.click(screen.getByRole("button", { name: "Submit" }));
    await waitFor(() => expect(apiMock).toHaveBeenCalledWith("/catalog/submissions", {
      method: "POST",
      body: { url: "https://new.example/rss", category: null },
    }));
    expect(await screen.findByText(/queued for review/)).toBeInTheDocument();
  });

  it("shows a suggested-feed validation error", async () => {
    apiMock.mockRejectedValue(new Error("Private network feed URLs are not allowed"));
    render(<CatalogPage />);
    await userEvent.click(screen.getByRole("button", { name: /Suggest feed/ }));
    await userEvent.type(screen.getByLabelText("Feed URL"), "http://127.0.0.1/feed");
    await userEvent.click(screen.getByRole("button", { name: "Submit" }));
    expect(await screen.findByText(/Private network feed URLs/)).toBeInTheDocument();
  });
});

describe("catalog detail modal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  async function openModal() {
    await userEvent.click(screen.getByRole("button", { name: "Example Blog" }));
    return screen.getByRole("dialog", { name: "Example Blog" });
  }

  it("opens from the card and shows metadata plus live stories", async () => {
    setSwr([makeCatalogEntry()], CATEGORIES, {}, { data: makeCatalogPreview() });
    render(<CatalogPage />);
    const dialog = await openModal();

    expect(swrMock).toHaveBeenCalledWith(
      "/catalog/1/preview",
      expect.anything(),
      expect.objectContaining({ shouldRetryOnError: false }),
    );
    expect(within(dialog).getByText(/Tech · RSS/)).toBeInTheDocument();
    expect(within(dialog).getByText("A blog about examples")).toBeInTheDocument();
    expect(within(dialog).getByText("12 recent items")).toBeInTheDocument();
    expect(within(dialog).getByText("Healthy")).toBeInTheDocument();
    expect(within(dialog).getByText("https://blog.example/rss")).toBeInTheDocument();
    const site = within(dialog).getByRole("link", { name: /example.com/ });
    expect(site).toHaveAttribute("href", "https://blog.example");
    const story = within(dialog).getByRole("link", { name: /Fresh story/ });
    expect(story).toHaveAttribute("href", "https://blog.example/fresh");
    expect(within(dialog).getByText("A short plain-text summary of the story.")).toBeInTheDocument();
    expect(within(dialog).getByText("Undated story")).toBeInTheDocument();
  });

  it("shows a loading skeleton, then an empty-stories message", () => {
    setSwr([makeCatalogEntry()], CATEGORIES, {}, { isLoading: true });
    const { rerender } = render(<CatalogPage />);
    fireEvent.click(screen.getByRole("button", { name: "Example Blog" }));
    expect(screen.getByLabelText("Loading stories")).toBeInTheDocument();

    setSwr([makeCatalogEntry()], CATEGORIES, {}, { data: makeCatalogPreview({ items: [] }) });
    rerender(<CatalogPage />);
    expect(screen.getByText("This feed has no stories right now.")).toBeInTheDocument();
  });

  it("falls back to the cached snapshot when the live preview fails", async () => {
    const entry = makeCatalogEntry({
      preview_items: [{ title: "Cached story", url: "https://x/1", published_at: "2026-07-10T12:00:00Z" }],
    });
    setSwr([entry], CATEGORIES, {}, { error: new Error("bad gateway") });
    render(<CatalogPage />);
    const dialog = await openModal();
    expect(within(dialog).getByText(/showing a recent snapshot/)).toBeInTheDocument();
    expect(within(dialog).getByText("Cached story")).toBeInTheDocument();
  });

  it("shows an error when the live preview fails without a snapshot", async () => {
    setSwr([makeCatalogEntry()], CATEGORIES, {}, { error: new Error("bad gateway") });
    render(<CatalogPage />);
    const dialog = await openModal();
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Could not load stories");
  });

  it("subscribes from the modal and flips to a feed link in place", async () => {
    const entry = makeCatalogEntry();
    setSwr([entry], CATEGORIES, {}, { data: makeCatalogPreview() });
    apiMock.mockResolvedValue(makeFeed({ id: 42 }));
    render(<CatalogPage />);
    const dialog = await openModal();

    await userEvent.click(within(dialog).getByRole("button", { name: /Subscribe/ }));
    await waitFor(() =>
      expect(apiMock).toHaveBeenCalledWith("/feeds", {
        method: "POST",
        body: { url: entry.url },
      }),
    );

    // Once the SWR cache reflects the subscription, the open modal follows.
    setSwr([{ ...entry, subscribed: true, feed_id: 42 }], CATEGORIES, {}, { data: makeCatalogPreview() });
    await userEvent.click(screen.getByRole("button", { name: "Popular" })); // trigger re-render
    expect(within(screen.getByRole("dialog")).getByRole("link", { name: /View feed/ })).toHaveAttribute(
      "href",
      "/?feed=42",
    );
  });

  it("surfaces subscribe failures inside the modal", async () => {
    setSwr([makeCatalogEntry()], CATEGORIES, {}, { data: makeCatalogPreview() });
    apiMock.mockRejectedValue(new Error("boom"));
    render(<CatalogPage />);
    const dialog = await openModal();
    await userEvent.click(within(dialog).getByRole("button", { name: /Subscribe/ }));
    expect(await within(dialog).findByText(/Could not subscribe to Example Blog: boom/)).toBeInTheDocument();
  });

  it("closes via the close button, Escape, and the scrim", async () => {
    setSwr([makeCatalogEntry()], CATEGORIES, {}, { data: makeCatalogPreview() });
    render(<CatalogPage />);

    let dialog = await openModal();
    await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    dialog = await openModal();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    dialog = await openModal();
    fireEvent.click(dialog.parentElement!);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders linkless stories as plain text, not dead anchors", async () => {
    setSwr([makeCatalogEntry()], CATEGORIES, {}, {
      data: makeCatalogPreview({
        items: [{ title: "Ghost story", url: null, author: null, published_at: "2026-07-12T08:00:00Z", summary: "s" }],
      }),
    });
    render(<CatalogPage />);
    const dialog = await openModal();
    expect(within(dialog).getByText("Ghost story")).toBeInTheDocument();
    expect(within(dialog).queryByRole("link", { name: /Ghost story/ })).not.toBeInTheDocument();
  });

  it("sends quick-settings deviations with the subscribe request", async () => {
    const entry = makeCatalogEntry();
    setSwr([entry], CATEGORIES, {}, { data: makeCatalogPreview() });
    apiMock.mockResolvedValue(makeFeed({ id: 42 }));
    render(<CatalogPage />);
    const dialog = await openModal();

    await userEvent.click(within(dialog).getByRole("checkbox", { name: "AI images" }));
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Mute" }));
    await userEvent.click(within(dialog).getByRole("button", { name: /Subscribe/ }));

    await waitFor(() =>
      expect(apiMock).toHaveBeenCalledWith("/feeds", {
        method: "POST",
        body: { url: entry.url, image_gen_enabled: false, is_muted: true },
      }),
    );
  });

  it("does not open when the card's subscribe button or subscribed link is clicked", async () => {
    setSwr([makeCatalogEntry()], CATEGORIES, {}, { data: makeCatalogPreview() });
    apiMock.mockResolvedValue(makeFeed({ id: 42 }));
    const first = render(<CatalogPage />);
    await userEvent.click(screen.getByRole("button", { name: /Subscribe/ }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    first.unmount();

    setSwr([makeCatalogEntry({ subscribed: true, feed_id: 7 })], CATEGORIES, {}, { data: makeCatalogPreview() });
    render(<CatalogPage />);
    fireEvent.click(screen.getByRole("link", { name: /Subscribed/ }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("smart feeds", () => {
  const RESOLVE: SmartFeedResolve = {
    key: "reddit",
    topic: "rust",
    url: "https://www.reddit.com/r/rust/.rss",
    title: "r/rust",
  };

  function setSmartSwr(opts: {
    resolve?: SmartFeedResolve;
    resolveError?: Error;
    preview?: CatalogPreview;
    previewError?: Error;
  } = {}) {
    swrMock.mockImplementation((key: string | null) => {
      if (key === null) return {};
      if (key === "/catalog/categories") return { data: CATEGORIES };
      if (key === "/catalog/smart") return { data: [makeSmartFeed()] };
      if (key.includes("/resolve")) return opts.resolveError ? { error: opts.resolveError } : { data: opts.resolve };
      if (key.includes("/preview")) return opts.previewError ? { error: opts.previewError } : { data: opts.preview };
      return { data: [makeCatalogEntry()] };
    });
  }

  beforeEach(() => {
    vi.clearAllMocks();
  });

  async function openSmartModal() {
    await userEvent.click(screen.getByRole("button", { name: /Follow any subreddit/ }));
    return screen.getByRole("dialog", { name: "Reddit" });
  }

  it("shows the rail and opens the smart feed modal", async () => {
    setSmartSwr();
    render(<CatalogPage />);
    expect(screen.getByLabelText("Smart feeds")).toBeInTheDocument();
    const dialog = await openSmartModal();
    expect(within(dialog).getByLabelText("Subreddit")).toBeInTheDocument();
    expect(within(dialog).getByText(/Enter a subreddit to preview/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /Subscribe/ })).toBeDisabled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("hides the rail while a search is active", async () => {
    setSmartSwr();
    render(<CatalogPage />);
    await userEvent.type(screen.getByPlaceholderText("Search feeds…"), "vim");
    await waitFor(
      () => expect(screen.queryByLabelText("Smart feeds")).not.toBeInTheDocument(),
      { timeout: 1000 },
    );
  });

  it("fills the topic from an example chip", async () => {
    setSmartSwr();
    render(<CatalogPage />);
    const dialog = await openSmartModal();
    await userEvent.click(within(dialog).getByRole("button", { name: "programming" }));
    expect(within(dialog).getByLabelText("Subreddit")).toHaveValue("programming");
  });

  it("resolves a topic, previews its stories, and subscribes", async () => {
    setSmartSwr({ resolve: RESOLVE, preview: makeCatalogPreview() });
    apiMock.mockResolvedValue(makeFeed({ id: 9 }));
    render(<CatalogPage />);
    const dialog = await openSmartModal();

    await userEvent.type(within(dialog).getByLabelText("Subreddit"), "rust");
    await waitFor(
      () => expect(swrMock).toHaveBeenCalledWith(
        "/catalog/smart/reddit/resolve?topic=rust",
        expect.anything(),
        expect.objectContaining({ shouldRetryOnError: false }),
      ),
      { timeout: 1000 },
    );
    expect(within(dialog).getByText(/Latest stories · r\/rust/)).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: /Fresh story/ })).toBeInTheDocument();
    expect(within(dialog).getByText(RESOLVE.url)).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: /Subscribe/ }));
    await waitFor(() =>
      expect(apiMock).toHaveBeenCalledWith("/feeds", {
        method: "POST",
        body: { url: RESOLVE.url },
      }),
    );
    expect(mutateMock).toHaveBeenCalledWith("/feeds");
    expect(within(dialog).getByRole("link", { name: /View feed/ })).toHaveAttribute("href", "/?feed=9");
  });

  it("sends quick-settings deviations when subscribing", async () => {
    setSmartSwr({ resolve: RESOLVE, preview: makeCatalogPreview() });
    apiMock.mockResolvedValue(makeFeed({ id: 9 }));
    render(<CatalogPage />);
    const dialog = await openSmartModal();
    await userEvent.type(within(dialog).getByLabelText("Subreddit"), "rust");
    await waitFor(
      () => expect(within(dialog).getByRole("button", { name: /Subscribe/ })).toBeEnabled(),
      { timeout: 1000 },
    );
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "AI summaries" }));
    await userEvent.click(within(dialog).getByRole("button", { name: /Subscribe/ }));
    await waitFor(() =>
      expect(apiMock).toHaveBeenCalledWith("/feeds", {
        method: "POST",
        body: { url: RESOLVE.url, ai_enabled: false },
      }),
    );
  });

  it("surfaces resolve and subscribe errors", async () => {
    setSmartSwr({ resolveError: new Error("That does not look like a valid Reddit subreddit") });
    render(<CatalogPage />);
    let dialog = await openSmartModal();
    await userEvent.type(within(dialog).getByLabelText("Subreddit"), "bad topic");
    await waitFor(
      () => expect(within(dialog).getByRole("alert")).toHaveTextContent(/valid Reddit subreddit/),
      { timeout: 1000 },
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));

    setSmartSwr({ resolve: RESOLVE, preview: makeCatalogPreview() });
    apiMock.mockRejectedValue(new Error("boom"));
    dialog = await openSmartModal();
    await userEvent.type(within(dialog).getByLabelText("Subreddit"), "rust");
    await waitFor(
      () => expect(within(dialog).getByRole("button", { name: /Subscribe/ })).toBeEnabled(),
      { timeout: 1000 },
    );
    await userEvent.click(within(dialog).getByRole("button", { name: /Subscribe/ }));
    expect(await within(dialog).findByText(/Could not subscribe to r\/rust: boom/)).toBeInTheDocument();
  });

  it("shows a preview error but keeps subscribing possible", async () => {
    setSmartSwr({ resolve: RESOLVE, previewError: new Error("bad gateway") });
    render(<CatalogPage />);
    const dialog = await openSmartModal();
    await userEvent.type(within(dialog).getByLabelText("Subreddit"), "rust");
    await waitFor(
      () => expect(within(dialog).getByRole("alert")).toHaveTextContent(/Could not load stories/),
      { timeout: 1000 },
    );
    expect(within(dialog).getByRole("button", { name: /Subscribe/ })).toBeEnabled();
  });
});
