import type { Article } from "./api";

export type ReadingSessionSnapshot = {
  articles: Article[];
  prevCursor: string | null;
  nextCursor: string | null;
  unreadCount: number | null;
  newAbove: number;
};

export type ReadingReturnAnchor = {
  articleId: number;
  offset: number;
};

const snapshots = new Map<string, ReadingSessionSnapshot>();
const returnAnchors = new Map<string, ReadingReturnAnchor>();

export function readingSessionKey(filter: "all" | "unread", feedId?: string | null) {
  return `${filter}|${feedId ?? ""}`;
}

export function getReadingSession(key: string): ReadingSessionSnapshot | null {
  return snapshots.get(key) ?? null;
}

export function setReadingSession(key: string, snapshot: ReadingSessionSnapshot) {
  snapshots.set(key, snapshot);
}

export function markArticleReadInReadingSessions(articleId: number) {
  for (const [key, snapshot] of snapshots) {
    let changed = false;
    const articles = snapshot.articles.map((article) => {
      if (article.id !== articleId || article.is_read) return article;
      changed = true;
      return { ...article, is_read: true };
    });
    if (!changed) continue;
    snapshots.set(key, {
      ...snapshot,
      articles,
      unreadCount:
        snapshot.unreadCount === null ? null : Math.max(0, snapshot.unreadCount - 1),
    });
  }
}

export function setReadingReturnAnchor(key: string, anchor: ReadingReturnAnchor) {
  returnAnchors.set(key, anchor);
}

export function getReadingReturnAnchor(key: string): ReadingReturnAnchor | null {
  return returnAnchors.get(key) ?? null;
}

export function clearReadingReturnAnchor(key: string) {
  returnAnchors.delete(key);
}

export function clearReadingSessions() {
  snapshots.clear();
  returnAnchors.clear();
}
