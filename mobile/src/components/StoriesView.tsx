// Full-screen story cards, one article at a time — the mobile take on the web
// app's StoriesView. Tap right to advance (marks the current card read, like
// the web), tap left to go back, "Read article" opens the detail screen.

import { Ionicons } from "@expo/vector-icons";
import { ImageBackground } from "expo-image";
import { useRef, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { imageSrc } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import type { Article } from "@/lib/types";

const SCRIM = "rgba(8, 10, 14, 0.55)";
const CARD_BG = "#14171c";

type Props = {
  articles: Article[];
  onOpen: (article: Article) => void;
  onMarkRead: (article: Article) => void;
  onExit: () => void;
};

export default function StoriesView({ articles, onOpen, onMarkRead, onExit }: Props) {
  // The nav header is hidden here, so the safe areas are ours to respect.
  const insets = useSafeAreaInsets();
  // Snapshot the queue on mount so read-state changes don't reshuffle cards
  // mid-session (same behaviour as the web StoriesView).
  const queue = useRef(articles).current;
  const [index, setIndex] = useState(0);
  const article = queue[index];

  const advance = () => {
    if (article) onMarkRead(article);
    setIndex((current) => current + 1);
  };
  const goBack = () => setIndex((current) => Math.max(0, current - 1));

  if (!article) {
    return (
      <View style={[styles.screen, { backgroundColor: CARD_BG }]}>
        <View style={styles.done}>
          <Ionicons name="checkmark-done-outline" size={44} color="#9aa0a6" />
          <Text style={styles.doneTitle}>You're all caught up</Text>
          <Pressable style={styles.doneButton} onPress={onExit}>
            <Text style={styles.doneButtonLabel}>Back to the list</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  const blurb = article.summary_medium || article.summary_short || article.excerpt;

  return (
    <View style={[styles.screen, { backgroundColor: CARD_BG }]}>
      <ImageBackground
        source={article.image_url ? { uri: imageSrc(article.image_url) } : undefined}
        style={styles.card}
        contentFit="cover"
        transition={200}
      >
        <View style={styles.scrim} />

        <View style={[styles.progressRow, { top: insets.top + 8 }]}>
          {queue.length <= 30 ? (
            queue.map((item, itemIndex) => (
              <View
                key={item.id}
                style={[styles.progressSegment, itemIndex <= index && styles.progressDone]}
              />
            ))
          ) : (
            <Text style={styles.progressCounter}>
              {index + 1} / {queue.length} · {queue.length - index - 1} left
            </Text>
          )}
        </View>

        <Pressable
          style={[styles.closeButton, { top: insets.top + 2 }]}
          onPress={onExit}
          hitSlop={14}
        >
          <Ionicons name="close" size={26} color="#ffffff" />
        </Pressable>

        {/* Tap zones: left third back, the rest advances. */}
        <View style={styles.tapZones}>
          <Pressable style={styles.tapBack} onPress={goBack} />
          <Pressable style={styles.tapForward} onPress={advance} />
        </View>

        <View
          style={[styles.body, { paddingBottom: 24 + insets.bottom }]}
          pointerEvents="box-none"
        >
          <Text style={styles.meta}>
            {article.feed_title}
            {article.published_at ? ` · ${timeAgo(article.published_at)}` : ""}
          </Text>
          <Text style={styles.title}>{article.title}</Text>
          {blurb !== "" && (
            <Text style={styles.blurb} numberOfLines={6}>
              {blurb}
            </Text>
          )}
          <Pressable style={styles.openButton} onPress={() => onOpen(article)}>
            <Text style={styles.openLabel}>Read article</Text>
            <Ionicons name="arrow-forward" size={16} color="#0b0d10" />
          </Pressable>
        </View>
      </ImageBackground>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  card: { flex: 1, justifyContent: "flex-end" },
  scrim: { ...StyleSheet.absoluteFillObject, backgroundColor: SCRIM },
  progressRow: {
    position: "absolute",
    left: 12,
    right: 56,
    flexDirection: "row",
    gap: 4,
    alignItems: "center",
  },
  progressSegment: {
    flex: 1,
    height: 3,
    borderRadius: 2,
    backgroundColor: "rgba(255,255,255,0.25)",
  },
  progressDone: { backgroundColor: "rgba(255,255,255,0.9)" },
  progressCounter: { color: "rgba(255,255,255,0.9)", fontSize: 13, fontWeight: "600" },
  closeButton: { position: "absolute", right: 12, padding: 6, zIndex: 3 },
  tapZones: { ...StyleSheet.absoluteFillObject, flexDirection: "row", zIndex: 1 },
  tapBack: { flex: 1 },
  tapForward: { flex: 2 },
  body: { padding: 20, paddingBottom: 36, gap: 10, zIndex: 2 },
  meta: { color: "rgba(255,255,255,0.75)", fontSize: 14, fontWeight: "600" },
  title: { color: "#ffffff", fontSize: 28, fontWeight: "800", lineHeight: 34 },
  blurb: { color: "rgba(255,255,255,0.92)", fontSize: 15, lineHeight: 22 },
  openButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    alignSelf: "flex-start",
    backgroundColor: "#ffffff",
    borderRadius: 20,
    paddingHorizontal: 16,
    paddingVertical: 9,
    marginTop: 6,
  },
  openLabel: { color: "#0b0d10", fontSize: 15, fontWeight: "700" },
  done: { flex: 1, alignItems: "center", justifyContent: "center", gap: 14 },
  doneTitle: { color: "#e8eaed", fontSize: 20, fontWeight: "700" },
  doneButton: {
    backgroundColor: "#ffffff",
    borderRadius: 20,
    paddingHorizontal: 18,
    paddingVertical: 10,
  },
  doneButtonLabel: { color: "#0b0d10", fontSize: 15, fontWeight: "700" },
});
