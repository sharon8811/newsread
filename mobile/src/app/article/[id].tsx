import { Stack, useLocalSearchParams } from "expo-router";
import * as WebBrowser from "expo-web-browser";
import { useEffect, useRef } from "react";
import { ActivityIndicator, Pressable, Share, StyleSheet, Text, View } from "react-native";
import { WebView } from "react-native-webview";
import useSWR from "swr";

import { api } from "@/lib/api";
import { buildArticleHtml } from "@/lib/articleHtml";
import { usePalette } from "@/lib/theme";
import type { ArticleDetail } from "@/lib/types";

export default function ArticleScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { colors, isDark } = usePalette();
  const { data, error, mutate } = useSWR<ArticleDetail>(id ? `/articles/${id}` : null);
  const markedRead = useRef(false);

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

  const share = () => {
    if (data) Share.share({ message: data.url }).catch(() => {});
  };

  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <Stack.Screen
        options={{
          title: data?.feed_title ?? "",
          headerBackButtonDisplayMode: "minimal",
          headerRight: () =>
            data ? (
              <View style={styles.headerButtons}>
                <Pressable onPress={toggleSaved} hitSlop={10}>
                  <Text style={{ color: colors.tint, fontSize: 15 }}>
                    {data.is_saved ? "Saved ✓" : "Save"}
                  </Text>
                </Pressable>
                <Pressable onPress={share} hitSlop={10}>
                  <Text style={{ color: colors.tint, fontSize: 15 }}>Share</Text>
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
        <>
          <WebView
            style={{ backgroundColor: colors.background }}
            source={{ html: buildArticleHtml(data, isDark) }}
            originWhitelist={["*"]}
            // Taps on links open the system browser; only the injected
            // document itself renders inside the WebView.
            onShouldStartLoadWithRequest={(request) => {
              if (request.url.startsWith("http")) {
                WebBrowser.openBrowserAsync(request.url).catch(() => {});
                return false;
              }
              return true;
            }}
          />
          <Pressable
            style={[styles.originalButton, { backgroundColor: colors.card, borderColor: colors.border }]}
            onPress={() => WebBrowser.openBrowserAsync(data.url).catch(() => {})}
          >
            <Text style={{ color: colors.tint, fontSize: 15, fontWeight: "600" }}>
              Open original
            </Text>
          </Pressable>
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 32 },
  headerButtons: { flexDirection: "row", gap: 18 },
  originalButton: {
    alignItems: "center",
    paddingVertical: 12,
    borderTopWidth: StyleSheet.hairlineWidth,
  },
});
