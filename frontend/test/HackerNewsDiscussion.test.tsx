import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import HackerNewsDiscussion, {
  HackerNewsDiscussionLink,
} from "@/components/HackerNewsDiscussion";
import { makeArticleDetail } from "./fixtures";
import type { DiscussionComment, DiscussionSnapshot, HNItem } from "@/lib/discussions";

const {
  discussionRefForMock,
  fetchHNThreadMock,
  mutateMock,
  streamDiscussionQAMock,
  swrMock,
} = vi.hoisted(() => ({
  discussionRefForMock: vi.fn(),
  fetchHNThreadMock: vi.fn(),
  mutateMock: vi.fn(),
  streamDiscussionQAMock: vi.fn(),
  swrMock: vi.fn(),
}));

vi.mock("swr", () => ({ default: swrMock }));
vi.mock("@/lib/discussions", async (original) => ({
  ...(await original<typeof import("@/lib/discussions")>()),
  discussionRefFor: discussionRefForMock,
  fetchHNThread: fetchHNThreadMock,
}));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  streamDiscussionQA: streamDiscussionQAMock,
}));
vi.mock("@/components/QAPanel", () => ({
  default: (props: {
    heading: string;
    initialInput?: string;
    stream: (question: string, onEvent: () => void) => Promise<void>;
  }) => (
    <div>
      <span>{props.heading}</span>
      <input aria-label="Discussion prompt" readOnly value={props.initialInput ?? ""} />
      <button onClick={() => props.stream("question", () => {})}>Run discussion stream</button>
    </div>
  ),
}));

const article = makeArticleDetail({
  id: 9,
  comments_url: "https://news.ycombinator.com/item?id=42",
});
const ref = {
  provider: "hackernews" as const,
  id: 42,
  canonicalUrl: "https://news.ycombinator.com/item?id=42",
};
const story: HNItem = { id: 42, score: 17, descendants: 3, kids: [100, 103] };

function comment(over: Partial<DiscussionComment>): DiscussionComment {
  return {
    id: 100,
    parent_id: 42,
    author: "alice",
    text: "Top-level point",
    created_at: "2026-07-12T12:00:00Z",
    depth: 0,
    position: 0,
    deleted: false,
    dead: false,
    ...over,
  };
}

function snapshot(over: Partial<DiscussionSnapshot> = {}): DiscussionSnapshot {
  return {
    provider: "hackernews",
    discussion_id: "42",
    fetched_at: "2026-07-12T12:00:00Z",
    reported_total: 3,
    included_total: 3,
    comments: [
      comment({}),
      comment({
        id: 101,
        parent_id: 100,
        author: null,
        text: "",
        created_at: null,
        depth: 1,
        position: 1,
        deleted: true,
      }),
      comment({
        id: 103,
        author: null,
        text: "",
        created_at: null,
        position: 2,
        dead: true,
      }),
    ],
    ...over,
  };
}

let swrResult: {
  data?: HNItem;
  error?: unknown;
  isLoading?: boolean;
  mutate: typeof mutateMock;
};

beforeEach(() => {
  discussionRefForMock.mockReturnValue(ref);
  swrResult = { data: story, error: undefined, isLoading: false, mutate: mutateMock };
  swrMock.mockImplementation(() => swrResult);
  fetchHNThreadMock.mockResolvedValue(snapshot());
  streamDiscussionQAMock.mockResolvedValue(undefined);
});

