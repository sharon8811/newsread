import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";

import { api } from "@/lib/api";
import type { Palette } from "@/lib/theme";
import type { SummaryOut } from "@/lib/types";

/** The AI-summary slot while there is nothing stored yet: asks the server to
 * generate once, shows a generating state meanwhile, and a clear failure
 * state — instead of silently rendering nothing — when the article's page
 * turns out to be unsummarizable (404, paywall, bot check). */
export default function PendingSummary({
  articleId,
  skippedReason,
  colors,
  onSettled,
}: {
  articleId: number;
  /** The stored skip reason, if the server already gave up on this article. */
  skippedReason: string | null;
  colors: Palette;
  /** Revalidate the article so a finished summary replaces this slot. */
  onSettled: () => void;
}) {
  const [state, setState] = useState<"generating" | "unusable" | "failed">(
    skippedReason === "unusable_page" ? "unusable" : "generating",
  );
  const requested = useRef(false);
  // "Try again" on an unusable page must force, or the server would just
  // replay the stored skip.
  const force = useRef(false);

  useEffect(() => {
    if (state !== "generating" || requested.current) return;
    requested.current = true;
    api<SummaryOut>(`/articles/${articleId}/summarize${force.current ? "?force=true" : ""}`, {
      method: "POST",
    })
      .then((result) => {
        if (result.skipped_reason === "unusable_page") setState("unusable");
        onSettled();
      })
      .catch(() => setState("failed"));
  }, [state, articleId, onSettled]);

  const retry = () => {
    force.current = true;
    requested.current = false;
    setState("generating");
  };

  return (
    <View style={[styles.box, { borderColor: colors.border }]}>
      <Text style={[styles.label, { color: colors.muted }]}>AI summary</Text>
      {state === "generating" ? (
        <View style={styles.row}>
          <ActivityIndicator size="small" color={colors.tint} />
          <Text style={[styles.body, { color: colors.muted }]}>Reading the full article…</Text>
        </View>
      ) : (
        <>
          <Text style={[styles.body, { color: colors.muted }]}>
            {state === "unusable"
              ? "Couldn’t summarize this article — its page appears to be unavailable (a missing page, paywall, or bot check)."
              : "The AI summary failed."}
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
