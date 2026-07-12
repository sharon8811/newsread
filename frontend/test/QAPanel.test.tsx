import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import QAPanel from "@/components/QAPanel";
import { makeArticleDetail } from "./fixtures";
import type { AiStatus, ChatMessage, QAStreamEvent } from "@/lib/api";

const { streamQAMock, swrMock, mutateMock } = vi.hoisted(() => ({
  streamQAMock: vi.fn(),
  swrMock: vi.fn(),
  mutateMock: vi.fn(),
}));

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  streamQA: streamQAMock,
}));
vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));

const article = makeArticleDetail({ id: 1 });
const KEY = "/articles/1/qa";

// The page passes an article-bound stream closure; mirror that shape so the
// existing streamQAMock(articleId, …) assertions keep holding.
const panelProps = {
  qaKey: KEY,
  stream: (q: string, cb: (e: QAStreamEvent) => void) => streamQAMock(article.id, q, cb),
  heading: "Ask the article",
  placeholder: "Ask anything about this article…",
  suggestions: [
    "What are the key points?",
    "Why does this matter?",
    "What is the counterargument?",
  ],
};

function makeStatus(over: Partial<AiStatus> = {}): AiStatus {
  return { configured: true, model: "m", search: false, search_provider: null, ...over };
}

function makeMsg(over: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 1,
    role: "assistant",
    content: "answer",
    tool_events: null,
    created_at: "2024-01-01T00:00:00Z",
    ...over,
  };
}

// Feed useSWR by key: "/ai/status" -> status, KEY -> messages.
function stub({
  status = makeStatus(),
  messages,
}: {
  status?: AiStatus | undefined;
  messages?: ChatMessage[];
} = {}) {
  const map: Record<string, unknown> = { "/ai/status": status, [KEY]: messages };
  swrMock.mockImplementation((k: string | null) => ({ data: k == null ? undefined : map[k] }));
}

beforeAll(() => {
  // jsdom does not implement scrollIntoView.
  Element.prototype.scrollIntoView = vi.fn();
});

beforeEach(() => {
  swrMock.mockReset();
  streamQAMock.mockReset();
  mutateMock.mockClear();
});

