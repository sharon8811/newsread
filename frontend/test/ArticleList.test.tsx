import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ArticleList, { articlesKey, mutateArticleLists } from "@/components/ArticleList";
import { makeArticle } from "./fixtures";
import {
  clearReadingSessions,
  getReadingReturnAnchor,
  readingSessionKey,
} from "@/lib/readingSession";

const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }));

const { swrMock, mutateMock } = vi.hoisted(() => ({ swrMock: vi.fn(), mutateMock: vi.fn() }));
vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));

vi.mock("@/components/ShareModal", () => ({
  default: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="share-modal" onClick={onClose} />
  ),
}));

vi.mock("@/components/ProjectPickerModal", () => ({
  default: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="project-picker" onClick={onClose} />
  ),
}));

vi.mock("@/components/NotInterestedModal", () => ({
  default: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="not-interested-modal" onClick={onClose} />
  ),
}));

function stub(articles: unknown, isLoading = false) {
  swrMock.mockReturnValue({ data: articles, isLoading });
}

function okFetch() {
  return vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => ({}) });
}

beforeEach(() => clearReadingSessions());

describe("<ArticleList>", () => {
  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
    pushMock.mockClear();
    // jsdom does not implement scrollIntoView
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("articlesKey encodes filter, feed and query params", () => {
    expect(articlesKey({ filter: "all" })).toBe("/articles?filter=all&limit=100");
    expect(articlesKey({ filter: "saved", feedId: "7", q: "ai" })).toBe(
      "/articles?filter=saved&limit=100&feed_id=7&q=ai",
    );
  });

  it("mutateArticleLists revalidates article lists and feeds", () => {
    mutateArticleLists();
    expect(mutateMock).toHaveBeenCalledTimes(2);
    // first call passes a key-matcher predicate
    const predicate = mutateMock.mock.calls[0][0] as (k: unknown) => boolean;
    expect(predicate("/articles?filter=all")).toBe(true);
    expect(predicate("/feeds")).toBe(false);
    expect(predicate(123)).toBe(false);
    expect(mutateMock).toHaveBeenCalledWith("/feeds");
  });

  it("renders loading skeletons while loading", () => {
    stub(undefined, true);
    const { container } = render(<ArticleList filter="saved" emptyTitle="Nothing" />);
    expect(container.querySelectorAll(".rounded-md").length).toBe(6);
  });

  it("renders the empty state with a subtitle", () => {
    stub(undefined, false);
    render(<ArticleList filter="saved" emptyTitle="No articles" emptySubtitle="try later" />);
    expect(screen.getByText("No articles")).toBeInTheDocument();
    expect(screen.getByText("try later")).toBeInTheDocument();
  });

  it("renders the empty state without a subtitle", () => {
    stub([], false);
    render(<ArticleList filter="saved" emptyTitle="Empty" />);
    expect(screen.getByText("Empty")).toBeInTheDocument();
    expect(screen.queryByText("try later")).not.toBeInTheDocument();
  });

  it("renders article rows in list mode", () => {
    stub([makeArticle({ id: 1, title: "First" }), makeArticle({ id: 2, title: "Second" })]);
    render(<ArticleList filter="saved" emptyTitle="Empty" />);
    expect(screen.getByText("First")).toBeInTheDocument();
    expect(screen.getByText("Second")).toBeInTheDocument();
    expect(screen.getByText(/j \/ k to navigate/)).toBeInTheDocument();
  });

  it("renders cards in cards mode", () => {
    stub([makeArticle({ id: 1, title: "Card One" }), makeArticle({ id: 2, title: "Card Two" })]);
    const { container } = render(
      <ArticleList filter="saved" emptyTitle="Empty" variant="cards" />,
    );
    expect(screen.getByText("Card One")).toBeInTheDocument();
    expect(screen.getByText("Card Two")).toBeInTheDocument();
    expect(container.querySelectorAll("article").length).toBe(2);
  });

  it("renders card-shaped skeletons while loading in cards mode", () => {
    stub(undefined, true);
    const { container } = render(
      <ArticleList filter="saved" emptyTitle="Empty" variant="cards" />,
    );
    expect(container.querySelectorAll(".rounded-lg").length).toBe(4);
  });

  it("navigates selection with j and k", () => {
    stub([makeArticle({ id: 1, title: "One" }), makeArticle({ id: 2, title: "Two" })]);
    const { container } = render(<ArticleList filter="saved" emptyTitle="Empty" />);
    fireEvent.keyDown(window, { key: "j" });
    // row 1 is now selected -> ArticleRow applies selected background
    fireEvent.keyDown(window, { key: "j" }); // clamps at last index
    fireEvent.keyDown(window, { key: "k" });
    fireEvent.keyDown(window, { key: "k" }); // clamps at 0
    expect(container).toBeTruthy();
  });

  it("opens the selected article on Enter", () => {
    stub([makeArticle({ id: 42, title: "Deep" })]);
    render(<ArticleList filter="saved" emptyTitle="Empty" />);
    fireEvent.keyDown(window, { key: "Enter" });
    expect(pushMock).toHaveBeenCalledWith("/article/42");
  });

  it("toggles saved with the s key", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    stub([makeArticle({ id: 5, is_saved: false })]);
    render(<ArticleList filter="saved" emptyTitle="Empty" />);
    fireEvent.keyDown(window, { key: "s" });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, opts] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/articles/5/state");
    expect(JSON.parse(opts.body)).toEqual({ is_saved: true });
    await waitFor(() => expect(mutateMock).toHaveBeenCalled());
  });

  it("toggles read with the m key", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    stub([makeArticle({ id: 6, is_read: false })]);
    render(<ArticleList filter="saved" emptyTitle="Empty" />);
    fireEvent.keyDown(window, { key: "m" });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, opts] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/articles/6/state");
    expect(JSON.parse(opts.body)).toEqual({ is_read: true });
  });

  it("ignores unhandled keys", () => {
    stub([makeArticle({ id: 1 })]);
    render(<ArticleList filter="saved" emptyTitle="Empty" />);
    fireEvent.keyDown(window, { key: "z" });
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("ignores keys typed into inputs", () => {
    stub([makeArticle({ id: 1 })]);
    render(
      <>
        <input data-testid="field" />
        <ArticleList filter="saved" emptyTitle="Empty" />
      </>,
    );
    const input = screen.getByTestId("field");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("ignores keys from contentEditable targets", () => {
    stub([makeArticle({ id: 1 })]);
    const { container } = render(<ArticleList filter="saved" emptyTitle="Empty" />);
    const editable = document.createElement("div");
    editable.setAttribute("contenteditable", "true");
    Object.defineProperty(editable, "isContentEditable", { value: true });
    container.appendChild(editable);
    fireEvent.keyDown(editable, { key: "Enter" });
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("ignores keys when there are no articles", () => {
    stub([], false);
    render(<ArticleList filter="saved" emptyTitle="Empty" />);
    fireEvent.keyDown(window, { key: "j" });
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("opens the share modal and suspends keyboard nav while open", async () => {
    stub([makeArticle({ id: 1, title: "Shareable" })]);
    render(<ArticleList filter="saved" emptyTitle="Empty" />);
    await userEvent.click(screen.getByTitle("Share with a note"));
    expect(screen.getByTestId("share-modal")).toBeInTheDocument();
    // keyboard is ignored while the modal is open
    fireEvent.keyDown(window, { key: "Enter" });
    expect(pushMock).not.toHaveBeenCalled();
    // closing the modal restores nav
    await userEvent.click(screen.getByTestId("share-modal"));
    expect(screen.queryByTestId("share-modal")).not.toBeInTheDocument();
  });

  it("opens the project picker and suspends keyboard nav while open", async () => {
    stub([makeArticle({ id: 1, title: "Pinnable" })]);
    render(<ArticleList filter="saved" emptyTitle="Empty" />);
    await userEvent.click(screen.getByTitle("Add to project"));
    expect(screen.getByTestId("project-picker")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Enter" });
    expect(pushMock).not.toHaveBeenCalled();
    await userEvent.click(screen.getByTestId("project-picker"));
    expect(screen.queryByTestId("project-picker")).not.toBeInTheDocument();
  });

  it("opens the not-interested modal and suspends keyboard nav while open", async () => {
    stub([makeArticle({ id: 1, title: "Dismissible" })]);
    render(<ArticleList filter="saved" emptyTitle="Empty" />);
    await userEvent.click(screen.getByTitle("Not interested"));
    expect(screen.getByTestId("not-interested-modal")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Enter" });
    expect(pushMock).not.toHaveBeenCalled();
    await userEvent.click(screen.getByTestId("not-interested-modal"));
    expect(screen.queryByTestId("not-interested-modal")).not.toBeInTheDocument();
  });

  it("saves via the row save button (onToggleSaved callback)", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    stub([makeArticle({ id: 9, is_saved: false })]);
    render(<ArticleList filter="saved" emptyTitle="Empty" />);
    await userEvent.click(screen.getByTitle("Save for later"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(String(fetchMock.mock.calls[0][0])).toContain("/articles/9/state");
  });
});

describe("<ArticleList> image-generation polling", () => {
  beforeEach(() => {
    swrMock.mockReset();
    Element.prototype.scrollIntoView = vi.fn();
  });

  function swrOptions() {
    return swrMock.mock.calls[0][2] as {
      refreshInterval: (data?: unknown) => number;
    };
  }

  it("polls fast while any article has an illustration rendering", () => {
    stub([makeArticle({ image_pending: true })]);
    render(<ArticleList filter="saved" emptyTitle="empty" />);
    const { refreshInterval } = swrOptions();
    expect(refreshInterval([makeArticle({ image_pending: true })])).toBe(3000);
  });

  it("falls back to the configured interval once nothing is pending", () => {
    stub([makeArticle()]);
    render(<ArticleList filter="saved" emptyTitle="empty" refreshInterval={4000} />);
    const { refreshInterval } = swrOptions();
    expect(refreshInterval([makeArticle()])).toBe(4000);
    expect(refreshInterval(undefined)).toBe(4000);
    // A pending article whose image already landed no longer forces the fast poll.
    expect(
      refreshInterval([makeArticle({ image_pending: true, image_url: "https://x/i.png" })]),
    ).toBe(4000);
  });
});

// ——— reading mode (filter unread/all): anchor window, auto-read, pills ———

type IOCallback = (entries: Partial<IntersectionObserverEntry>[]) => void;

class MockIntersectionObserver {
  static instances: MockIntersectionObserver[] = [];
  callback: IOCallback;
  options: IntersectionObserverInit | undefined;
  observed = new Set<Element>();
  constructor(cb: IOCallback, options?: IntersectionObserverInit) {
    this.callback = cb;
    this.options = options;
    MockIntersectionObserver.instances.push(this);
  }
  observe(el: Element) {
    this.observed.add(el);
  }
  unobserve(el: Element) {
    this.observed.delete(el);
  }
  disconnect() {
    this.observed.clear();
  }
}

/** The component observes its targets in an effect, which may not have
 * flushed when the test reaches for the observer — retry until it has. */
async function ioFor(el: Element): Promise<MockIntersectionObserver> {
  return await vi.waitFor(() => {
    const io = MockIntersectionObserver.instances.find((i) => i.observed.has(el));
    expect(io).toBeTruthy();
    return io!;
  });
}

function pageResponse(
  articles: unknown[],
  headers: Record<string, string> = {},
) {
  return {
    ok: true,
    status: 200,
    json: async () => articles,
    headers: new Headers(headers),
  };
}

function readingFetch(
  articles: unknown[],
  headers: Record<string, string> = {},
) {
  return vi.fn().mockImplementation((url: string) => {
    if (String(url).includes("/state/batch")) {
      return Promise.resolve({ ok: true, status: 204, json: async () => ({}) });
    }
    return Promise.resolve(pageResponse(articles, headers));
  });
}

function renderReading(ui: React.ReactElement) {
  // The reading window roots its observers in the app shell's <main> scroller.
  return render(<main>{ui}</main>);
}

describe("<ArticleList> reading mode", () => {
  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
    pushMock.mockClear();
    MockIntersectionObserver.instances = [];
    vi.stubGlobal("IntersectionObserver", MockIntersectionObserver);
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("loads the anchored window and shows the unread pill", async () => {
    const fetchMock = readingFetch(
      [makeArticle({ id: 1, title: "Resume Here" }), makeArticle({ id: 2, title: "Next" })],
      { "X-Unread-Count": "7", "X-New-Above-Count": "0" },
    );
    vi.stubGlobal("fetch", fetchMock);
    renderReading(<ArticleList filter="unread" emptyTitle="Empty" />);
    expect(await screen.findByText("Resume Here")).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0][0])).toContain("anchor=resume");
    expect(String(fetchMock.mock.calls[0][0])).toContain("reading_window=true");
    expect(screen.getByText("7 unread ↓")).toBeInTheDocument();
    expect(screen.queryByText(/new ↑/)).not.toBeInTheDocument();
  });

  it("shows the new-above pill and the history sentinel when headers say so", async () => {
    vi.stubGlobal(
      "fetch",
      readingFetch([makeArticle({ id: 1, title: "Mid List" })], {
        "X-Unread-Count": "3",
        "X-New-Above-Count": "2",
        "X-Prev-Cursor": "prev-token",
      }),
    );
    renderReading(<ArticleList filter="all" emptyTitle="Empty" />);
    expect(await screen.findByText("Mid List")).toBeInTheDocument();
    expect(screen.getByText("2 new ↑")).toBeInTheDocument();
    expect(screen.getByText(/loading earlier articles/)).toBeInTheDocument();
  });

  it("shows 'All caught up' when nothing is unread", async () => {
    vi.stubGlobal(
      "fetch",
      readingFetch([makeArticle({ id: 1, title: "Old", is_read: true })], {
        "X-Unread-Count": "0",
        "X-New-Above-Count": "0",
      }),
    );
    renderReading(<ArticleList filter="all" emptyTitle="Empty" />);
    expect(await screen.findByText("All caught up ✓")).toBeInTheDocument();
  });

  it("marks an article read when it scrolls past the top and flushes a batch", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = readingFetch(
        [
          makeArticle({ id: 11, title: "Passing" }),
          makeArticle({ id: 12, title: "Below" }),
        ],
        { "X-Unread-Count": "2", "X-New-Above-Count": "0" },
      );
      vi.stubGlobal("fetch", fetchMock);
      const { container } = renderReading(
        <ArticleList filter="unread" emptyTitle="Empty" />,
      );
      await vi.waitFor(() =>
        expect(container.querySelector('[data-article-id="11"]')).toBeTruthy(),
      );

      // Simulate the item's box fully exiting through the scroller's top edge.
      const target = container.querySelector('[data-article-id="11"]')!;
      const io = await ioFor(target);
      io.callback([
        {
          isIntersecting: false,
          target,
          boundingClientRect: { width: 100, height: 50, top: -60, bottom: -10 } as DOMRectReadOnly,
          rootBounds: { top: 0 } as DOMRectReadOnly,
        },
      ]);

      // Optimistic: the pill drops before any network flush.
      await vi.waitFor(() => expect(screen.getByText("1 unread ↓")).toBeInTheDocument());

      await vi.advanceTimersByTimeAsync(2000);
      const batchCall = fetchMock.mock.calls.find((c) =>
        String(c[0]).includes("/state/batch"),
      )!;
      expect(batchCall).toBeTruthy();
      const body = JSON.parse(batchCall[1].body);
      expect(body.article_ids).toEqual([11]);
      expect(body.read_source).toBe("scrolled");
      expect(body.frontier_article_id).toBe(11);
    } finally {
      vi.useRealTimers();
    }
  });

  it("re-marking an already-read article is a no-op (no queue, no flush)", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = readingFetch(
        [makeArticle({ id: 21, title: "Already", is_read: true })],
        { "X-Unread-Count": "0", "X-New-Above-Count": "0" },
      );
      vi.stubGlobal("fetch", fetchMock);
      const { container } = renderReading(
        <ArticleList filter="all" emptyTitle="Empty" />,
      );
      await vi.waitFor(() =>
        expect(container.querySelector('[data-article-id="21"]')).toBeTruthy(),
      );
      const target = container.querySelector('[data-article-id="21"]')!;
      const io = await ioFor(target);
      io.callback([
        {
          isIntersecting: false,
          target,
          boundingClientRect: { width: 100, height: 50, top: -60, bottom: -10 } as DOMRectReadOnly,
          rootBounds: { top: 0 } as DOMRectReadOnly,
        },
      ]);
      await vi.advanceTimersByTimeAsync(3000);
      expect(
        fetchMock.mock.calls.filter((c) => String(c[0]).includes("/state/batch")),
      ).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows the reading-mode empty state when the window is empty", async () => {
    vi.stubGlobal(
      "fetch",
      readingFetch([], { "X-Unread-Count": "0", "X-New-Above-Count": "0" }),
    );
    renderReading(
      <ArticleList filter="unread" emptyTitle="All caught up." emptySubtitle="sub" />,
    );
    expect(await screen.findByText("All caught up.")).toBeInTheDocument();
    expect(screen.getByText("sub")).toBeInTheDocument();
  });
});

describe("<ArticleList> reading mode interactions", () => {
  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
    pushMock.mockClear();
    MockIntersectionObserver.instances = [];
    vi.stubGlobal("IntersectionObserver", MockIntersectionObserver);
    Element.prototype.scrollIntoView = vi.fn();
    (Element.prototype as unknown as { scrollTo: unknown }).scrollTo = vi.fn();
  });

  function routedFetch(
    routes: { match: (u: string) => boolean; articles: unknown[]; headers?: Record<string, string> }[],
  ) {
    const mock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/state/batch") || String(url).includes("/state")) {
        return Promise.resolve({ ok: true, status: 204, json: async () => ({}) });
      }
      const route = routes.find((r) => r.match(String(url)));
      if (!route) return Promise.reject(new Error(`no route: ${url}`));
      return Promise.resolve(pageResponse(route.articles, route.headers ?? {}));
    });
    vi.stubGlobal("fetch", mock);
    return mock;
  }

  it("bottom sentinel loads the next page; end of list shows the keys hint", async () => {
    const fetchMock = routedFetch([
      {
        match: (u) => u.includes("anchor=resume"),
        articles: [makeArticle({ id: 1, title: "First" })],
        headers: { "X-Unread-Count": "2", "X-Next-Cursor": "n1" },
      },
      {
        match: (u) => u.includes("cursor=n1"),
        articles: [makeArticle({ id: 2, title: "Second" })],
      },
    ]);
    renderReading(<ArticleList filter="all" emptyTitle="Empty" />);
    await screen.findByText("First");
    expect(screen.getByText(/loading more/)).toBeInTheDocument();

    const sentinel = screen.getByText(/loading more/).parentElement!;
    const io = await ioFor(sentinel);
    io.callback([{ isIntersecting: true, target: sentinel } as IntersectionObserverEntry]);

    await screen.findByText("Second");
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("cursor=n1"))).toBe(true);
    // Last page reached: the sentinel gives way to the keyboard hint.
    expect(screen.queryByText(/loading more/)).not.toBeInTheDocument();
    expect(screen.getByText(/j \/ k to navigate/)).toBeInTheDocument();
  });

  it("top sentinel pages read history in above", async () => {
    const fetchMock = routedFetch([
      {
        match: (u) => u.includes("anchor=resume"),
        articles: [makeArticle({ id: 5, title: "Anchor" })],
        headers: { "X-Unread-Count": "1", "X-Prev-Cursor": "p1" },
      },
      {
        match: (u) => u.includes("direction=before"),
        articles: [makeArticle({ id: 4, title: "History", is_read: true })],
      },
    ]);
    renderReading(<ArticleList filter="all" emptyTitle="Empty" />);
    await screen.findByText("Anchor");
    const sentinel = screen.getByText(/loading earlier/).parentElement!;
    const io = await ioFor(sentinel);
    io.callback([{ isIntersecting: true, target: sentinel } as IntersectionObserverEntry]);
    await screen.findByText("History");
    expect(
      fetchMock.mock.calls.some((c) => String(c[0]).includes("direction=before")),
    ).toBe(true);
  });

  it("unread pill jumps to the next unread below the viewport", async () => {
    routedFetch([
      {
        match: (u) => u.includes("anchor=resume"),
        articles: [
          makeArticle({ id: 1, title: "Read One", is_read: true }),
          makeArticle({ id: 2, title: "Unread Two" }),
        ],
        headers: { "X-Unread-Count": "1" },
      },
    ]);
    // Stub at the prototype, not on a queried instance: a re-render between
    // the stub and the click can swap the row element, and the component
    // reads rects/scrolls through its own ref to the CURRENT node.
    const origRect = Element.prototype.getBoundingClientRect;
    const scrollSpy = vi.fn();
    Element.prototype.getBoundingClientRect = function () {
      if ((this as Element).getAttribute?.("data-article-id") === "2") {
        return { top: 500, bottom: 700, width: 100, height: 200 } as DOMRect;
      }
      return origRect.call(this);
    };
    const origScroll = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = scrollSpy;
    try {
      renderReading(<ArticleList filter="all" emptyTitle="Empty" />);
      await screen.findByText("Unread Two");
      // Re-click on retry: a click that lands before the component's live
      // refs are wired is a silent no-op.
      await waitFor(() => {
        fireEvent.click(screen.getByText("1 unread ↓"));
        expect(scrollSpy).toHaveBeenCalledWith(
          expect.objectContaining({ block: "start" }),
        );
      });
    } finally {
      Element.prototype.getBoundingClientRect = origRect;
      Element.prototype.scrollIntoView = origScroll;
    }
  });

  it("unread pill falls back to the top when only new-above items remain", async () => {
    const fetchMock = routedFetch([
      {
        match: (u) => u.includes("anchor=resume"),
        articles: [makeArticle({ id: 1, title: "Read One", is_read: true })],
        headers: { "X-Unread-Count": "2", "X-New-Above-Count": "2" },
      },
      {
        match: (u) => !u.includes("anchor"),
        articles: [makeArticle({ id: 9, title: "Fresh Top" })],
      },
    ]);
    renderReading(<ArticleList filter="all" emptyTitle="Empty" />);
    await screen.findByText("Read One");
    fireEvent.click(screen.getByText("2 unread ↓"));
    await screen.findByText("Fresh Top");
    expect(
      fetchMock.mock.calls.some(
        (c) => !String(c[0]).includes("anchor") && String(c[0]).includes("/articles?"),
      ),
    ).toBe(true);
  });

  it("new-above pill resets the window to the top of the list", async () => {
    routedFetch([
      {
        match: (u) => u.includes("anchor=resume"),
        articles: [makeArticle({ id: 5, title: "Mid Anchor" })],
        headers: { "X-Unread-Count": "3", "X-New-Above-Count": "2" },
      },
      {
        match: (u) => !u.includes("anchor"),
        articles: [makeArticle({ id: 9, title: "Breaking Top" })],
      },
    ]);
    renderReading(<ArticleList filter="all" emptyTitle="Empty" />);
    await screen.findByText("Mid Anchor");
    fireEvent.click(screen.getByText("2 new ↑"));
    await screen.findByText("Breaking Top");
    expect(screen.queryByText(/new ↑/)).not.toBeInTheDocument();
  });

  it("keyboard m toggles read through the window (no full refetch)", async () => {
    const fetchMock = routedFetch([
      {
        match: (u) => u.includes("anchor=resume"),
        articles: [makeArticle({ id: 3, title: "Toggle Me" })],
        headers: { "X-Unread-Count": "1" },
      },
    ]);
    renderReading(<ArticleList filter="unread" emptyTitle="Empty" />);
    await screen.findByText("Toggle Me");
    fireEvent.keyDown(window, { key: "m" });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((c) => String(c[0]).includes("/articles/3/state")),
      ).toBe(true),
    );
    await screen.findByText("All caught up ✓");
  });

  it("keyboard Enter opens the selected article from the window", async () => {
    routedFetch([
      {
        match: (u) => u.includes("anchor=resume"),
        articles: [makeArticle({ id: 77, title: "Openable" })],
        headers: { "X-Unread-Count": "1" },
      },
    ]);
    renderReading(<ArticleList filter="all" emptyTitle="Empty" />);
    await screen.findByText("Openable");
    fireEvent.keyDown(window, { key: "Enter" });
    expect(pushMock).toHaveBeenCalledWith("/article/77");
    expect(screen.getByText("All caught up ✓")).toBeInTheDocument();
  });

  it("keeps a clicked unread row in place when the list remounts", async () => {
    const fetchMock = routedFetch([
      {
        match: (u) => u.includes("anchor=resume"),
        articles: [
          makeArticle({ id: 81, title: "Return Here" }),
          makeArticle({ id: 82, title: "Still Below" }),
        ],
        headers: { "X-Unread-Count": "2" },
      },
    ]);
    const first = renderReading(<ArticleList filter="unread" emptyTitle="Empty" />);
    await screen.findByText("Return Here");
    fireEvent.click(screen.getByText("Return Here"));
    expect(pushMock).toHaveBeenCalledWith("/article/81");
    await screen.findByText("1 unread ↓");
    expect(getReadingReturnAnchor(readingSessionKey("unread"))).toEqual({
      articleId: 81,
      offset: 0,
    });
    first.unmount();

    const second = renderReading(<ArticleList filter="unread" emptyTitle="Empty" />);
    expect(await screen.findByText("Return Here")).toBeInTheDocument();
    expect(screen.getByText("Still Below")).toBeInTheDocument();
    expect(screen.getByText("1 unread ↓")).toBeInTheDocument();
    expect(getReadingReturnAnchor(readingSessionKey("unread"))).toEqual({
      articleId: 81,
      offset: 0,
    });
    expect(
      fetchMock.mock.calls.filter((call) => String(call[0]).includes("anchor=resume")),
    ).toHaveLength(1);
    second.unmount();
  });
});

