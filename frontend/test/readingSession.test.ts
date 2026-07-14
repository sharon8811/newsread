import { beforeEach, describe, expect, it } from "vitest";
import {
  clearReadingReturnAnchor,
  clearReadingSessions,
  getLatestReadingReturnAnchor,
  getReadingReturnAnchor,
  getReadingSession,
  markArticleReadInReadingSessions,
  readingSessionKey,
  setReadingReturnAnchor,
  setReadingSession,
} from "@/lib/readingSession";
import { makeArticle } from "./fixtures";

beforeEach(() => clearReadingSessions());

describe("readingSession", () => {
  it("keeps read rows in the window while updating the unread count", () => {
    const key = readingSessionKey("unread", "7");
    setReadingSession(key, {
      articles: [makeArticle({ id: 1 }), makeArticle({ id: 2 })],
      prevCursor: "previous",
      nextCursor: "next",
      unreadCount: 2,
      newAbove: 0,
    });

    markArticleReadInReadingSessions(1);

    expect(getReadingSession(key)?.articles.map((article) => article.id)).toEqual([1, 2]);
    expect(getReadingSession(key)?.articles[0].is_read).toBe(true);
    expect(getReadingSession(key)?.unreadCount).toBe(1);
  });

  it("stores and consumes a return anchor independently per list", () => {
    const key = readingSessionKey("all");
    setReadingReturnAnchor(key, { articleId: 42, offset: 180 });

    expect(getReadingReturnAnchor(key)).toEqual({ articleId: 42, offset: 180 });
    expect(getLatestReadingReturnAnchor()).toEqual({
      key,
      anchor: { articleId: 42, offset: 180 },
    });
    clearReadingReturnAnchor(key);
    expect(getReadingReturnAnchor(key)).toBeNull();
    expect(getLatestReadingReturnAnchor()).toBeNull();
  });
});
