import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AiSummary from "@/components/AiSummary";
import type { User } from "@/lib/api";
import { makeArticleDetail, makeUser } from "./fixtures";

const { swrMock, mutateMock, updateUserMock } = vi.hoisted(() => ({
  swrMock: vi.fn(),
  mutateMock: vi.fn(),
  updateUserMock: vi.fn(),
}));
vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));

const authState: { user: User | null } = { user: makeUser() };
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: authState.user, updateUser: updateUserMock }),
}));

const LANGUAGES = [
  { code: "he", name: "Hebrew", native_name: "עברית", rtl: true },
  { code: "fr", name: "French", native_name: "Français", rtl: false },
];

/** The component reads two SWR keys (status, languages), so the mock answers
 * by key rather than returning one shape for every call. */
function stubStatus(configured: boolean, { translation = false } = {}) {
  swrMock.mockImplementation((key: string | null) => {
    if (key === null) return { data: undefined }; // SWR skips null keys
    if (key === "/translation/languages") return { data: LANGUAGES };
    return { data: { configured, model: "m", search: false, search_provider: null, translation } };
  });
}

function okFetch() {
  return vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => ({}) });
}

describe("<AiSummary>", () => {
  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
    updateUserMock.mockClear();
    authState.user = makeUser();
  });

  it("renders nothing when AI is not configured", () => {
    stubStatus(false);
    const { container } = render(<AiSummary article={makeArticleDetail()} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows an existing summary", () => {
    stubStatus(true);
    vi.stubGlobal("fetch", okFetch());
    render(<AiSummary article={makeArticleDetail({ summary: "the summary text", summary_model: "gpt" })} />);
    expect(screen.getByText("the summary text")).toBeInTheDocument();
    expect(screen.getByText("AI Summary")).toBeInTheDocument();
  });

  it("renders markdown lists, converting legacy '•' bullets too", () => {
    stubStatus(true);
    vi.stubGlobal("fetch", okFetch());
    render(
      <AiSummary
        article={makeArticleDetail({
          summary: "Core takeaway.\n\n- **First** point\n• legacy point",
        })}
      />,
    );
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("First point");
    expect(items[1]).toHaveTextContent("legacy point");
    expect(screen.getByText("First").tagName).toBe("STRONG");
  });

  it("renders markdown tables", () => {
    stubStatus(true);
    vi.stubGlobal("fetch", okFetch());
    render(
      <AiSummary
        article={makeArticleDetail({
          summary:
            "Intro.\n\n| State | Definition |\n| --- | --- |\n| Virginia | monetary only |",
        })}
      />,
    );
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("Virginia")).toBeInTheDocument();
  });

  it("auto-generates when there is no summary yet", async () => {
    stubStatus(true);
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<AiSummary article={makeArticleDetail({ summary: "" })} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][0]).toContain("/articles/1/summarize");
    await waitFor(() => expect(mutateMock).toHaveBeenCalledWith("/articles/1"));
  });

  it("explains a too-short source without requesting a summary", async () => {
    stubStatus(true);
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(
      <AiSummary
        article={makeArticleDetail({ summary: "", summary_skipped_reason: "too_short" })}
      />,
    );
    expect(
      screen.getByText("This post is already short, so there’s no AI summary."),
    ).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(mutateMock).not.toHaveBeenCalled();
    expect(screen.queryByTitle("Regenerate summary")).not.toBeInTheDocument();
  });

  it("regenerates on the refresh button (force=true)", async () => {
    stubStatus(true);
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<AiSummary article={makeArticleDetail({ summary: "old summary" })} />);
    await userEvent.click(screen.getByTitle("Regenerate summary"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("force=true"))).toBe(true);
  });

  it("shows an error and retries", async () => {
    stubStatus(true);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ status: 502, ok: false, json: async () => ({ detail: "LLM failed" }) })
      .mockResolvedValue({ status: 200, ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    render(<AiSummary article={makeArticleDetail({ summary: "" })} />);
    await waitFor(() => expect(screen.getByText("LLM failed")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Try again"));
    await waitFor(() => expect(mutateMock).toHaveBeenCalled());
  });
});