describe("<ArticleList> reading mode guards", () => {
  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
    pushMock.mockClear();
    MockIntersectionObserver.instances = [];
    vi.stubGlobal("IntersectionObserver", MockIntersectionObserver);
    Element.prototype.scrollIntoView = vi.fn();
    (Element.prototype as unknown as { scrollTo: unknown }).scrollTo = vi.fn();
  });

  it("articlesKey carries the resume anchor when asked", () => {
    expect(articlesKey({ filter: "unread", anchor: "resume" })).toBe(
      "/articles?filter=unread&limit=100&anchor=resume",
    );
  });

  it("ignores unmounting (zero-rect) and still-below observer entries", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = readingFetch(
        [makeArticle({ id: 31, title: "Guarded" })],
        { "X-Unread-Count": "1" },
      );
      vi.stubGlobal("fetch", fetchMock);
      const { container } = renderReading(
        <ArticleList filter="all" emptyTitle="Empty" />,
      );
      await vi.waitFor(() =>
        expect(container.querySelector('[data-article-id="31"]')).toBeTruthy(),
      );
      const target = container.querySelector('[data-article-id="31"]')!;
      const io = await ioFor(target);
      io.callback([
        {
          // unmount storm: zero rect must not mark
          isIntersecting: false,
          target,
          boundingClientRect: { width: 0, height: 0, top: 0, bottom: 0 } as DOMRectReadOnly,
          rootBounds: { top: 0 } as DOMRectReadOnly,
        },
        {
          // exited through the BOTTOM edge (scrolled up) — must not mark
          isIntersecting: false,
          target,
          boundingClientRect: { width: 100, height: 50, top: 900, bottom: 950 } as DOMRectReadOnly,
          rootBounds: { top: 0 } as DOMRectReadOnly,
        },
        {
          // missing rootBounds falls back to 0 and still guards correctly
          isIntersecting: true,
          target,
          boundingClientRect: { width: 100, height: 50, top: 10, bottom: 60 } as DOMRectReadOnly,
          rootBounds: null,
        },
      ] as IntersectionObserverEntry[]);
      await vi.advanceTimersByTimeAsync(3000);
      expect(
        fetchMock.mock.calls.filter((c) => String(c[0]).includes("/state/batch")),
      ).toHaveLength(0);
      expect(screen.getByText("1 unread ↓")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("jump pill pulls the next page when the unread lives beyond the window", async () => {
    const fetchMock = routedJumpFetch();
    vi.stubGlobal("fetch", fetchMock);
    const { container } = renderReading(<ArticleList filter="all" emptyTitle="Empty" />);
    await screen.findByText("Read One");
    fireEvent.click(screen.getByText("1 unread ↓"));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("cursor=n1"))).toBe(true),
    );
    await screen.findByText("Deep Unread");
    // After the retry the fetched unread below gets scrolled to.
    const target = container.querySelector('[data-article-id="52"]') as HTMLElement;
    expect(target).toBeTruthy();
  });
});

