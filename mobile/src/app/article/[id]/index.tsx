import { Ionicons } from "@expo/vector-icons";
import { Image } from "expo-image";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import * as WebBrowser from "expo-web-browser";
import { useEffect, useMemo, useRef } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  Share,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from "react-native";
import Markdown from "react-native-markdown-display";
import RenderHTML, { type MixedStyleDeclaration } from "react-native-render-html";
import useSWR from "swr";

import GeneratingImage from "@/components/GeneratingImage";
import RelatedCoverage from "@/components/RelatedCoverage";
import { api, imageSrc } from "@/lib/api";
import { discussionRefFor, fetchHNItem } from "@/lib/discussions";
import { timeAgo } from "@/lib/format";
import { useReadingTimer } from "@/lib/useReadingTimer";
import { usePalette, type Palette } from "@/lib/theme";
import type { AiStatus, ArticleDetail } from "@/lib/types";

const openLink = (url: string) => WebBrowser.openBrowserAsync(url).catch(() => {});

/** Summaries generated before the markdown prompt use "• " bullet lines —
 * rewrite them into list items so they render the same as new ones. */
const asMarkdown = (summary: string) => summary.replace(/^[ \t]*•\s*/gm, "- ");

/** Styles for the AI summary markdown (bullets, bold, small tables). */
function summaryMdStyles(colors: Palette) {
  return StyleSheet.create({
    body: { color: colors.text, fontSize: 15, lineHeight: 22 },
    paragraph: { marginTop: 0, marginBottom: 8 },
    bullet_list: { marginBottom: 8 },
    ordered_list: { marginBottom: 8 },
    list_item: { marginBottom: 4 },
    table: {
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 6,
      marginBottom: 8,
    },
    thead: { backgroundColor: colors.card },
    th: { padding: 6, fontWeight: "600", fontSize: 13 },
    tr: { borderColor: colors.border, flexDirection: "row" },
    td: { padding: 6, fontSize: 13 },
    code_inline: { backgroundColor: colors.card, borderRadius: 4, fontSize: 13 },
    link: { color: colors.tint },
  });
}

function htmlStyles(colors: Palette, isDark: boolean): Record<string, MixedStyleDeclaration> {
  return {
    a: { color: colors.tint },
    pre: {
      backgroundColor: colors.card,
      padding: 12,
      borderRadius: 8,
      fontSize: 14,
    },
    code: { fontSize: 14, backgroundColor: colors.card },
    blockquote: {
      borderLeftWidth: 3,
      borderLeftColor: colors.border,
      marginLeft: 0,
      paddingLeft: 14,
      color: colors.muted,
    },
    figcaption: { color: colors.muted, fontSize: 13 },
    hr: { backgroundColor: colors.border, height: StyleSheet.hairlineWidth },
    img: { borderRadius: 8 },
    h1: { fontSize: 24, lineHeight: 30 },
    h2: { fontSize: 21, lineHeight: 27 },
    h3: { fontSize: 18, lineHeight: 24 },
  };
}

