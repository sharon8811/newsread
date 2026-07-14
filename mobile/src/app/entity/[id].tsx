import { Ionicons } from "@expo/vector-icons";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import * as WebBrowser from "expo-web-browser";
import useSWR from "swr";

import { entityKey, entityKindLabel } from "@/lib/entities";
import { timeAgo } from "@/lib/format";
import { usePalette } from "@/lib/theme";
import type { EntityPage } from "@/lib/types";

/** One person / org / product / repo…, plus every visible article from the
 * user's feeds that mentions it. Reached from the article page's entity
 * chips; the native stack header provides back navigation. */
export default function EntityScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { colors } = usePalette();
  const { data: entity, error } = useSWR<EntityPage>(entityKey(id));

  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <Stack.Screen
        options={{ title: entity?.name ?? "", headerBackButtonDisplayMode: "minimal" }}
      />
      {error && (
        <View style={styles.center}>
          <Text style={{ color: colors.muted }}>This entity could not be loaded.</Text>
        </View>
      )}
      {!entity && !error && (
        <View style={styles.center}>
          <ActivityIndicator color={colors.tint} />
        </View>
      )}
      {entity && (
        <ScrollView contentContainerStyle={styles.content}>
          <Text style={[styles.label, { color: colors.muted }]}>
            {entityKindLabel(entity.kind).toUpperCase()}
          </Text>
          <View style={styles.titleRow}>
            <Text style={[styles.title, { color: colors.text }]}>{entity.name}</Text>
            {!!entity.url && (
              <Pressable
                onPress={() => WebBrowser.openBrowserAsync(entity.url).catch(() => {})}
                hitSlop={8}
              >
                <Ionicons name="open-outline" size={18} color={colors.tint} />
              </Pressable>
            )}
          </View>

          <Text style={[styles.label, { color: colors.muted, marginTop: 24 }]}>
            FROM YOUR FEEDS
          </Text>
          {entity.articles.length === 0 && (
            <Text style={{ color: colors.muted, marginTop: 8, fontSize: 14 }}>
              No articles from your feeds mention this yet.
            </Text>
          )}
          {entity.articles.map((item) => (
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
              </View>
              <Text style={[styles.rowMeta, { color: colors.muted }]} numberOfLines={1}>
                {[item.feed_title, timeAgo(item.published_at)].filter(Boolean).join(" · ")}
              </Text>
            </Pressable>
          ))}
        </ScrollView>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 32 },
  content: { padding: 16, paddingBottom: 48 },
  label: { fontSize: 11, fontWeight: "600", letterSpacing: 1 },
  titleRow: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 4 },
  title: { fontSize: 24, fontWeight: "700", lineHeight: 30, flexShrink: 1 },
  row: {
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 8,
    padding: 12,
    marginTop: 8,
  },
  rowHeader: { flexDirection: "row", alignItems: "center", gap: 8 },
  dot: { width: 7, height: 7, borderRadius: 4 },
  rowTitle: { fontSize: 15, fontWeight: "600", flex: 1 },
  rowMeta: { fontSize: 12, marginTop: 4 },
});
