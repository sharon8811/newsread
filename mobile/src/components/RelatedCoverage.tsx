import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { useMemo, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import Markdown from "react-native-markdown-display";
import useSWR from "swr";

import { relatedKey, synthesizeCoverage, timelineRows } from "@/lib/related";
import { timeAgo } from "@/lib/format";
import { usePalette, type Palette } from "@/lib/theme";
import type { AiStatus, CoverageSynthesis, RelatedArticle } from "@/lib/types";

/** Markdown styling for the synthesis card (matches the AI-summary block). */
function mdStyles(colors: Palette) {
  return StyleSheet.create({
    body: { color: colors.text, fontSize: 15, lineHeight: 22 },
    paragraph: { marginTop: 0, marginBottom: 8 },
    bullet_list: { marginBottom: 8 },
    list_item: { marginBottom: 4 },
    link: { color: colors.tint },
  });
}

/** Related coverage from the user's subscribed feeds, plus the lazy
 * "synthesize coverage" action (one LLM call, only on tap). Hides entirely
 * when nothing is related — same convention as the HN discussion link. */
export default function RelatedCoverage({ articleId }: { articleId: number }) {
  const router = useRouter();
  const { colors } = usePalette();
  const { data: related } = useSWR<RelatedArticle[]>(relatedKey(articleId));
  const { data: ai } = useSWR<AiStatus>("/ai/status");
  const [synthesis, setSynthesis] = useState<CoverageSynthesis | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const markdownStyle = useMemo(() => mdStyles(colors), [colors]);

  if (!related || related.length === 0) return null;

  const synthesize = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      setSynthesis(await synthesizeCoverage(articleId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "The synthesis failed");
    } finally {
      setBusy(false);
    }
  };

  const timeline = synthesis ? timelineRows(synthesis) : null;

  return (
    <View style={[styles.section, { borderTopColor: colors.border }]}>
      <Text style={[styles.label, { color: colors.muted }]}>Related coverage</Text>

      {related.map((item) => (
        <Pressable
          key={item.id}
          style={({ pressed }) => [
            styles.row,
            { borderColor: colors.border, backgroundColor: colors.card },
            pressed && { opacity: 0.7 },
          ]}
          onPress={() => router.push(`/article/${item.id}`)}
        >
          <View style={styles.rowHeader}>
            {!item.is_read && <View style={[styles.dot, { backgroundColor: colors.tint }]} />}
            <Text style={[styles.rowTitle, { color: colors.text }]} numberOfLines={2}>
              {item.title}
            </Text>
            {item.tier === "same_story" && (
              <Text style={[styles.tag, { borderColor: colors.tint, color: colors.tint }]}>
                SAME STORY
              </Text>
            )}
          </View>
          <Text style={[styles.rowMeta, { color: colors.muted }]} numberOfLines={1}>
            {[item.feed_title, timeAgo(item.published_at)].filter(Boolean).join(" · ")}
          </Text>
        </Pressable>
      ))}

      {ai?.configured && !synthesis && !busy && (
        <Pressable style={styles.synthRow} onPress={synthesize}>
          <Ionicons name="sparkles-outline" size={16} color={colors.tint} />
          <Text style={[styles.synthText, { color: colors.tint }]}>Synthesize coverage</Text>
        </Pressable>
      )}

      {busy && (
        <View style={styles.busyRow}>
          <ActivityIndicator color={colors.tint} />
          <Text style={{ color: colors.muted, fontSize: 13 }}>Reading the coverage…</Text>
        </View>
      )}

      {error && !busy && (
        <View style={styles.busyRow}>
          <Text style={{ color: colors.danger, fontSize: 13, flex: 1 }}>{error}</Text>
          <Pressable onPress={synthesize} hitSlop={8}>
            <Text style={{ color: colors.tint, fontSize: 13, fontWeight: "600" }}>Try again</Text>
          </Pressable>
        </View>
      )}

      {synthesis && !busy && (
        <View style={[styles.card, { borderColor: colors.border }]}>
          <Text style={[styles.label, { color: colors.muted }]}>Coverage synthesis</Text>
          <Markdown style={markdownStyle}>{synthesis.overview}</Markdown>

          {timeline && (
            <>
              <Text style={[styles.subLabel, { color: colors.muted }]}>Timeline</Text>
              <View style={[styles.timeline, { borderLeftColor: colors.border }]}>
                {timeline.map((item, index) => (
                  <View key={index} style={styles.timelineRow}>
                    <Text style={[styles.timelineWhen, { color: colors.tint }]}>{item.when}</Text>
                    <Text style={[styles.timelineWhat, { color: colors.text }]}>{item.what}</Text>
                  </View>
                ))}
              </View>
            </>
          )}
          {!timeline && synthesis.timeline_raw && (
            <>
              <Text style={[styles.subLabel, { color: colors.muted }]}>Timeline</Text>
              <Markdown style={markdownStyle}>{synthesis.timeline_raw}</Markdown>
            </>
          )}

          {synthesis.perspectives && (
            <>
              <Text style={[styles.subLabel, { color: colors.muted }]}>Perspectives</Text>
              <Markdown style={markdownStyle}>{synthesis.perspectives}</Markdown>
            </>
          )}

          <Text style={[styles.sources, { color: colors.muted }]}>
            {synthesis.sources.map((source) => `[${source.n}] ${source.title}`).join("  ·  ")}
          </Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  section: {
    marginTop: 24,
    paddingTop: 16,
    borderTopWidth: StyleSheet.hairlineWidth,
    gap: 10,
  },
  label: {
    fontSize: 12,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.6,
  },
  row: { borderWidth: 1, borderRadius: 10, padding: 12, gap: 4 },
  rowHeader: { flexDirection: "row", alignItems: "center", gap: 8 },
  dot: { width: 7, height: 7, borderRadius: 4 },
  rowTitle: { flex: 1, fontSize: 15, fontWeight: "600", lineHeight: 20 },
  tag: {
    fontSize: 9,
    fontWeight: "700",
    letterSpacing: 0.4,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 7,
    paddingVertical: 2,
    overflow: "hidden",
  },
  rowMeta: { fontSize: 12 },
  synthRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 4 },
  synthText: { fontSize: 15, fontWeight: "600" },
  busyRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 6 },
  card: { borderWidth: 1, borderRadius: 12, padding: 14, gap: 8 },
  subLabel: {
    fontSize: 11,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.6,
    marginTop: 4,
  },
  timeline: { borderLeftWidth: 2, paddingLeft: 12, gap: 8 },
  timelineRow: { gap: 2 },
  timelineWhen: { fontSize: 12, fontWeight: "600" },
  timelineWhat: { fontSize: 14, lineHeight: 20 },
  sources: { fontSize: 11, marginTop: 4 },
});