export default function ArticleScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { colors, isDark } = usePalette();
  const { width } = useWindowDimensions();
  // While an AI illustration renders in the background, poll the detail so the
  // image appears the moment it lands (and the "generating" state clears if it
  // fails). Server-side pending stops reporting after ~3min, which ends the
  // poll on its own. Mirrors the web article view.
  const { data, error, mutate } = useSWR<ArticleDetail>(id ? `/articles/${id}` : null, {
    refreshInterval: (latest) => (latest?.image_pending && !latest.image_url ? 3000 : 0),
  });
  const { data: ai } = useSWR<AiStatus>("/ai/status");
  const discussionRef = data ? discussionRefFor(data) : null;
  // The fetcher reads the item id from the SWR key args rather than the
  // render closure, so it can never deref a discussionRef the key no longer
  // matches.
  const { data: hnStory } = useSWR(
    discussionRef ? ["hackernews-story", discussionRef.id] : null,
    ([, itemId]: [string, number]) => fetchHNItem(itemId, { fresh: true }),
  );
  const markedRead = useRef(false);

  useReadingTimer(data?.id);

  // Opening an article marks it read, like the web app's article view.
  useEffect(() => {
    if (!data || data.is_read || markedRead.current) return;
    markedRead.current = true;
    api(`/articles/${data.id}/state`, { method: "POST", body: { is_read: true } })
      .then(() => mutate({ ...data, is_read: true }, { revalidate: false }))
      .catch(() => {});
  }, [data, mutate]);

  const toggleSaved = async () => {
    if (!data) return;
    const saved = !data.is_saved;
    mutate({ ...data, is_saved: saved }, { revalidate: false });
    try {
      await api(`/articles/${data.id}/state`, { method: "POST", body: { is_saved: saved } });
    } catch {
      mutate({ ...data, is_saved: !saved }, { revalidate: false });
    }
  };

  const tagsStyles = useMemo(() => htmlStyles(colors, isDark), [colors, isDark]);
  const summaryStyles = useMemo(() => summaryMdStyles(colors), [colors]);

  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <Stack.Screen
        options={{
          title: data?.feed_title ?? "",
          headerBackButtonDisplayMode: "minimal",
          headerRight: () =>
            data ? (
              <View style={styles.headerButtons}>
                {ai?.configured && (
                  <Pressable onPress={() => router.push(`/article/${data.id}/qa`)} hitSlop={8}>
                    <Ionicons name="chatbubble-ellipses-outline" size={22} color={colors.tint} />
                  </Pressable>
                )}
                <Pressable onPress={toggleSaved} hitSlop={8}>
                  <Ionicons
                    name={data.is_saved ? "bookmark" : "bookmark-outline"}
                    size={22}
                    color={colors.tint}
                  />
                </Pressable>
                <Pressable
                  onPress={() => Share.share({ message: data.url }).catch(() => {})}
                  hitSlop={8}
                >
                  <Ionicons name="share-outline" size={22} color={colors.tint} />
                </Pressable>
              </View>
            ) : null,
        }}
      />

      {error ? (
        <View style={styles.center}>
          <Text style={{ color: colors.danger, textAlign: "center" }}>
            Couldn't load the article: {error instanceof Error ? error.message : "unknown error"}
          </Text>
        </View>
      ) : !data ? (
        <ActivityIndicator style={styles.center} color={colors.tint} />
      ) : (
        <ScrollView contentContainerStyle={styles.content}>
          <Text style={[styles.title, { color: colors.text }]}>{data.title}</Text>
          <Text style={[styles.byline, { color: colors.muted }]}>
            {[data.feed_title, data.author, timeAgo(data.published_at)]
              .filter(Boolean)
              .join(" · ")}
          </Text>

          {/* Illustration hero. A finished image cross-fades in; while one is
              still rendering we show a live "generating" placeholder; nothing
              renders for articles with no image and none on the way. */}
          {data.image_url ? (
            <Image
              source={{ uri: imageSrc(data.image_url) }}
              style={[styles.hero, { backgroundColor: colors.card, borderColor: colors.border }]}
              contentFit="cover"
              transition={400}
              accessibilityIgnoresInvertColors
            />
          ) : data.image_pending ? (
            <GeneratingImage colors={colors} style={styles.hero} />
          ) : null}

          {data.summary !== "" && (
            <View style={[styles.summary, { borderColor: colors.border }]}>
              <Text style={[styles.summaryLabel, { color: colors.muted }]}>AI summary</Text>
              <Markdown
                style={summaryStyles}
                onLinkPress={(url) => {
                  openLink(url);
                  return false;
                }}
              >
                {asMarkdown(data.summary)}
              </Markdown>
            </View>
          )}

          <RenderHTML
            contentWidth={width - 32}
            source={{ html: data.content_html || `<p>${data.excerpt}</p>` }}
            baseStyle={{ color: colors.text, fontSize: 17, lineHeight: 26 }}
            tagsStyles={tagsStyles}
            renderersProps={{ a: { onPress: (_event, href) => openLink(href) } }}
            defaultTextProps={{ selectable: true }}
            enableExperimentalMarginCollapsing
          />

          <View style={[styles.links, { borderTopColor: colors.border }]}>
            <Pressable style={styles.linkRow} onPress={() => openLink(data.url)}>
              <Ionicons name="open-outline" size={18} color={colors.tint} />
              <Text style={[styles.linkText, { color: colors.tint }]}>Open original</Text>
            </Pressable>
            {discussionRef && (
              <Pressable
                style={styles.linkRow}
                onPress={() => router.push(`/article/${data.id}/discussion`)}
              >
                <Ionicons name="chatbox-outline" size={18} color={colors.tint} />
                <Text style={[styles.linkText, { color: colors.tint }]}>Hacker News discussion</Text>
                {hnStory && (
                  <Text style={[styles.linkMeta, { color: colors.muted }]}>
                    {hnStory.score ?? 0} points, {hnStory.descendants ?? 0} comments
                  </Text>
                )}
              </Pressable>
            )}
            {data.comments_url && !discussionRef && (
              <Pressable style={styles.linkRow} onPress={() => openLink(data.comments_url!)}>
                <Ionicons name="chatbox-outline" size={18} color={colors.tint} />
                <Text style={[styles.linkText, { color: colors.tint }]}>Comments</Text>
              </Pressable>
            )}
          </View>

          <RelatedCoverage articleId={data.id} />
        </ScrollView>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 32 },
  headerButtons: { flexDirection: "row", gap: 18, alignItems: "center" },
  content: { padding: 16, paddingBottom: 48 },
  title: { fontSize: 24, fontWeight: "700", lineHeight: 30, marginBottom: 8 },
  byline: { fontSize: 14, marginBottom: 16 },
  hero: {
    width: "100%",
    aspectRatio: 2,
    borderRadius: 12,
    borderWidth: StyleSheet.hairlineWidth,
    marginBottom: 16,
    overflow: "hidden",
  },
  summary: { borderWidth: 1, borderRadius: 12, padding: 14, marginBottom: 16, gap: 8 },
  summaryLabel: {
    fontSize: 12,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.6,
  },
  links: { marginTop: 24, paddingTop: 16, borderTopWidth: StyleSheet.hairlineWidth, gap: 14 },
  linkRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  linkText: { fontSize: 16, fontWeight: "600" },
  linkMeta: { fontSize: 12, marginLeft: "auto" },
});
