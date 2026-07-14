import { useRouter } from "expo-router";
import { Pressable, StyleSheet, Text, View } from "react-native";
import * as WebBrowser from "expo-web-browser";

import { entityDisplayName, entityKindLabel, isNameEntity } from "@/lib/entities";
import { usePalette } from "@/lib/theme";
import type { ArticleEntity } from "@/lib/types";

/** Entity chips under the article body: people/orgs/products navigate to
 * their in-app coverage page; linked resources (repos, papers…) open their
 * external page. Hides entirely when the article has no entities. */
export default function EntityChips({ entities }: { entities: ArticleEntity[] | undefined }) {
  const router = useRouter();
  const { colors } = usePalette();
  if (!entities || entities.length === 0) return null;

  const open = (entity: ArticleEntity) => {
    if (isNameEntity(entity)) {
      router.push(`/entity/${entity.id}`);
    } else if (entity.url) {
      WebBrowser.openBrowserAsync(entity.url).catch(() => {});
    }
  };

  return (
    <View style={styles.wrap}>
      {entities.map((entity) => (
        <Pressable
          key={entity.id}
          style={({ pressed }) => [
            styles.chip,
            { borderColor: colors.border, backgroundColor: colors.card },
            pressed && { opacity: 0.7 },
          ]}
          onPress={() => open(entity)}
        >
          <Text style={[styles.kind, { color: colors.tint }]}>
            {entityKindLabel(entity.kind)}
          </Text>
          <Text style={[styles.name, { color: colors.text }]} numberOfLines={1}>
            {entityDisplayName(entity)}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 16 },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 5,
    maxWidth: "100%",
  },
  kind: { fontSize: 12, fontWeight: "600" },
  name: { fontSize: 13, flexShrink: 1 },
});
