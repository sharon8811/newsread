import { Ionicons } from "@expo/vector-icons";
import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import useSWR, { useSWRConfig } from "swr";

import { api } from "@/lib/api";
import { usePalette, type Palette } from "@/lib/theme";
import type { CatalogCategory, CatalogEntry } from "@/lib/types";

function catalogKey(q: string, category: string | null): string {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (category) params.set("category", category);
  const qs = params.toString();
  return qs ? `/catalog?${qs}` : "/catalog";
}

function EntryCard({ entry, colors, busy, onSubscribe }: {
  entry: CatalogEntry;
  colors: Palette;
  busy: boolean;
  onSubscribe: () => void;
}) {
  return (
    <View style={[styles.card, { backgroundColor: colors.card, borderColor: colors.border }]}>
      <View style={styles.cardHeader}>
        <Text style={[styles.cardTitle, { color: colors.text }]} numberOfLines={2}>
          {entry.title}
        </Text>
        <Text style={[styles.cardCategory, { color: colors.muted, borderColor: colors.border }]}>
          {entry.category}
        </Text>
      </View>
      <Text style={[styles.cardSource, { color: colors.muted }]} numberOfLines={1}>
        {entry.source_host} · {entry.content_type?.includes("atom") ? "Atom" : entry.content_type?.includes("json") ? "JSON Feed" : "RSS"}
      </Text>
      {entry.description ? (
        <Text style={[styles.cardDescription, { color: colors.muted }]} numberOfLines={3}>
          {entry.description}
        </Text>
      ) : null}
      <View style={styles.cardMeta}>
        {entry.item_count !== null ? <Text style={{ color: colors.muted, fontSize: 11 }}>{entry.item_count} recent {entry.item_count === 1 ? "item" : "items"}</Text> : null}
        {entry.subscriber_count > 0 ? <Text style={{ color: colors.muted, fontSize: 11 }}>{entry.subscriber_count} {entry.subscriber_count === 1 ? "reader" : "readers"}</Text> : null}
        {entry.match_reason ? <Text style={{ color: colors.tint, fontSize: 11 }}>{entry.match_reason}</Text> : null}
      </View>
      {entry.subscribed ? (
        <View style={styles.subscribeRow}>
          <Ionicons name="checkmark" size={16} color={colors.tint} />
          <Text style={{ color: colors.tint, fontSize: 14, fontWeight: "600" }}>Subscribed</Text>
        </View>
      ) : (
        <Pressable
          style={({ pressed }) => [
            styles.subscribeButton,
            { backgroundColor: colors.tint, opacity: pressed || busy ? 0.7 : 1 },
          ]}
          disabled={busy}
          onPress={onSubscribe}
        >
          {busy ? (
            <ActivityIndicator size="small" color={colors.background} />
          ) : (
            <Text style={{ color: colors.background, fontSize: 14, fontWeight: "600" }}>
              Subscribe
            </Text>
          )}
        </Pressable>
      )}
    </View>
  );
}

