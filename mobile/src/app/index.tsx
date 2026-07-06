import { Image } from "expo-image";
import { Stack, useRouter } from "expo-router";
import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { useArticles, type ArticleFilter } from "@/lib/articles";
import { useAuth } from "@/lib/auth";
import { timeAgo } from "@/lib/format";
import { usePalette, type Palette } from "@/lib/theme";
import type { Article } from "@/lib/types";

const FILTERS: { key: ArticleFilter; label: string }[] = [
  { key: "unread", label: "Unread" },
  { key: "all", label: "All" },
  { key: "saved", label: "Saved" },
];

function ArticleRow({ article, colors, onPress }: {
  article: Article;
  colors: Palette;
  onPress: () => void;
}) {
  const dim = article.is_read;
  return (
    <Pressable
      style={({ pressed }) => [
        styles.row,
        { borderBottomColor: colors.border, opacity: pressed ? 0.7 : 1 },
      ]}
      onPress={onPress}
    >
      <View style={styles.rowBody}>
        <Text
          style={[styles.rowTitle, { color: dim ? colors.muted : colors.text }]}
          numberOfLines={2}
        >
          {article.title}
        </Text>
        {(article.summary_short || article.excerpt) !== "" && (
          <Text style={[styles.rowExcerpt, { color: colors.muted }]} numberOfLines={2}>
            {article.summary_short || article.excerpt}
          </Text>
        )}
        <Text style={[styles.rowMeta, { color: colors.muted }]} numberOfLines={1}>
          {!dim && <Text style={{ color: colors.tint }}>● </Text>}
          {article.feed_title}
          {article.published_at ? ` · ${timeAgo(article.published_at)}` : ""}
          {article.is_saved ? " · Saved" : ""}
        </Text>
      </View>
      {article.image_url && (
        <Image
          source={{ uri: article.image_url }}
          style={styles.thumb}
          contentFit="cover"
          transition={150}
        />
      )}
    </Pressable>
  );
}

export default function ArticleListScreen() {
  const router = useRouter();
  const { logout, user } = useAuth();
  const { colors } = usePalette();
  const [filter, setFilter] = useState<ArticleFilter>("unread");
  const { articles, error, isLoading, isValidating, hasMore, loadMore, refresh } =
    useArticles(filter);

  const confirmLogout = () => {
    Alert.alert("Sign out", user ? `Signed in as @${user.username}` : undefined, [
      { text: "Cancel", style: "cancel" },
      { text: "Sign out", style: "destructive", onPress: () => logout() },
    ]);
  };

  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <Stack.Screen
        options={{
          headerRight: () => (
            <Pressable onPress={confirmLogout} hitSlop={12}>
              <Text style={{ color: colors.tint, fontSize: 15 }}>Sign out</Text>
            </Pressable>
          ),
        }}
      />

      <View style={[styles.filters, { borderBottomColor: colors.border }]}>
        {FILTERS.map(({ key, label }) => (
          <Pressable
            key={key}
            style={[
              styles.filterChip,
              { backgroundColor: filter === key ? colors.tint : colors.card },
            ]}
            onPress={() => setFilter(key)}
          >
            <Text
              style={{
                color: filter === key ? colors.background : colors.text,
                fontSize: 14,
                fontWeight: "600",
              }}
            >
              {label}
            </Text>
          </Pressable>
        ))}
      </View>

      {isLoading ? (
        <ActivityIndicator style={styles.center} color={colors.tint} />
      ) : error ? (
        <View style={styles.center}>
          <Text style={[styles.emptyText, { color: colors.danger }]}>
            Couldn't load articles: {error instanceof Error ? error.message : "unknown error"}
          </Text>
          <Pressable onPress={() => refresh()}>
            <Text style={[styles.emptyText, { color: colors.tint, marginTop: 8 }]}>Retry</Text>
          </Pressable>
        </View>
      ) : (
        <FlatList
          data={articles}
          keyExtractor={(article) => String(article.id)}
          renderItem={({ item }) => (
            <ArticleRow
              article={item}
              colors={colors}
              onPress={() => router.push(`/article/${item.id}`)}
            />
          )}
          refreshControl={
            <RefreshControl
              refreshing={isValidating && !hasMore}
              onRefresh={() => refresh()}
              tintColor={colors.muted}
            />
          }
          onEndReached={loadMore}
          onEndReachedThreshold={0.4}
          ListFooterComponent={
            hasMore ? <ActivityIndicator style={styles.footer} color={colors.muted} /> : null
          }
          ListEmptyComponent={
            <View style={styles.center}>
              <Text style={[styles.emptyText, { color: colors.muted }]}>
                {filter === "unread"
                  ? "You're all caught up."
                  : filter === "saved"
                    ? "No saved articles yet."
                    : "No articles yet. Subscribe to feeds in the NewsRead web app to start reading."}
              </Text>
            </View>
          }
          contentContainerStyle={articles.length === 0 ? styles.fill : undefined}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  filters: {
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  filterChip: { borderRadius: 16, paddingHorizontal: 14, paddingVertical: 6 },
  row: {
    flexDirection: "row",
    gap: 12,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  rowBody: { flex: 1, gap: 3 },
  rowTitle: { fontSize: 16, fontWeight: "600", lineHeight: 21 },
  rowExcerpt: { fontSize: 14, lineHeight: 19 },
  rowMeta: { fontSize: 13, marginTop: 2 },
  thumb: { width: 72, height: 72, borderRadius: 8, alignSelf: "center" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 32 },
  fill: { flexGrow: 1 },
  footer: { paddingVertical: 20 },
  emptyText: { fontSize: 15, textAlign: "center", lineHeight: 21 },
});