describe("<AiSummary> translation", () => {
  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
    updateUserMock.mockClear();
    authState.user = makeUser();
  });

  function translatedFetch(text = "ההצבעה נדחתה") {
    return vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/translate"))
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            language: "he",
            text,
            model: "free-model",
            cached: false,
            translated: true,
            source_language: "English",
          }),
          headers: new Headers(),
        });
      return Promise.resolve({ ok: true, status: 200, json: async () => makeUser({ translation_language: "he" }), headers: new Headers() });
    });
  }

  it("offers no translate control when no translation model is configured", () => {
    stubStatus(true, { translation: false });
    vi.stubGlobal("fetch", okFetch());
    render(<AiSummary article={makeArticleDetail({ summary: "the summary" })} />);
    expect(screen.queryByText("translate summary")).not.toBeInTheDocument();
  });

  it("asks for a language the first time, then translates and saves the default", async () => {
    stubStatus(true, { translation: true });
    const fetchMock = translatedFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<AiSummary article={makeArticleDetail({ summary: "the original summary" })} />);

    await userEvent.click(screen.getByText("translate summary"));
    await userEvent.click(screen.getByText("Hebrew"));

    await waitFor(() => expect(screen.getByText("ההצבעה נדחתה")).toBeInTheDocument());
    const translateCall = fetchMock.mock.calls.find(([u]) => String(u).includes("/translate"))!;
    expect(JSON.parse(translateCall[1].body)).toEqual({ language: "he" });
    // ...and the choice becomes the reader's default, so it is asked only once.
    const patchCall = fetchMock.mock.calls.find(([u]) => String(u).includes("/users/me"))!;
    expect(JSON.parse(patchCall[1].body)).toEqual({ translation_language: "he" });
    expect(updateUserMock).toHaveBeenCalled();
  });

  it("uses the saved language without asking again", async () => {
    stubStatus(true, { translation: true });
    authState.user = makeUser({ translation_language: "he" });
    const fetchMock = translatedFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<AiSummary article={makeArticleDetail({ summary: "the original summary" })} />);

    await userEvent.click(screen.getByText("translate to hebrew"));

    await waitFor(() => expect(screen.getByText("ההצבעה נדחתה")).toBeInTheDocument());
    expect(screen.queryByText("Choose a language")).not.toBeInTheDocument();
    // A repeat translation must not re-save the default it already matches.
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/users/me"))).toBe(false);
  });

  it("switches back to the original and forward again", async () => {
    stubStatus(true, { translation: true });
    authState.user = makeUser({ translation_language: "he" });
    vi.stubGlobal("fetch", translatedFetch());
    render(<AiSummary article={makeArticleDetail({ summary: "the original summary" })} />);

    await userEvent.click(screen.getByText("translate to hebrew"));
    await waitFor(() => expect(screen.getByText("ההצבעה נדחתה")).toBeInTheDocument());

    await userEvent.click(screen.getByText("show original"));
    expect(screen.getByText("the original summary")).toBeInTheDocument();

    await userEvent.click(screen.getByText("show hebrew"));
    expect(screen.getByText("ההצבעה נדחתה")).toBeInTheDocument();
  });

  it("keeps the original summary when the translation fails", async () => {
    stubStatus(true, { translation: true });
    authState.user = makeUser({ translation_language: "he" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        json: async () => ({ detail: "The LLM request failed" }),
        headers: new Headers(),
      }),
    );
    render(<AiSummary article={makeArticleDetail({ summary: "the original summary" })} />);

    await userEvent.click(screen.getByText("translate to hebrew"));

    await waitFor(() => expect(screen.getByText("The LLM request failed")).toBeInTheDocument());
    expect(screen.getByText("the original summary")).toBeInTheDocument();
  });

  it("says so when the summary is already in the target language", async () => {
    stubStatus(true, { translation: true });
    authState.user = makeUser({ translation_language: "he" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          language: "he",
          text: "הסיכום המקורי",
          model: null,
          cached: false,
          translated: false,
          source_language: "Hebrew",
        }),
        headers: new Headers(),
      }),
    );
    render(<AiSummary article={makeArticleDetail({ summary: "הסיכום המקורי" })} />);

    await userEvent.click(screen.getByText("translate to hebrew"));

    await waitFor(() =>
      expect(screen.getByText("This summary is already in Hebrew.")).toBeInTheDocument(),
    );
  });

  it("lets the reader pick another language without changing the default", async () => {
    stubStatus(true, { translation: true });
    authState.user = makeUser({ translation_language: "he" });
    const fetchMock = translatedFetch("la traduction");
    vi.stubGlobal("fetch", fetchMock);
    render(<AiSummary article={makeArticleDetail({ summary: "the original summary" })} />);

    await userEvent.click(screen.getByText("another language"));
    await userEvent.click(screen.getByText("French"));

    await waitFor(() => expect(screen.getByText("la traduction")).toBeInTheDocument());
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/users/me"))).toBe(false);
  });

  it("can promote a one-off language to the default", async () => {
    stubStatus(true, { translation: true });
    authState.user = makeUser({ translation_language: "he" });
    const fetchMock = translatedFetch("la traduction");
    vi.stubGlobal("fetch", fetchMock);
    render(<AiSummary article={makeArticleDetail({ summary: "the original summary" })} />);

    await userEvent.click(screen.getByText("another language"));
    await userEvent.click(screen.getByLabelText("Make this my default language"));
    await userEvent.click(screen.getByText("French"));

    await waitFor(() => expect(screen.getByText("la traduction")).toBeInTheDocument());
    const patchCall = fetchMock.mock.calls.find(([u]) => String(u).includes("/users/me"))!;
    expect(JSON.parse(patchCall[1].body)).toEqual({ translation_language: "fr" });
  });

  it("still shows a stored summary when only translation is configured", async () => {
    // A server can have a translation model but no summarizing one: the stored
    // summary and its translate action must not disappear with it.
    stubStatus(false, { translation: true });
    authState.user = makeUser({ translation_language: "he" });
    const fetchMock = translatedFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<AiSummary article={makeArticleDetail({ summary: "the original summary" })} />);

    expect(screen.getByText("the original summary")).toBeInTheDocument();
    // ...but there is no model to regenerate with, so that action stays hidden.
    expect(screen.queryByTitle("Regenerate summary")).not.toBeInTheDocument();

    await userEvent.click(screen.getByText("translate to hebrew"));
    await waitFor(() => expect(screen.getByText("ההצבעה נדחתה")).toBeInTheDocument());
  });

  it("does not name the translation model in the reading view", async () => {
    stubStatus(true, { translation: true });
    authState.user = makeUser({ translation_language: "he" });
    vi.stubGlobal("fetch", translatedFetch());
    render(<AiSummary article={makeArticleDetail({ summary: "the original summary" })} />);

    await userEvent.click(screen.getByText("translate to hebrew"));
    await waitFor(() => expect(screen.getByText("ההצבעה נדחתה")).toBeInTheDocument());
    expect(screen.queryByText("free-model")).not.toBeInTheDocument();
  });

  it("says so when the language list can't be loaded", async () => {
    stubStatus(true, { translation: true });
    swrMock.mockImplementation((key: string | null) => {
      if (key === null) return { data: undefined };
      if (key === "/translation/languages")
        return { data: undefined, error: new Error("offline") };
      return { data: { configured: true, model: "m", search: false, translation: true } };
    });
    vi.stubGlobal("fetch", okFetch());
    render(<AiSummary article={makeArticleDetail({ summary: "the original summary" })} />);

    await userEvent.click(screen.getByText("translate summary"));

    expect(
      screen.getByText("Couldn’t load the language list. Check your connection and try again."),
    ).toBeInTheDocument();
  });

  it("renders nothing when neither model is configured", () => {
    stubStatus(false, { translation: false });
    const { container } = render(
      <AiSummary article={makeArticleDetail({ summary: "the original summary" })} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("lays the original out in the article's own direction", () => {
    stubStatus(true, { translation: true });
    vi.stubGlobal("fetch", okFetch());
    const { container, rerender } = render(
      <AiSummary article={makeArticleDetail({ summary: "the summary", rtl: false })} />,
    );
    expect(container.querySelector(".summary-md")).toHaveAttribute("dir", "ltr");

    rerender(<AiSummary article={makeArticleDetail({ summary: "הסיכום", rtl: true })} />);
    expect(container.querySelector(".summary-md")).toHaveAttribute("dir", "rtl");
  });

  it("lays a translation out in its target language's direction", async () => {
    // The reported bug: a Hebrew translation that opens with a Latin brand
    // name ("OpenAI משיקה…") rendered left to right, because the direction was
    // inferred from the text's first strong character rather than the language.
    stubStatus(true, { translation: true });
    authState.user = makeUser({ translation_language: "he" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          language: "he",
          text: "OpenAI משיקה יכולות פרסום ישירות בתוך ChatGPT",
          rtl: true,
          model: "free-model",
          cached: false,
          translated: true,
          source_language: "English",
        }),
        headers: new Headers(),
      }),
    );
    const { container } = render(
      <AiSummary article={makeArticleDetail({ summary: "OpenAI launches ads", rtl: false })} />,
    );
    expect(container.querySelector(".summary-md")).toHaveAttribute("dir", "ltr");

    await userEvent.click(screen.getByText("translate to hebrew"));

    await waitFor(() =>
      expect(container.querySelector(".summary-md")).toHaveAttribute("dir", "rtl"),
    );
    // ...and back to the English original, left to right again.
    await userEvent.click(screen.getByText("show original"));
    expect(container.querySelector(".summary-md")).toHaveAttribute("dir", "ltr");
  });
});
