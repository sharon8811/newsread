import { Ionicons } from "@expo/vector-icons";
import { Image } from "expo-image";
import { Stack, useRouter } from "expo-router";
import { useEffect, useRef, useState } from "react";
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

import GeneratingImage from "@/components/GeneratingImage";
import StoriesView from "@/components/StoriesView";
import { api, imageSrc } from "@/lib/api";
import { useArticles, type ArticleFilter } from "@/lib/articles";
import { useAuth } from "@/lib/auth";
import { timeAgo } from "@/lib/format";
import { usePalette, type Palette } from "@/lib/theme";
import type { Article, ViewMode } from "@/lib/types";

const FILTERS: { key: ArticleFilter; label: string }[] = [
  { key: "unread", label: "Unread" },
  { key: "all", label: "All" },
  { key: "saved", label: "Saved" },
];

const MODE_ORDER: ViewMode[] = ["cards", "list", "stories"];
const MODE_ICON: Record<ViewMode, keyof typeof Ionicons.glyphMap> = {
  cards: "grid-outline",
  list: "list-outline",
  stories: "albums-outline",
};

function ListRow({ article, colors, onPress }: {
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
      {article.image_url ? (
        <Image
          source={{ uri: imageSrc(article.image_url) }}
          style={styles.thumb}
          contentFit="cover"
          transition={150}
        />
      ) : article.image_pending ? (
        <GeneratingImage colors={colors} style={styles.thumb} compact />
      ) : null}
    </Pressable>
  );
}

function CardRow({ article, colors, onPress }: {
  article: Article;
  colors: Palette;
  onPress: () => void;
}) {
  const dim = article.is_read;
  return (
    <Pressable
      style={({ pressed }) => [
        styles.card,
        {
          backgroundColor: colors.card,
          borderColor: colors.border,
          opacity: pressed ? 0.7 : 1,
        },
      ]}
      onPress={onPress}
    >
      {article.image_url ? (
        <Image
          source={{ uri: imageSrc(article.image_url) }}
          style={[styles.cardImage, dim && { opacity: 0.55 }]}
          contentFit="cover"
          transition={150}
        />
      ) : article.image_pending ? (
        <GeneratingImage colors={colors} style={styles.cardImage} />
      ) : null}
      <View style={styles.cardBody}>
        <Text style={[styles.rowMeta, { color: colors.muted }]} numberOfLines={1}>
          {!dim && <Text style={{ color: colors.tint }}>● </Text>}
          {article.feed_title}
          {article.published_at ? ` · ${timeAgo(article.published_at)}` : ""}
          {article.is_saved ? " · Saved" : ""}
        </Text>
        <Text
          style={[styles.cardTitle, { color: dim ? colors.muted : colors.text }]}
          numberOfLines={3}
        >
          {article.title}
        </Text>
        {(article.summary_short || article.excerpt) !== "" && (
          <Text style={[styles.cardExcerpt, { color: colors.muted }]} numberOfLines={3}>
            {article.summary_short || article.excerpt}
          </Text>
        )}
      </View>
    </Pressable>
  );
}

export default function ArticleListScreen() {
  const router = useRouter();
  const { logout, user } = useAuth();
  const { colors } = usePalette();
  const [filter, setFilter] = useState<ArticleFilter>("unread");
  const [mode, setMode] = useState<ViewMode>("list");
  const modeBeforeStories = useRef<ViewMode>("list");
  const appliedDefault = useRef(false);
  const { articles, error, isLoading, isValidating, hasMore, loadMore, refresh } =
    useArticles(filter);

  // Start in the user's default view once the profile has loaded.
  useEffect(() => {
    if (user && !appliedDefault.current) {
      appliedDefault.current = true;
      setMode(user.default_view);
    }
  }, [user]);

  const cycleMode = () => {
    const next = MODE_ORDER[(MODE_ORDER.indexOf(mode) + 1) % MODE_ORDER.length];
    if (next === "stories") modeBeforeStories.current = mode;
    setMode(next);
    // Same as the web's "make default" when browsing without a feed context.
    api("/users/me", { method: "PATCH", body: { default_view: next } }).catch(() => {});
  };

  const exitStories = () => {
    setMode(modeBeforeStories.current === "stories" ? "list" : modeBeforeStories.current);
    refresh(); // one revalidation on exit, like the web StoriesView
  };

  const markRead = (article: Article) => {
    if (article.is_read) return;
    api(`/articles/${article.id}/state`, { method: "POST", body: { is_read: true } }).catch(
      () => {},
    );
  };

  const confirmLogout = () => {
    Alert.alert("Sign out", user ? `Signed in as @${user.username}` : undefined, [
      { text: "Cancel", style: "cancel" },
      { text: "Sign out", style: "destructive", onPress: () => logout() },
    ]);
  };

  const openArticle = (article: Article) => router.push(`/article/${article.id}`);

  if (mode === "stories" && !isLoading && !error) {
    return (
      <>
        <Stack.Screen options={{ headerShown: false }} />
        <StoriesView
          articles={articles}
          onOpen={openArticle}
          onMarkRead={markRead}
          onExit={exitStories}
        />
      </>
    );
  }

  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <Stack.Screen
        options={{
          headerShown: true,
          headerRight: () => (
            <View style={styles.headerButtons}>
              <Pressable onPress={() => router.push("/catalog")} hitSlop={8}>
                <Ionicons name="compass-outline" size={22} color={colors.tint} />
              </Pressable>
              <Pressable onPress={cycleMode} hitSlop={8}>
                <Ionicons name={MODE_ICON[mode]} size={22} color={colors.tint} />
              </Pressable>
              <Pressable onPress={confirmLogout} hitSlop={8}>
                <Ionicons name="log-out-outline" size={22} color={colors.tint} />
              </Pressable>
            </View>
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
          renderItem={({ item }) =>
            mode === "list" ? (
              <ListRow article={item} colors={colors} onPress={() => openArticle(item)} />
            ) : (
              <CardRow article={item} colors={colors} onPress={() => openArticle(item)} />
            )
          }
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
                    : "No articles yet. Subscribe to feeds to start reading."}
              </Text>
              {filter === "all" && (
                <Pressable onPress={() => router.push("/catalog")}>
                  <Text style={[styles.emptyText, { color: colors.tint, marginTop: 8 }]}>
                    Browse the feed catalog
                  </Text>
                </Pressable>
              )}
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
  headerButtons: { flexDirection: "row", gap: 18, alignItems: "center" },
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
  card: {
    marginHorizontal: 16,
    marginTop: 14,
    borderRadius: 12,
    borderWidth: StyleSheet.hairlineWidth,
    overflow: "hidden",
  },
  cardImage: { width: "100%", aspectRatio: 16 / 9 },
  cardBody: { gap: 5, paddingHorizontal: 14, paddingVertical: 12 },
  cardTitle: { fontSize: 18, fontWeight: "600", lineHeight: 24 },
  cardExcerpt: { fontSize: 14.5, lineHeight: 20 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 32 },
  fill: { flexGrow: 1 },
  footer: { paddingVertical: 20 },
  emptyText: { fontSize: 15, textAlign: "center", lineHeight: 21 },
});
