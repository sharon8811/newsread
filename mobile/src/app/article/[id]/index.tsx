import { Ionicons } from "@expo/vector-icons";
import { Image } from "expo-image";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import * as WebBrowser from "expo-web-browser";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AccessibilityInfo,
  ActivityIndicator,
  Animated,
  Pressable,
  ScrollView,
  Share,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from "react-native";
import RenderHTML, { type MixedStyleDeclaration } from "react-native-render-html";
import useSWR from "swr";

import { api, imageSrc } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { useReadingTimer } from "@/lib/useReadingTimer";
import { usePalette, type Palette } from "@/lib/theme";
import type { AiStatus, ArticleDetail } from "@/lib/types";

const openLink = (url: string) => WebBrowser.openBrowserAsync(url).catch(() => {});

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

// Honour the OS "reduce motion" setting so the shimmer/dots hold still for
// users who opt out of animation (the RN equivalent of the web's
// prefers-reduced-motion gate). Nothing else in the app tracks this yet, so
// it lives here with the only motion that needs it.
function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    let active = true;
    AccessibilityInfo.isReduceMotionEnabled().then((value) => {
      if (active) setReduced(value);
    });
    const sub = AccessibilityInfo.addEventListener("reduceMotionChanged", setReduced);
    return () => {
      active = false;
      sub.remove();
    };
  }, []);
  return reduced;
}

// Three dots that blink in sequence — the "thinking" motion mirrored from the
// web QA panel / generating label. Opacity is native-driver friendly.
function TypingDots({ color }: { color: string }) {
  const reduced = useReducedMotion();
  const a = useRef(new Animated.Value(0.3)).current;
  const b = useRef(new Animated.Value(0.3)).current;
  const c = useRef(new Animated.Value(0.3)).current;
  const dots = useMemo(() => [a, b, c], [a, b, c]);

  useEffect(() => {
    if (reduced) return;
    const loops = dots.map((value, i) =>
      Animated.loop(
        Animated.sequence([
          Animated.delay(i * 180),
          Animated.timing(value, { toValue: 1, duration: 300, useNativeDriver: true }),
          Animated.timing(value, { toValue: 0.3, duration: 300, useNativeDriver: true }),
          Animated.delay((dots.length - 1 - i) * 180),
        ]),
      ),
    );
    loops.forEach((loop) => loop.start());
    return () => loops.forEach((loop) => loop.stop());
  }, [dots, reduced]);

  return (
    <View style={styles.dots}>
      {dots.map((value, i) => (
        <Animated.View
          key={i}
          style={[styles.dot, { backgroundColor: color, opacity: reduced ? 0.6 : value }]}
        />
      ))}
    </View>
  );
}

// Placeholder shown while an illustration renders: a gently pulsing skeleton
// surface (the RN take on the web `.shimmer`) with the app's ✦ AI marker and a
// live "generating" label. Announced politely to assistive tech.
function GeneratingHero({ colors }: { colors: Palette }) {
  const reduced = useReducedMotion();
  const pulse = useRef(new Animated.Value(0.4)).current;

  useEffect(() => {
    if (reduced) return;
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 850, useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0.4, duration: 850, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [pulse, reduced]);

  return (
    <View
      style={[styles.hero, styles.heroPending, { backgroundColor: colors.card, borderColor: colors.border }]}
      accessible
      accessibilityRole="progressbar"
      accessibilityLabel="Generating illustration"
      accessibilityLiveRegion="polite"
    >
      <Animated.View
        style={[
          StyleSheet.absoluteFill,
          { backgroundColor: colors.border, opacity: reduced ? 0.5 : pulse },
        ]}
      />
      <View style={styles.heroLabel}>
        <Text style={[styles.heroSpark, { color: colors.tint }]}>✦</Text>
        <Text style={[styles.heroLabelText, { color: colors.muted }]}>generating illustration</Text>
        <TypingDots color={colors.muted} />
      </View>
    </View>
  );
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
            <GeneratingHero colors={colors} />
          ) : null}

          {data.summary !== "" && (
            <View style={[styles.summary, { borderColor: colors.border }]}>
              <Text style={[styles.summaryLabel, { color: colors.muted }]}>AI summary</Text>
              {data.summary.split(/\n+/).map((paragraph, index) => (
                <Text key={index} style={[styles.summaryText, { color: colors.text }]}>
                  {paragraph}
                </Text>
              ))}
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
            {data.comments_url && (
              <Pressable style={styles.linkRow} onPress={() => openLink(data.comments_url!)}>
                <Ionicons name="chatbox-outline" size={18} color={colors.tint} />
                <Text style={[styles.linkText, { color: colors.tint }]}>Comments</Text>
              </Pressable>
            )}
          </View>
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
  heroPending: { alignItems: "center", justifyContent: "center" },
  heroLabel: { flexDirection: "row", alignItems: "center", gap: 6 },
  heroSpark: { fontSize: 13, fontWeight: "600" },
  heroLabelText: { fontSize: 13 },
  dots: { flexDirection: "row", alignItems: "center", gap: 4, marginLeft: 2 },
  dot: { width: 4, height: 4, borderRadius: 2 },
  summary: { borderWidth: 1, borderRadius: 12, padding: 14, marginBottom: 16, gap: 8 },
  summaryLabel: {
    fontSize: 12,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.6,
  },
  summaryText: { fontSize: 15, lineHeight: 22 },
  links: { marginTop: 24, paddingTop: 16, borderTopWidth: StyleSheet.hairlineWidth, gap: 14 },
  linkRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  linkText: { fontSize: 16, fontWeight: "600" },
});
