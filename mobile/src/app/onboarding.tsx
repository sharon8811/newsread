import { useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useAuth } from "@/lib/auth";
import { usePalette } from "@/lib/theme";

export default function OnboardingScreen() {
  const { setServer } = useAuth();
  const { colors } = usePalette();
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const connect = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await setServer(input);
      // Success flips auth status; the router swaps to the login screen.
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SafeAreaView style={[styles.safe, { backgroundColor: colors.background }]}>
      <KeyboardAvoidingView
        style={styles.container}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <Text style={[styles.logo, { color: colors.text }]}>NewsRead</Text>
        <Text style={[styles.blurb, { color: colors.muted }]}>
          NewsRead is self-hosted. Enter the address of your server to get started.
        </Text>
        <TextInput
          style={[
            styles.input,
            { borderColor: colors.border, color: colors.text, backgroundColor: colors.card },
          ]}
          placeholder="news.example.com"
          placeholderTextColor={colors.muted}
          value={input}
          onChangeText={setInput}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          returnKeyType="go"
          onSubmitEditing={connect}
          editable={!busy}
        />
        {error && <Text style={[styles.error, { color: colors.danger }]}>{error}</Text>}
        <Pressable
          style={[styles.button, { backgroundColor: colors.tint, opacity: busy ? 0.6 : 1 }]}
          onPress={connect}
          disabled={busy}
        >
          {busy ? (
            <ActivityIndicator color={colors.background} />
          ) : (
            <Text style={[styles.buttonLabel, { color: colors.background }]}>Connect</Text>
          )}
        </Pressable>
        <View style={styles.spacer} />
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1 },
  container: { flex: 1, justifyContent: "center", paddingHorizontal: 28, gap: 14 },
  logo: { fontSize: 34, fontWeight: "800", textAlign: "center", marginBottom: 4 },
  blurb: { fontSize: 15, textAlign: "center", lineHeight: 21, marginBottom: 12 },
  input: { borderWidth: 1, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 12, fontSize: 16 },
  error: { fontSize: 14, textAlign: "center" },
  button: { borderRadius: 12, paddingVertical: 14, alignItems: "center" },
  buttonLabel: { fontSize: 16, fontWeight: "600" },
  spacer: { height: 80 },
});