describe("<QAPanel>", () => {
  it("renders nothing when AI is not configured", () => {
    stub({ status: makeStatus({ configured: false }) });
    const { container } = render(<QAPanel {...panelProps} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when status is undefined", () => {
    swrMock.mockReturnValue({ data: undefined });
    const { container } = render(<QAPanel {...panelProps} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows the header, suggestions and no web-aware label by default", () => {
    stub();
    render(<QAPanel {...panelProps} />);
    expect(screen.getByText("Ask the article")).toBeInTheDocument();
    expect(screen.getByText("What are the key points?")).toBeInTheDocument();
    expect(screen.queryByText("· web-aware")).toBeNull();
  });

  it("shows the web-aware label when search is enabled", () => {
    stub({ status: makeStatus({ search: true, search_provider: "tavily" }) });
    render(<QAPanel {...panelProps} />);
    expect(screen.getByText("· web-aware")).toBeInTheDocument();
  });

  it("prefills an editable comment drafting request", () => {
    stub();
    const { rerender } = render(<QAPanel {...panelProps} />);
    rerender(
      <QAPanel
        key="comment-44"
        {...panelProps}
        initialInput="Draft a reply to comment 44. My point is: "
      />,
    );
    expect(screen.getByPlaceholderText(panelProps.placeholder)).toHaveValue(
      "Draft a reply to comment 44. My point is: ",
    );
  });

  it("renders existing history with tool traces (all label + host branches)", () => {
    const messages: ChatMessage[] = [
      makeMsg({ id: 1, role: "user", content: "my question" }),
      makeMsg({
        id: 2,
        role: "assistant",
        content: "the reply body",
        tool_events: [
          { name: "tavily_search", args: {}, summary: "s1" }, // no query -> `?? ""` fallback
          { name: "web_search", args: { query: "q2" }, summary: null },
          { name: "web_extract", args: { url: "https://www.example.com/p" }, summary: "s3" },
          { name: "web_extract", args: { url: "not a url" }, summary: "bad" },
          { name: "web_extract", args: {}, summary: "empty" },
          { name: "custom_tool", args: {}, summary: "s4" },
        ],
      }),
      // assistant with no tool_events -> exercises the `?? []` fallback
      makeMsg({ id: 3, role: "assistant", content: "second reply", tool_events: null }),
    ];
    stub({ messages });
    const { container } = render(<QAPanel {...panelProps} />);

    expect(screen.getByText("my question")).toBeInTheDocument();
    expect(screen.getByText("the reply body")).toBeInTheDocument();
    expect(screen.getByText("second reply")).toBeInTheDocument();
    // toolLabel branches
    expect(screen.getByText('Searching the web (Tavily): “”')).toBeInTheDocument();
    expect(screen.getByText('Searching the web (SearXNG): “q2”')).toBeInTheDocument();
    expect(screen.getByText("Reading example.com")).toBeInTheDocument();
    expect(screen.getByText("Reading not a url")).toBeInTheDocument();
    expect(screen.getByText("Running custom_tool")).toBeInTheDocument();
    // summary shown when done && summary
    expect(screen.getByText("· s1")).toBeInTheDocument();
    // web_search had null summary -> not shown
    expect(screen.queryByText("· s2")).toBeNull();
    // suggestions hidden when history exists
    expect(screen.queryByText("What are the key points?")).toBeNull();
    // all history tool chips are "done" -> no spinner
    expect(container.querySelector(".spinning")).toBeNull();
  });

  it("ignores empty/whitespace submissions", async () => {
    stub();
    render(<QAPanel {...panelProps} />);
    const input = screen.getByPlaceholderText("Ask anything about this article…");
    await userEvent.type(input, "   ");
    fireEvent.submit(input.closest("form")!);
    expect(streamQAMock).not.toHaveBeenCalled();
  });

  it("streams a full answer, shows pending UI, then revalidates", async () => {
    stub();
    let release: () => void;
    const gate = new Promise<void>((r) => (release = r));
    streamQAMock.mockImplementation(
      async (_id: number, _q: string, onEvent: (e: QAStreamEvent) => void) => {
        onEvent({ type: "status", state: "thinking" }); // no-op branch
        // no `query` arg -> exercises the `?? ""` label fallback
        onEvent({ type: "tool_call", id: "t1", name: "web_search", args: {} });
        await gate;
        // unmatched id -> exercises the false branch of the id ternary
        onEvent({ type: "tool_result", id: "no-match", summary: "ignored" });
        onEvent({ type: "tool_result", id: "t1", summary: "done searching" });
        onEvent({ type: "delta", text: "Hello " });
        onEvent({ type: "delta", text: "world" });
        onEvent({ type: "done", message: makeMsg({ id: 9 }) });
      },
    );

    const { container } = render(<QAPanel {...panelProps} />);
    await userEvent.click(screen.getByText("What are the key points?"));

    // pending: question echoed, tool running (spinner) and typing dots (no live text yet)
    await waitFor(() => expect(screen.getByText("What are the key points?")).toBeInTheDocument());
    expect(container.querySelector(".spinning")).not.toBeNull();
    expect(container.querySelectorAll(".typing-dot")).toHaveLength(3);

    await act(async () => {
      release!();
      await gate;
    });

    await waitFor(() => expect(mutateMock).toHaveBeenCalledWith(KEY));
    expect(streamQAMock).toHaveBeenCalledWith(1, "What are the key points?", expect.any(Function));
  });

  it("renders streamed live text via markdown before completion", async () => {
    stub();
    let release: () => void;
    const gate = new Promise<void>((r) => (release = r));
    streamQAMock.mockImplementation(
      async (_id: number, _q: string, onEvent: (e: QAStreamEvent) => void) => {
        onEvent({ type: "delta", text: "Partial answer" });
        await gate;
        onEvent({ type: "done", message: makeMsg({ id: 5 }) });
      },
    );

    render(<QAPanel {...panelProps} />);
    const input = screen.getByPlaceholderText("Ask anything about this article…");
    await userEvent.type(input, "why?");
    await userEvent.click(screen.getByRole("button", { name: "" }).closest("button")!);

    await waitFor(() => expect(screen.getByText("Partial answer")).toBeInTheDocument());

    await act(async () => {
      release!();
      await gate;
    });
    await waitFor(() => expect(mutateMock).toHaveBeenCalledWith(KEY));
  });

  it("errors when the stream ends without a done event and restores input", async () => {
    stub();
    streamQAMock.mockImplementation(
      async (_id: number, _q: string, onEvent: (e: QAStreamEvent) => void) => {
        onEvent({ type: "delta", text: "cut off" });
        // no "done" event
      },
    );
    render(<QAPanel {...panelProps} />);
    const input = screen.getByPlaceholderText("Ask anything about this article…") as HTMLInputElement;
    await userEvent.type(input, "a question");
    fireEvent.submit(input.closest("form")!);

    await waitFor(() =>
      expect(screen.getByText("The assistant's reply was interrupted")).toBeInTheDocument(),
    );
    // input restored so the user can retry
    expect(input.value).toBe("a question");
    expect(mutateMock).not.toHaveBeenCalled();
  });

  it("shows the rejection message when streamQA throws an Error", async () => {
    stub();
    streamQAMock.mockRejectedValue(new Error("network down"));
    render(<QAPanel {...panelProps} />);
    await userEvent.click(screen.getByText("Why does this matter?"));
    await waitFor(() => expect(screen.getByText("network down")).toBeInTheDocument());
  });

  it("falls back to a generic message for non-Error rejections", async () => {
    stub();
    streamQAMock.mockRejectedValue("string failure");
    render(<QAPanel {...panelProps} />);
    await userEvent.click(screen.getByText("What is the counterargument?"));
    await waitFor(() =>
      expect(screen.getByText("The assistant could not answer")).toBeInTheDocument(),
    );
  });
});
