import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";

import { api } from "@/lib/api";
import type { Palette } from "@/lib/theme";
import type { SummaryOut, SummarySkippedReason } from "@/lib/types";

/** Skip reasons that mean "we reached the source and could not read it",
 * each said in its own words: a reader told "its page is unavailable" about a
 * caption-less video learns nothing true. Mirrors UNREADABLE_SOURCE in the
 * web's AiSummary. */
const UNREADABLE_SOURCE: Partial<Record<SummarySkippedReason, string>> = {
  unusable_page:
    "Couldn’t summarize this article — its page appears to be unavailable (a missing page, paywall, or bot check).",
  no_transcript:
    "This video has no captions to summarize from, and its feed entry carries no description.",
  unreadable_pdf:
    "Couldn’t read any text out of this PDF — it may be a scan, encrypted, or damaged.",
};

/** The AI-summary slot while there is nothing stored yet: asks the server to
 * generate once, shows a generating state meanwhile, and a clear failure
 * state — instead of silently rendering nothing — when the source turns out
 * to be unsummarizable (404, paywall, bot check, captions off, a scanned
 * document). */
export default function PendingSummary({
  articleId,
  skippedReason,
  colors,
  onSettled,
}: {
  articleId: number;
  /** The stored skip reason, if the server already gave up on this article. */
  skippedReason: SummarySkippedReason | null;
  colors: Palette;
  /** Revalidate the article so a finished summary replaces this slot. */
  onSettled: () => void;
}) {
  const [unreadable, setUnreadable] = useState<string | null>(
    (skippedReason && UNREADABLE_SOURCE[skippedReason]) || null,
  );
  const [failed, setFailed] = useState(false);
  const generating = !unreadable && !failed;
  const requested = useRef(false);
  // "Try again" on an unreadable source must force, or the server would just
  // replay the stored skip.
  const force = useRef(false);

  useEffect(() => {
    if (!generating || requested.current) return;
    requested.current = true;
    api<SummaryOut>(`/articles/${articleId}/summarize${force.current ? "?force=true" : ""}`, {
      method: "POST",
    })
      .then((result) => {
        const reason = result.skipped_reason;
        if (reason && UNREADABLE_SOURCE[reason]) setUnreadable(UNREADABLE_SOURCE[reason]);
        onSettled();
      })
      .catch(() => setFailed(true));
  }, [generating, articleId, onSettled]);

  const retry = () => {
    force.current = true;
    requested.current = false;
    setUnreadable(null);
    setFailed(false);
  };

  return (
    <View style={[styles.box, { borderColor: colors.border }]}>
      <Text style={[styles.label, { color: colors.muted }]}>AI summary</Text>
      {generating ? (
        <View style={styles.row}>
          <ActivityIndicator size="small" color={colors.tint} />
          <Text style={[styles.body, { color: colors.muted }]}>Reading the full article…</Text>
        </View>
      ) : (
        <>
          <Text style={[styles.body, { color: colors.muted }]}>
            {unreadable ?? "The AI summary failed."}
          </Text>
          <Pressable onPress={retry} hitSlop={8} accessibilityRole="button">
            <Text style={[styles.retry, { color: colors.tint }]}>Try again</Text>
          </Pressable>
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  box: { borderWidth: 1, borderRadius: 12, padding: 14, marginBottom: 16, gap: 8 },
  label: { fontSize: 12, fontWeight: "600", textTransform: "uppercase", letterSpacing: 0.6 },
  row: { flexDirection: "row", alignItems: "center", gap: 8 },
  body: { fontSize: 14, lineHeight: 20 },
  retry: { fontSize: 13, fontWeight: "600" },
});
