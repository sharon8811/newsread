import { Image } from "expo-image";
import { useRouter } from "expo-router";
import { useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import useSWR from "swr";

import GeneratingImage from "@/components/GeneratingImage";
import { api, imageSrc } from "@/lib/api";
import { useArticles } from "@/lib/articles";
import { timeAgo } from "@/lib/format";
import { usePalette, type Palette } from "@/lib/theme";
import type { Article, ArticleDetail, ImportFeed } from "@/lib/types";

function Row({ article, colors, onPress }: {
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
          {article.published_at ? timeAgo(article.published_at) : ""}
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

export default function ImportedScreen() {
  const router = useRouter();
  const { colors } = usePalette();
  // Created server-side on first call; its id scopes the list below.
  const { data: importFeed } = useSWR<ImportFeed>("/imports/feed");
  const feedId = importFeed?.feed_id ?? null;
  const { articles, error, isLoading, hasMore, loadMore, refresh } = useArticles(
    feedId ? "all" : null,
    feedId,
  );

  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const submit = async () => {
    const trimmed = url.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setImportError(null);
    try {
      const article = await api<ArticleDetail>("/imports", {
        method: "POST",
        body: { url: trimmed },
      });
      setUrl("");
      refresh();
      router.push(`/article/${article.id}`);
    } catch (err) {
      setImportError(err instanceof Error ? err.message : "Could not import that link");
    } finally {
      setBusy(false);
    }
  };

  const onRefresh = async () => {
    setRefreshing(true);
    try {
      await refresh();
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <View style={[styles.addBar, { borderBottomColor: colors.border }]}>
        <TextInput
          style={[
            styles.input,
            { backgroundColor: colors.card, borderColor: colors.border, color: colors.text },
          ]}
          placeholder="Paste a link to import…"
          placeholderTextColor={colors.muted}
          value={url}
          onChangeText={setUrl}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          returnKeyType="go"
          onSubmitEditing={submit}
        />
        <Pressable
          style={[
            styles.importButton,
            { backgroundColor: colors.tint, opacity: !url.trim() || busy ? 0.5 : 1 },
          ]}
          disabled={!url.trim() || busy}
          onPress={submit}
        >
          {busy ? (
            <ActivityIndicator size="small" color={colors.background} />
          ) : (
            <Text style={[styles.importLabel, { color: colors.background }]}>Import</Text>
          )}
        </Pressable>
      </View>
      {importError && (
        <Text style={[styles.error, { color: colors.danger }]}>{importError}</Text>
      )}

      {isLoading || feedId === null ? (
        <ActivityIndicator style={styles.center} color={colors.tint} />
      ) : error ? (
        <View style={styles.center}>
          <Text style={[styles.emptyText, { color: colors.danger }]}>
            Couldn't load imports: {error instanceof Error ? error.message : "unknown error"}
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
            <Row
              article={item}
              colors={colors}
              onPress={() => router.push(`/article/${item.id}`)}
            />
          )}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
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
                Nothing imported yet. Paste a link above to save and summarize
                any page from around the web.
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
  addBar: {
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  input: {
    flex: 1,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 15,
  },
  importButton: {
    borderRadius: 8,
    paddingHorizontal: 16,
    justifyContent: "center",
    minWidth: 76,
    alignItems: "center",
  },
  importLabel: { fontSize: 14, fontWeight: "700" },
  error: { paddingHorizontal: 16, paddingTop: 8, fontSize: 13 },
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
