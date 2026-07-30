import { Ionicons } from "@expo/vector-icons";
import { useState } from "react";
import { ActivityIndicator, FlatList, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import Markdown, { type MarkdownProps } from "react-native-markdown-display";
import useSWR from "swr";

import { api } from "@/lib/api";
import { textDirection } from "@/lib/rtl";
import { useAuth } from "@/lib/auth";
import type { Palette } from "@/lib/theme";
import type { SummaryTranslation, TranslationLanguage, User } from "@/lib/types";

/** The AI summary plus its translate control. The original never leaves
 * component state, so switching back is instant and a failed translation
 * costs the reader nothing.
 *
 * The phone app has no settings screen, so the language picker is also where
 * the default is set — it writes the same `translation_language` the web app
 * reads, and a default chosen here applies there too. */
export default function TranslatableSummary({
  articleId,
  summary,
  articleRtl,
  colors,
  markdownStyles,
  translatable,
  onLinkPress,
}: {
  articleId: number;
  summary: string;
  /** The article's own direction, from the server's language detection. */
  articleRtl: boolean;
  colors: Palette;
  markdownStyles: MarkdownProps["style"];
  translatable: boolean;
  onLinkPress: (url: string) => boolean;
}) {
  const { user, updateUser } = useAuth();
  const { data: languages } = useSWR<TranslationLanguage[]>(
    translatable ? "/translation/languages" : null,
  );
  const [translation, setTranslation] = useState<SummaryTranslation | null>(null);
  const [showingOriginal, setShowingOriginal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);

  const saved = user?.translation_language ?? null;
  const language = languages?.find((item) => item.code === (translation?.language ?? saved));
  const showingTranslation = translation !== null && !showingOriginal;
  const body = showingTranslation ? translation.text : summary;

  async function translate(code: string, makeDefault: boolean) {
    setPicking(false);
    setBusy(true);
    setError(null);
    try {
      const result = await api<SummaryTranslation>(`/articles/${articleId}/translate`, {
        method: "POST",
        body: { language: code },
      });
      setTranslation(result);
      setShowingOriginal(false);
      if (makeDefault && code !== saved && user) {
        // A failed save must not read as a failed translation: the language is
        // applied locally either way.
        try {
          updateUser(
            await api<User>("/users/me", {
              method: "PATCH",
              body: { translation_language: code },
            }),
          );
        } catch {
          updateUser({ ...user, translation_language: code });
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Translation failed");
    } finally {
      setBusy(false);
    }
  }

  function onTranslatePress() {
    if (translation) setShowingOriginal((value) => !value);
    else if (saved) translate(saved, false);
    else setPicking(true);
  }

  const label = busy
    ? "Translating…"
    : translation
      ? showingOriginal
        ? `Show ${language?.name ?? "translation"}`
        : "Show original"
      : saved && language
        ? `Translate to ${language.name}`
        : "Translate summary";

  return (
    <View style={[styles.summary, { borderColor: colors.border }]}>
      <View style={styles.header}>
        <Text style={[styles.label, { color: colors.muted }]}>AI summary</Text>
        {translatable && (
          <Pressable
            style={styles.action}
            onPress={onTranslatePress}
            disabled={busy}
            hitSlop={8}
            accessibilityRole="button"
          >
            {busy ? (
              <ActivityIndicator size="small" color={colors.tint} />
            ) : (
              <Ionicons name="language-outline" size={16} color={colors.tint} />
            )}
            <Text style={[styles.actionText, { color: colors.tint }]}>{label}</Text>
          </Pressable>
        )}
      </View>

      {translatable && saved && !busy && (
        <Pressable onPress={() => setPicking(true)} hitSlop={8}>
          <Text style={[styles.secondary, { color: colors.muted }]}>Another language</Text>
        </Pressable>
      )}

      {error && <Text style={{ color: colors.danger, fontSize: 13 }}>{error}</Text>}

      {showingTranslation && !translation.translated && (
        <Text style={[styles.secondary, { color: colors.muted }]}>
          This summary is already in {language?.name ?? "that language"}.
        </Text>
      )}

      <Markdown
        style={directionalStyles(markdownStyles, showingTranslation ? translation.rtl : articleRtl)}
        onLinkPress={onLinkPress}
      >
        {body}
      </Markdown>

      {/* Mounted only while open, so "make this my default" starts unticked
          every time — a sticky tick would silently move the saved language on
          a later one-off pick. */}
      {picking && (
        <LanguagePicker
          colors={colors}
          languages={languages ?? []}
          current={saved}
          allowOneOff={Boolean(saved)}
          onPick={translate}
          onClose={() => setPicking(false)}
        />
      )}
    </View>
  );
}

/** Markdown styles laid out in the text's own direction. `writingDirection`
 * only affects text runs — the bullet column and table rows are flex rows that
 * React Native still lays out left-to-right, so they are mirrored explicitly. */
function directionalStyles(base: MarkdownProps["style"], rtl: boolean): MarkdownProps["style"] {
  const direction = textDirection(rtl);
  return {
    ...base,
    body: { ...base?.body, ...direction },
    list_item: { ...base?.list_item, flexDirection: rtl ? "row-reverse" : "row" },
    tr: { ...base?.tr, flexDirection: rtl ? "row-reverse" : "row" },
  };
}

function LanguagePicker({
  colors,
  languages,
  current,
  allowOneOff,
  onPick,
  onClose,
}: {
  colors: Palette;
  languages: TranslationLanguage[];
  current: string | null;
  allowOneOff: boolean;
  onPick: (code: string, makeDefault: boolean) => void;
  onClose: () => void;
}) {
  // A pick made from "Another language" is a one-off unless asked otherwise;
  // on first use the checkbox is hidden and the pick becomes the default.
  const [makeDefault, setMakeDefault] = useState(false);

  return (
    <Modal visible animationType="slide" transparent onRequestClose={onClose}>
      <Pressable style={styles.scrim} onPress={onClose} />
      <View style={[styles.sheet, { backgroundColor: colors.background, borderColor: colors.border }]}>
        <View style={styles.sheetHeader}>
          <Text style={[styles.sheetTitle, { color: colors.text }]}>Translate summary to</Text>
          <Pressable onPress={onClose} hitSlop={8} accessibilityLabel="Close">
            <Ionicons name="close" size={22} color={colors.muted} />
          </Pressable>
        </View>

        <FlatList
          data={languages}
          keyExtractor={(item) => item.code}
          ListEmptyComponent={
            <Text style={[styles.empty, { color: colors.muted }]}>
              Couldn't load the language list. Check your connection and try again.
            </Text>
          }
          renderItem={({ item }) => (
            <Pressable
              style={styles.row}
              onPress={() => onPick(item.code, allowOneOff ? makeDefault : true)}
            >
              <Text style={{ color: colors.text, fontSize: 16 }}>{item.name}</Text>
              <Text style={{ color: colors.muted, fontSize: 14 }}>{item.native_name}</Text>
              {item.code === current && (
                <Ionicons
                  name="checkmark"
                  size={18}
                  color={colors.tint}
                  style={{ marginLeft: "auto" }}
                />
              )}
            </Pressable>
          )}
        />

        {allowOneOff && (
          <Pressable
            style={[styles.defaultRow, { borderTopColor: colors.border }]}
            onPress={() => setMakeDefault((value) => !value)}
            accessibilityRole="checkbox"
            accessibilityState={{ checked: makeDefault }}
          >
            <Ionicons
              name={makeDefault ? "checkbox" : "square-outline"}
              size={20}
              color={makeDefault ? colors.tint : colors.muted}
            />
            <Text style={{ color: colors.muted, fontSize: 14 }}>Make this my default language</Text>
          </Pressable>
        )}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  summary: { borderWidth: 1, borderRadius: 12, padding: 14, marginBottom: 16, gap: 8 },
  header: { flexDirection: "row", alignItems: "center", gap: 12 },
  label: { fontSize: 12, fontWeight: "600", textTransform: "uppercase", letterSpacing: 0.6 },
  action: { flexDirection: "row", alignItems: "center", gap: 5, marginLeft: "auto" },
  actionText: { fontSize: 13, fontWeight: "600" },
  secondary: { fontSize: 13 },
  scrim: { flex: 1, backgroundColor: "rgba(0,0,0,0.4)" },
  sheet: {
    maxHeight: "70%",
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    paddingBottom: 24,
  },
  sheetHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    padding: 16,
  },
  sheetTitle: { fontSize: 17, fontWeight: "700" },
  row: { flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 16, paddingVertical: 12 },
  empty: { fontSize: 14, paddingHorizontal: 16, paddingVertical: 12 },
  defaultRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 16,
    paddingTop: 14,
  },
});
