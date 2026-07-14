import {
  markArticleOpened,
  nextUnreadIndex,
  passedArticleIds,
} from "../readingList";
import type { Article } from "../types";

function article(id: number, is_read = false): Article {
  return { id, is_read } as Article;
}

describe("passedArticleIds", () => {
  const list = [article(1, true), article(2), article(3), article(4)];

  it("collects unread articles above the first visible index", () => {
    expect(passedArticleIds(list, 3)).toEqual([2, 3]);
  });

  it("returns nothing at the top of the list", () => {
    expect(passedArticleIds(list, 0)).toEqual([]);
  });

  it("clamps past the end of the window (fast fling to the bottom)", () => {
    expect(passedArticleIds(list, 99)).toEqual([2, 3, 4]);
  });

  it("skips already-read articles", () => {
    expect(passedArticleIds(list, 1)).toEqual([]);
  });
});

describe("reading-list navigation", () => {
  it("marks an opened row read without removing or reordering it", () => {
    const list = [article(1), article(2), article(3)];
    const updated = markArticleOpened(list, 2);

    expect(updated.map((item) => item.id)).toEqual([1, 2, 3]);
    expect(updated[1].is_read).toBe(true);
  });

  it("jumps only to unread rows below the viewport", () => {
    const list = [article(1), article(2, true), article(3), article(4)];

    expect(nextUnreadIndex(list, 2)).toBe(3);
    expect(nextUnreadIndex(list, 3)).toBe(-1);
  });
});
