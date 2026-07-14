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

  it("leaves sessions unchanged when the article is absent or already read", () => {
    const key = readingSessionKey("unread");
    const snapshot = {
      articles: [
        makeArticle({ id: 1, is_read: true }),
        makeArticle({ id: 2, is_read: false }),
      ],
      prevCursor: null,
      nextCursor: null,
      unreadCount: 1,
      newAbove: 0,
    };
    setReadingSession(key, snapshot);

    markArticleReadInReadingSessions(99);
    markArticleReadInReadingSessions(1);

    expect(getReadingSession(key)).toBe(snapshot);
    expect(getReadingSession(key)?.unreadCount).toBe(1);
  });

  it("preserves an unknown unread count when marking a cached row read", () => {
    const key = readingSessionKey("all");
    setReadingSession(key, {
      articles: [makeArticle({ id: 3, is_read: false })],
      prevCursor: null,
      nextCursor: null,
      unreadCount: null,
      newAbove: 0,
    });

    markArticleReadInReadingSessions(3);

    expect(getReadingSession(key)?.articles[0].is_read).toBe(true);
    expect(getReadingSession(key)?.unreadCount).toBeNull();
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

  it("clearing an older list anchor keeps the latest return target", () => {
    const olderKey = readingSessionKey("all");
    const latestKey = readingSessionKey("unread", "9");
    setReadingReturnAnchor(olderKey, { articleId: 1, offset: 20 });
    setReadingReturnAnchor(latestKey, { articleId: 2, offset: 40 });

    clearReadingReturnAnchor(olderKey);

    expect(getReadingReturnAnchor(olderKey)).toBeNull();
    expect(getLatestReadingReturnAnchor()).toEqual({
      key: latestKey,
      anchor: { articleId: 2, offset: 40 },
    });
  });
});