describe("Hacker News discussion UI", () => {
  it("renders nothing when the article has no supported discussion", () => {
    discussionRefForMock.mockReturnValue(null);
    swrResult = { data: undefined, isLoading: false, mutate: mutateMock };
    expect(render(<HackerNewsDiscussionLink article={article} />).container.firstChild).toBeNull();
    expect(render(<HackerNewsDiscussion article={article} />).container.firstChild).toBeNull();
  });

  it("shows fallback and live metadata in the compact discussion link", () => {
    swrResult = { data: undefined, isLoading: true, mutate: mutateMock };
    const { rerender } = render(<HackerNewsDiscussionLink article={article} />);
    expect(screen.getByRole("link", { name: /Discussion/ })).toHaveAttribute(
      "href",
      ref.canonicalUrl,
    );

    swrResult = { data: { id: 42 }, isLoading: false, mutate: mutateMock };
    rerender(<HackerNewsDiscussionLink article={article} />);
    expect(screen.getByRole("link", { name: /0 points, 0 comments/ })).toBeInTheDocument();
  });

  it("shows loading and unavailable story states", () => {
    swrResult = { data: undefined, isLoading: true, mutate: mutateMock };
    const { rerender } = render(<HackerNewsDiscussion article={article} />);
    expect(screen.getByText("Refreshing points and comments")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Read comments" })).toBeDisabled();

    swrResult = { data: undefined, error: new Error("offline"), isLoading: false, mutate: mutateMock };
    rerender(<HackerNewsDiscussion article={article} />);
    expect(screen.getByText("Live discussion unavailable")).toBeInTheDocument();
    expect(screen.getByText(/original discussion link still works/)).toBeInTheDocument();
  });

  it("refreshes metadata and renders nested, deleted, dead, and unknown comments", async () => {
    let resolveThread!: (value: DiscussionSnapshot) => void;
    fetchHNThreadMock.mockReturnValueOnce(
      new Promise<DiscussionSnapshot>((resolve) => {
        resolveThread = resolve;
      }),
    );
    render(<HackerNewsDiscussion article={article} />);

    await userEvent.click(screen.getByTitle("Refresh points and comment count"));
    expect(mutateMock).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole("button", { name: "Read comments" }));
    expect(screen.getByLabelText("Loading comments")).toBeInTheDocument();

    await act(async () => resolveThread(snapshot()));
    expect(await screen.findByText("Top-level point")).toBeInTheDocument();
    expect(screen.getByText("[deleted]")).toBeInTheDocument();
    expect(screen.getByText("[no visible text]")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "deleted" })).toHaveAttribute(
      "href",
      "https://news.ycombinator.com/item?id=101",
    );
    expect(screen.getByRole("link", { name: "unknown" })).toBeInTheDocument();
    expect(screen.getByText("Loaded 3 of 3 comments")).toBeInTheDocument();

    await userEvent.click(screen.getAllByRole("button", { name: "Draft reply" })[1]);
    expect(screen.getByLabelText("Discussion prompt")).toHaveValue(
      "Draft a thoughtful reply to comment 101. My point is: ",
    );
    await userEvent.click(screen.getByRole("button", { name: "Hide comments" }));
    expect(screen.queryByText("Top-level point")).toBeNull();
  });

  it("shows an empty snapshot and loads more comments for analysis", async () => {
    fetchHNThreadMock
      .mockResolvedValueOnce(snapshot({ comments: [], included_total: 0, reported_total: 5 }))
      .mockResolvedValueOnce(snapshot({ reported_total: 5 }));
    render(<HackerNewsDiscussion article={article} />);
    await userEvent.click(screen.getByRole("button", { name: "Read comments" }));
    expect(await screen.findByText("No visible comments yet.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Load more for analysis" }));
    await waitFor(() => expect(fetchHNThreadMock).toHaveBeenLastCalledWith(story, 300, expect.any(AbortSignal)));
    expect(await screen.findByText("Top-level point")).toBeInTheDocument();
  });

  it("reports Error and non-Error thread failures", async () => {
    fetchHNThreadMock.mockRejectedValueOnce(new Error("HN timed out"));
    const { unmount } = render(<HackerNewsDiscussion article={article} />);
    await userEvent.click(screen.getByRole("button", { name: "Read comments" }));
    expect(await screen.findByText("HN timed out")).toBeInTheDocument();
    unmount();

    fetchHNThreadMock.mockRejectedValueOnce("offline");
    render(<HackerNewsDiscussion article={article} />);
    await userEvent.click(screen.getByRole("button", { name: "Read comments" }));
    expect(await screen.findByText("Could not load the discussion")).toBeInTheDocument();
  });

  it("reuses a complete snapshot when asking the discussion", async () => {
    render(<HackerNewsDiscussion article={article} />);
    await userEvent.click(screen.getByRole("button", { name: "Read comments" }));
    await screen.findByText("Top-level point");
    await userEvent.click(screen.getByRole("button", { name: "Run discussion stream" }));
    await waitFor(() =>
      expect(streamDiscussionQAMock).toHaveBeenCalledWith(
        9,
        "question",
        expect.objectContaining({ included_total: 3 }),
        expect.any(Function),
      ),
    );
    expect(fetchHNThreadMock).toHaveBeenCalledOnce();
  });

  it("reuses a gapped snapshot for Q&A instead of re-walking the thread", async () => {
    // included < requested limit means the walk exhausted the thread, so the
    // Q&A path must not refetch on every question just because of the gaps.
    fetchHNThreadMock.mockResolvedValueOnce(
      snapshot({
        included_total: 2,
        reported_total: 5,
        comments: [comment({}), comment({ id: 103, position: 1, text: "Second point" })],
      }),
    );
    render(<HackerNewsDiscussion article={article} />);
    await userEvent.click(screen.getByRole("button", { name: "Read comments" }));
    await screen.findByText("Top-level point");
    await userEvent.click(screen.getByRole("button", { name: "Run discussion stream" }));
    await waitFor(() =>
      expect(streamDiscussionQAMock).toHaveBeenCalledWith(
        9,
        "question",
        expect.objectContaining({ included_total: 2 }),
        expect.any(Function),
      ),
    );
    expect(fetchHNThreadMock).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Load more for analysis" })).toBeInTheDocument();
  });

  it("lets a superseding load replace an aborted one without surfacing its error", async () => {
    fetchHNThreadMock
      .mockImplementationOnce(
        (_story, _limit, signal: AbortSignal) =>
          new Promise((_resolve, reject) => {
            signal.addEventListener("abort", () =>
              reject(new DOMException("The user aborted a request.", "AbortError")),
            );
          }),
      )
      .mockResolvedValueOnce(snapshot());
    render(<HackerNewsDiscussion article={article} />);
    await userEvent.click(screen.getByRole("button", { name: "Read comments" }));
    await userEvent.click(screen.getByRole("button", { name: "Run discussion stream" }));
    expect(await screen.findByText("Top-level point")).toBeInTheDocument();
    expect(screen.queryByText(/aborted/i)).toBeNull();
  });

  it("aborts an in-flight thread request when unmounted", async () => {
    fetchHNThreadMock.mockReturnValueOnce(new Promise(() => {}));
    const { unmount } = render(<HackerNewsDiscussion article={article} />);
    await userEvent.click(screen.getByRole("button", { name: "Read comments" }));
    const signal = fetchHNThreadMock.mock.calls[0][2] as AbortSignal;
    expect(signal.aborted).toBe(false);
    unmount();
    expect(signal.aborted).toBe(true);
  });
});
