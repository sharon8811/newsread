import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ArticleAssistantDrawer from "@/components/ArticleAssistantDrawer";
import type { HackerNewsDiscussionState } from "@/components/HackerNewsDiscussion";
import { makeArticleDetail } from "./fixtures";

const { streamDiscussionQAMock, streamQAMock } = vi.hoisted(() => ({
  streamDiscussionQAMock: vi.fn(),
  streamQAMock: vi.fn(),
}));

vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  streamDiscussionQA: streamDiscussionQAMock,
  streamQA: streamQAMock,
}));
vi.mock("@/components/QAPanel", () => ({
  default: (props: {
    heading: string;
    initialInput?: string;
    variant: string;
    stream: (question: string, onEvent: () => void) => Promise<void>;
  }) => (
    <div>
      <span>{props.heading}</span>
      <span>{props.variant}</span>
      <input aria-label="Assistant prompt" readOnly value={props.initialInput ?? ""} />
      <button onClick={() => props.stream("question", () => {})}>Run assistant</button>
    </div>
  ),
}));

const article = makeArticleDetail({ id: 9 });
const snapshot = {
  provider: "hackernews" as const,
  discussion_id: "42",
  fetched_at: "2026-07-14T00:00:00Z",
  reported_total: 2,
  included_total: 2,
  comments: [],
};

function discussion(over: Partial<HackerNewsDiscussionState> = {}) {
  return {
    ref: { provider: "hackernews", id: 42, canonicalUrl: "https://news.ycombinator.com/item?id=42" },
    loadThread: vi.fn().mockResolvedValue(snapshot),
    ...over,
  } as unknown as HackerNewsDiscussionState;
}

beforeEach(() => {
  streamDiscussionQAMock.mockReset().mockResolvedValue(undefined);
  streamQAMock.mockReset().mockResolvedValue(undefined);
});

describe("<ArticleAssistantDrawer>", () => {
  it("renders one article assistant by default and closes accessibly", async () => {
    const onClose = vi.fn();
    render(
      <ArticleAssistantDrawer
        article={article}
        discussion={discussion()}
        scope="article"
        onScopeChange={vi.fn()}
        draft={null}
        onClose={onClose}
      />,
    );
    expect(screen.getByRole("dialog", { name: "Ask this story" })).toBeInTheDocument();
    expect(screen.getByText("Ask the article")).toBeInTheDocument();
    expect(screen.queryByText("Ask the discussion")).toBeNull();
    expect(screen.getByText("embedded")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Run assistant" }));
    expect(streamQAMock).toHaveBeenCalledWith(9, "question", expect.any(Function));
    await userEvent.click(screen.getByRole("button", { name: "Close assistant" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("switches context and reuses the shared discussion snapshot", async () => {
    const state = discussion();
    const onScopeChange = vi.fn();
    const { rerender } = render(
      <ArticleAssistantDrawer
        article={article}
        discussion={state}
        scope="article"
        onScopeChange={onScopeChange}
        draft={null}
        onClose={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("tab", { name: "HN discussion" }));
    expect(onScopeChange).toHaveBeenCalledWith("discussion");
    rerender(
      <ArticleAssistantDrawer
        article={article}
        discussion={state}
        scope="discussion"
        onScopeChange={onScopeChange}
        draft={{ key: "draft-1", text: "Draft this reply" }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Ask the discussion")).toBeInTheDocument();
    expect(screen.getByLabelText("Assistant prompt")).toHaveValue("Draft this reply");
    await userEvent.click(screen.getByRole("button", { name: "Run assistant" }));
    await waitFor(() => expect(state.loadThread).toHaveBeenCalledWith(300));
    expect(streamDiscussionQAMock).toHaveBeenCalledWith(
      9,
      "question",
      snapshot,
      expect.any(Function),
    );
  });

  it("falls back to article scope when no HN discussion exists", () => {
    render(
      <ArticleAssistantDrawer
        article={article}
        discussion={discussion({ ref: null })}
        scope="discussion"
        onScopeChange={vi.fn()}
        draft={null}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByRole("tab", { name: "HN discussion" })).toBeDisabled();
    expect(screen.getByText("Ask the article")).toBeInTheDocument();
  });

  it("starts a discussion conversation without requiring a draft", () => {
    render(
      <ArticleAssistantDrawer
        article={article}
        discussion={discussion()}
        scope="discussion"
        onScopeChange={vi.fn()}
        draft={null}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Ask the discussion")).toBeInTheDocument();
    expect(screen.getByLabelText("Assistant prompt")).toHaveValue("");
  });
});