export default function CatalogScreen() {
  const { colors } = usePalette();
  const { mutate } = useSWRConfig();
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [busyUrl, setBusyUrl] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 450);
    return () => clearTimeout(t);
  }, [search]);

  const key = catalogKey(q, category);
  const { data: entries, error, isLoading } = useSWR<CatalogEntry[]>(key);
  const { data: categories } = useSWR<CatalogCategory[]>("/catalog/categories");

  const subscribe = async (entry: CatalogEntry) => {
    if (busyUrl) return;
    setBusyUrl(entry.url);
    try {
      const feed = await api<{ id: number }>("/feeds", {
        method: "POST",
        body: { url: entry.url },
      });
      // Flip this entry in place; the article list revalidates on its own.
      mutate(
        key,
        (current: CatalogEntry[] | undefined) =>
          current?.map((e) =>
            e.url === entry.url ? { ...e, subscribed: true, feed_id: feed.id } : e,
          ),
        { revalidate: false },
      );
    } catch (err) {
      Alert.alert(
        "Could not subscribe",
        err instanceof Error ? err.message : "Something went wrong.",
      );
    } finally {
      setBusyUrl(null);
    }
  };

  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <View style={styles.searchWrap}>
        <View style={[styles.searchBox, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <Ionicons name="search-outline" size={16} color={colors.muted} />
          <TextInput
            style={[styles.searchInput, { color: colors.text }]}
            placeholder="Search feeds…"
            placeholderTextColor={colors.muted}
            value={search}
            onChangeText={setSearch}
            autoCapitalize="none"
            autoCorrect={false}
          />
        </View>
      </View>

      <View style={{ borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border }}>
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.chips}
        >
          <Pressable
            style={[
              styles.chip,
              { backgroundColor: category === null ? colors.tint : colors.card },
            ]}
            onPress={() => setCategory(null)}
          >
            <Text
              style={{
                color: category === null ? colors.background : colors.text,
                fontSize: 13,
                fontWeight: "600",
              }}
            >
              All
            </Text>
          </Pressable>
          {categories?.map((c) => {
            const active = category === c.name;
            return (
              <Pressable
                key={c.name}
                style={[styles.chip, { backgroundColor: active ? colors.tint : colors.card }]}
                onPress={() => setCategory(active ? null : c.name)}
              >
                <Text
                  style={{
                    color: active ? colors.background : colors.text,
                    fontSize: 13,
                    fontWeight: "600",
                  }}
                >
                  {c.name}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>
      </View>

      {isLoading ? (
        <ActivityIndicator style={styles.center} color={colors.tint} />
      ) : error ? (
        <View style={styles.center}>
          <Text style={[styles.emptyText, { color: colors.danger }]}>
            Couldn&apos;t load the catalog: {error instanceof Error ? error.message : "unknown error"}
          </Text>
        </View>
      ) : (
        <FlatList
          data={entries}
          keyExtractor={(entry) => String(entry.id)}
          renderItem={({ item }) => (
            <EntryCard
              entry={item}
              colors={colors}
              busy={busyUrl === item.url}
              onSubscribe={() => subscribe(item)}
            />
          )}
          ListEmptyComponent={
            <View style={styles.center}>
              <Text style={[styles.emptyText, { color: colors.muted }]}>
                No feeds match{q ? ` “${q}”` : ""}.
              </Text>
            </View>
          }
          contentContainerStyle={
            entries?.length === 0 ? styles.fill : styles.listContent
          }
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  searchWrap: { paddingHorizontal: 16, paddingTop: 12, paddingBottom: 4 },
  searchBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 12,
  },
  searchInput: { flex: 1, fontSize: 15, paddingVertical: 9 },
  chips: {
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  chip: { borderRadius: 16, paddingHorizontal: 13, paddingVertical: 6 },
  card: {
    marginHorizontal: 16,
    marginTop: 12,
    borderRadius: 12,
    borderWidth: StyleSheet.hairlineWidth,
    padding: 14,
    gap: 8,
  },
  cardHeader: { flexDirection: "row", alignItems: "flex-start", gap: 8 },
  cardTitle: { flex: 1, fontSize: 16, fontWeight: "600", lineHeight: 21 },
  cardSource: { fontSize: 11 },
  cardMeta: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  cardCategory: {
    fontSize: 11,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 10,
    paddingHorizontal: 8,
    paddingVertical: 2,
    overflow: "hidden",
  },
  cardDescription: { fontSize: 14, lineHeight: 19 },
  subscribeButton: {
    alignItems: "center",
    borderRadius: 8,
    paddingVertical: 8,
    marginTop: 2,
  },
  subscribeRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 5,
    paddingVertical: 8,
    marginTop: 2,
  },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 32 },
  fill: { flexGrow: 1 },
  listContent: { paddingBottom: 24 },
  emptyText: { fontSize: 15, textAlign: "center", lineHeight: 21 },
});
