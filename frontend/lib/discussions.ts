export type DiscussionRef = {
  provider: "hackernews";
  id: number;
  canonicalUrl: string;
};

export type HNItem = {
  id: number;
  type?: "story" | "comment" | "job" | "poll" | "pollopt";
  by?: string;
  time?: number;
  text?: string;
  kids?: number[];
  score?: number;
  descendants?: number;
  deleted?: boolean;
  dead?: boolean;
};

export type DiscussionComment = {
  id: number;
  parent_id: number | null;
  author: string | null;
  text: string;
  created_at: string | null;
  depth: number;
  position: number;
  deleted: boolean;
  dead: boolean;
};

export type DiscussionSnapshot = {
  provider: "hackernews";
  discussion_id: string;
  fetched_at: string;
  reported_total: number;
  included_total: number;
  comments: DiscussionComment[];
};

export type DiscussionArticle = {
  url: string;
  comments_url: string | null;
};

export type DiscussionAdapter = {
  provider: DiscussionRef["provider"];
  match(article: DiscussionArticle): DiscussionRef | null;
};

const HN_API = "https://hacker-news.firebaseio.com/v0/item";
const itemCache = new Map<number, HNItem>();

function hackerNewsRef(value: string | null): DiscussionRef | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (
      (url.protocol !== "https:" && url.protocol !== "http:") ||
      url.hostname.toLowerCase() !== "news.ycombinator.com" ||
      url.port !== "" ||
      url.pathname.replace(/\/+$/, "") !== "/item"
    ) {
      return null;
    }
    const rawId = url.searchParams.get("id") ?? "";
    if (!/^[1-9]\d*$/.test(rawId)) return null;
    const id = Number(rawId);
    if (!Number.isSafeInteger(id)) return null;
    return {
      provider: "hackernews",
      id,
      canonicalUrl: `https://news.ycombinator.com/item?id=${id}`,
    };
  } catch {
    return null;
  }
}

export const hackerNewsAdapter: DiscussionAdapter = {
  provider: "hackernews",
  match(article) {
    return hackerNewsRef(article.comments_url) ?? hackerNewsRef(article.url);
  },
};

export const discussionAdapters: DiscussionAdapter[] = [hackerNewsAdapter];

export function discussionRefFor(article: DiscussionArticle): DiscussionRef | null {
  for (const adapter of discussionAdapters) {
    const match = adapter.match(article);
    if (match) return match;
  }
  return null;
}

export function hnItemUrl(id: number): string {
  return `${HN_API}/${id}.json`;
}

export async function fetchHNItem(
  id: number,
  options: { signal?: AbortSignal; fresh?: boolean } = {},
): Promise<HNItem> {
  if (!options.fresh) {
    const cached = itemCache.get(id);
    if (cached) return cached;
  }
  const response = await fetch(hnItemUrl(id), {
    cache: "no-store",
    signal: options.signal,
  });
  if (!response.ok) throw new Error(`Hacker News returned HTTP ${response.status}`);
  const item = (await response.json()) as HNItem | null;
  if (!item?.id) throw new Error("This Hacker News item is unavailable");
  itemCache.set(id, item);
  return item;
}

export function hnHtmlToText(value: string | undefined): string {
  if (!value) return "";
  if (typeof DOMParser !== "undefined") {
    const document = new DOMParser().parseFromString(value.replace(/<p>/gi, "\n\n"), "text/html");
    return (document.body.textContent ?? "").replace(/\n{3,}/g, "\n\n").trim();
  }
  return value
    .replace(/<p>/gi, "\n\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&gt;/g, ">")
    .replace(/&lt;/g, "<")
    .replace(/&amp;/g, "&")
    .replace(/&#x27;|&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .trim();
}

type PendingComment = {
  id: number;
  parentId: number;
  depth: number;
  // Index within the parent's kids list — HN display order among siblings,
  // reapplied when the tree is rebuilt (fetch completion order is arbitrary).
  rank: number;
};

const HN_FETCH_CONCURRENCY = 8;

export async function fetchHNThread(
  story: HNItem,
  limit = 300,
  signal?: AbortSignal,
): Promise<DiscussionSnapshot> {
  const cap = Math.max(0, Math.min(limit, 300));
  const queue: PendingComment[] = (story.kids ?? []).map((id, rank) => ({
    id,
    parentId: story.id,
    depth: 0,
    rank,
  }));
  const comments: DiscussionComment[] = [];
  const rankOf = new Map<number, number>();
  let abortError: unknown = null;

  const fetchOne = async (pending: PendingComment) => {
    let item: HNItem | null = null;
    try {
      item = await fetchHNItem(pending.id, { signal });
    } catch (error) {
      if (signal?.aborted) abortError = error;
      return;
    }
    if (!item || comments.length >= cap) return;
    rankOf.set(item.id, pending.rank);
    comments.push({
      id: item.id,
      parent_id: pending.parentId,
      author: item.by ?? null,
      text: hnHtmlToText(item.text),
      created_at: item.time ? new Date(item.time * 1000).toISOString() : null,
      depth: Math.min(pending.depth, 64),
      position: comments.length,
      deleted: Boolean(item.deleted),
      dead: Boolean(item.dead),
    });
    queue.push(
      ...(item.kids ?? []).map((id, rank) => ({
        id,
        parentId: item.id,
        depth: Math.min(pending.depth + 1, 64),
        rank,
      })),
    );
  };

  // Rolling pool rather than synchronized batches: up to 8 fetches stay
  // continuously in flight, so one slow item never stalls the other seven.
  const inFlight = new Set<Promise<void>>();
  while (
    (queue.length > 0 || inFlight.size > 0) &&
    comments.length < cap &&
    !abortError
  ) {
    while (queue.length > 0 && inFlight.size < HN_FETCH_CONCURRENCY) {
      const pending = queue.shift()!;
      const task = fetchOne(pending).finally(() => inFlight.delete(task));
      inFlight.add(task);
    }
    if (inFlight.size > 0) await Promise.race(inFlight);
  }
  if (abortError) throw abortError;

  const childrenByParent = new Map<number, DiscussionComment[]>();
  for (const comment of comments) {
    if (comment.parent_id === null) continue;
    let siblings = childrenByParent.get(comment.parent_id);
    if (!siblings) childrenByParent.set(comment.parent_id, (siblings = []));
    siblings.push(comment);
  }
  for (const siblings of childrenByParent.values()) {
    siblings.sort((a, b) => (rankOf.get(a.id) ?? 0) - (rankOf.get(b.id) ?? 0));
  }
  const ordered: DiscussionComment[] = [];
  const visit = (parentId: number) => {
    for (const comment of childrenByParent.get(parentId) ?? []) {
      ordered.push({ ...comment, position: ordered.length });
      visit(comment.id);
    }
  };
  visit(story.id);
  let remainingText = 120_000;
  const bounded = ordered.map((comment) => {
    const text = comment.text.slice(0, Math.min(8_000, remainingText));
    remainingText -= text.length;
    return { ...comment, text };
  });

  return {
    provider: "hackernews",
    discussion_id: String(story.id),
    fetched_at: new Date().toISOString(),
    reported_total: story.descendants ?? comments.length,
    included_total: bounded.length,
    comments: bounded,
  };
}
