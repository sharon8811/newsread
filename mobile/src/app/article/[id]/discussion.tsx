import { Ionicons } from "@expo/vector-icons";
import * as WebBrowser from "expo-web-browser";
import { Stack, useLocalSearchParams, useRouter, type Href } from "expo-router";
import { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import useSWR from "swr";

import {
  discussionRefFor,
  fetchHNItem,
  fetchHNThread,
  type DiscussionComment,
  type DiscussionSnapshot,
  type HNItem,
} from "@/lib/discussions";
import { timeAgo } from "@/lib/format";
import { usePalette, type Palette } from "@/lib/theme";
import type { ArticleDetail } from "@/lib/types";

function CommentBranch({
  comment,
  childrenByParent,
  depth,
  colors,
  onDraft,
}: {
  comment: DiscussionComment;
  childrenByParent: Map<number, DiscussionComment[]>;
  depth: number;
  colors: Palette;
  onDraft: (id: number) => void;
}) {
  const replies = childrenByParent.get(comment.id) ?? [];
  return (
    <View
      style={[
        styles.comment,
        depth > 0 && {
          borderLeftWidth: StyleSheet.hairlineWidth,
          borderLeftColor: colors.border,
          marginLeft: depth <= 4 ? 12 : 0,
          paddingLeft: 12,
        },
      ]}
    >
      <View style={styles.commentMeta}>
        <Text style={[styles.author, { color: colors.muted }]}>
          {comment.author ?? (comment.deleted ? "deleted" : "unknown")}
        </Text>
        {comment.created_at && (
          <Text style={[styles.age, { color: colors.muted }]}>{timeAgo(comment.created_at)}</Text>
        )}
        <Pressable onPress={() => onDraft(comment.id)} hitSlop={8} style={styles.draftButton}>
          <Text style={[styles.draftText, { color: colors.tint }]}>Draft reply</Text>
        </Pressable>
      </View>
      <Text
        selectable
        style={[styles.commentText, { color: comment.dead ? colors.muted : colors.text }]}
      >
        {comment.text || (comment.deleted ? "[deleted]" : "[no visible text]")}
      </Text>
      {replies.map((reply) => (
        <CommentBranch
          key={reply.id}
          comment={reply}
          childrenByParent={childrenByParent}
          depth={depth + 1}
          colors={colors}
          onDraft={onDraft}
        />
      ))}
    </View>
  );
}

export default function DiscussionScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { colors } = usePalette();
  const { data: article, error: articleError } = useSWR<ArticleDetail>(
    id ? `/articles/${id}` : null,
  );
  const ref = article ? discussionRefFor(article) : null;
  const {
    data: story,
    error: storyError,
    mutate: refreshStory,
  } = useSWR<HNItem>(
    ref ? ["hackernews-story", ref.id] : null,
    () => fetchHNItem(ref!.id, { fresh: true }),
  );
  const [snapshot, setSnapshot] = useState<DiscussionSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [threadError, setThreadError] = useState<string | null>(null);

  useEffect(() => {
    if (!story) return;
    const controller = new AbortController();
    setLoading(true);
    fetchHNThread(story, 120, controller.signal)
      .then(setSnapshot)
      .catch((error) => {
        if (error instanceof Error && error.name !== "AbortError") setThreadError(error.message);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [story]);

  const childrenByParent = useMemo(() => {
    const map = new Map<number, DiscussionComment[]>();
    for (const comment of snapshot?.comments ?? []) {
      if (comment.parent_id === null) continue;
      map.set(comment.parent_id, [...(map.get(comment.parent_id) ?? []), comment]);
    }
    return map;
  }, [snapshot]);

  const openAssistant = (prompt: string) =>
    router.push({
      pathname: "/article/[id]/discussion-qa",
      params: { id, prompt },
    } as unknown as Href);

  const loadMore = async () => {
    if (!story) return;
    setLoading(true);
    setThreadError(null);
    try {
      setSnapshot(await fetchHNThread(story, 300));
    } catch (error) {
      setThreadError(error instanceof Error ? error.message : "Could not load comments");
    } finally {
      setLoading(false);
    }
  };

  const topLevel = story ? childrenByParent.get(story.id) ?? [] : [];
  const error = articleError || storyError;

  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <Stack.Screen
        options={{
          title: "Hacker News discussion",
          headerBackButtonDisplayMode: "minimal",
          headerRight: () => (
            <Pressable onPress={() => refreshStory()} hitSlop={8}>
              <Ionicons name="refresh-outline" size={21} color={colors.tint} />
            </Pressable>
          ),
        }}
      />
      {!article || (!story && !error) ? (
        <ActivityIndicator style={styles.center} color={colors.tint} />
      ) : error || !ref ? (
        <View style={styles.center}>
          <Text style={{ color: colors.danger, textAlign: "center" }}>
            This Hacker News discussion is unavailable.
          </Text>
        </View>
      ) : (
        <ScrollView contentContainerStyle={styles.content}>
          <Text style={[styles.title, { color: colors.text }]}>{article.title}</Text>
          <Text style={[styles.counts, { color: colors.muted }]}>
            {story?.score ?? 0} points, {story?.descendants ?? 0} comments
          </Text>

          <View style={styles.actions}>
            <Pressable
              style={[styles.primaryButton, { backgroundColor: colors.tint }]}
              onPress={() => openAssistant("Summarize the discussion")}
            >
              <Ionicons name="sparkles-outline" size={17} color={colors.background} />
              <Text style={[styles.primaryLabel, { color: colors.background }]}>Summarize</Text>
            </Pressable>
            <Pressable
              style={[styles.secondaryButton, { borderColor: colors.border }]}
              onPress={() => WebBrowser.openBrowserAsync(ref.canonicalUrl)}
            >
              <Ionicons name="open-outline" size={17} color={colors.tint} />
              <Text style={{ color: colors.tint, fontWeight: "600" }}>Open on HN</Text>
            </Pressable>
          </View>

          {threadError && <Text style={{ color: colors.danger }}>{threadError}</Text>}
          {loading && !snapshot ? <ActivityIndicator color={colors.muted} /> : null}

          {snapshot && (
            <>
              <View style={[styles.thread, { borderTopColor: colors.border }]}>
                {topLevel.length === 0 && (
                  <Text style={[styles.empty, { color: colors.muted }]}>No visible comments yet.</Text>
                )}
                {topLevel.map((comment) => (
                  <CommentBranch
                    key={comment.id}
                    comment={comment}
                    childrenByParent={childrenByParent}
                    depth={0}
                    colors={colors}
                    onDraft={(commentId) =>
                      openAssistant(
                        `Draft a thoughtful reply to comment ${commentId}. My point is: `,
                      )
                    }
                  />
                ))}
              </View>
              <View style={styles.coverage}>
                <Text style={{ color: colors.muted, fontSize: 12 }}>
                  Loaded {snapshot.included_total} of {snapshot.reported_total} comments
                </Text>
                {snapshot.included_total < Math.min(snapshot.reported_total, 300) && (
                  <Pressable
                    style={[styles.secondaryButton, { borderColor: colors.border }]}
                    onPress={loadMore}
                    disabled={loading}
                  >
                    <Text style={{ color: colors.tint, fontWeight: "600" }}>
                      {loading ? "Loading" : "Load more"}
                    </Text>
                  </Pressable>
                )}
              </View>
            </>
          )}
        </ScrollView>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 32 },
  content: { padding: 16, paddingBottom: 48 },
  title: { fontSize: 21, lineHeight: 27, fontWeight: "700" },
  counts: { fontSize: 13, marginTop: 5 },
  actions: { flexDirection: "row", gap: 10, marginTop: 18, marginBottom: 18 },
  primaryButton: {
    minHeight: 42,
    borderRadius: 10,
    paddingHorizontal: 14,
    flexDirection: "row",
    alignItems: "center",
    gap: 7,
  },
  primaryLabel: { fontWeight: "700" },
  secondaryButton: {
    minHeight: 42,
    borderRadius: 10,
    borderWidth: 1,
    paddingHorizontal: 14,
    flexDirection: "row",
    alignItems: "center",
    gap: 7,
  },
  thread: { borderTopWidth: StyleSheet.hairlineWidth },
  empty: { fontSize: 14, paddingVertical: 20 },
  comment: { paddingTop: 15 },
  commentMeta: { flexDirection: "row", alignItems: "center", gap: 8 },
  author: { fontSize: 12, fontWeight: "700" },
  age: { fontSize: 11 },
  draftButton: { marginLeft: "auto", paddingVertical: 4 },
  draftText: { fontSize: 12, fontWeight: "600" },
  commentText: { fontSize: 14, lineHeight: 21, marginTop: 5 },
  coverage: {
    marginTop: 20,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
  },
});