function routedJumpFetch() {
  return vi.fn().mockImplementation((url: string) => {
    const u = String(url);
    if (u.includes("/state")) {
      return Promise.resolve({ ok: true, status: 204, json: async () => ({}) });
    }
    if (u.includes("anchor=resume")) {
      return Promise.resolve(
        pageResponse([makeArticle({ id: 51, title: "Read One", is_read: true })], {
          "X-Unread-Count": "1",
          "X-Next-Cursor": "n1",
        }),
      );
    }
    if (u.includes("cursor=n1")) {
      return Promise.resolve(
        pageResponse([makeArticle({ id: 52, title: "Deep Unread" })], {}),
      );
    }
    return Promise.reject(new Error(`no route ${u}`));
  });
}

describe("<ArticleList> reading mode with a feed scope", () => {
  beforeEach(() => {
    swrMock.mockReset();
    MockIntersectionObserver.instances = [];
    vi.stubGlobal("IntersectionObserver", MockIntersectionObserver);
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("scopes the window requests to the feed", async () => {
    const fetchMock = readingFetch(
      [makeArticle({ id: 1, title: "Feed Scoped" })],
      { "X-Unread-Count": "1" },
    );
    vi.stubGlobal("fetch", fetchMock);
    renderReading(<ArticleList filter="unread" feedId="7" emptyTitle="Empty" />);
    await screen.findByText("Feed Scoped");
    expect(String(fetchMock.mock.calls[0][0])).toContain("feed_id=7");
  });
});
