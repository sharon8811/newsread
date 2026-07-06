import { useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useAuth } from "@/lib/auth";
import { usePalette } from "@/lib/theme";

export default function LoginScreen() {
  const { serverUrl, login, register, changeServer } = useAuth();
  const { colors } = usePalette();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [identifier, setIdentifier] = useState("");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") await login(identifier.trim(), password);
      else await register({ email: email.trim(), username: username.trim(), name: name.trim(), password });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  const field = (props: React.ComponentProps<typeof TextInput>) => (
    <TextInput
      style={[
        styles.input,
        { borderColor: colors.border, color: colors.text, backgroundColor: colors.card },
      ]}
      placeholderTextColor={colors.muted}
      autoCapitalize="none"
      autoCorrect={false}
      editable={!busy}
      {...props}
    />
  );

  return (
    <SafeAreaView style={[styles.safe, { backgroundColor: colors.background }]}>
      <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
          <Text style={[styles.logo, { color: colors.text }]}>NewsRead</Text>
          <Text style={[styles.server, { color: colors.muted }]} numberOfLines={1}>
            {serverUrl}
          </Text>

          {mode === "login" ? (
            <>
              {field({
                placeholder: "Email or username",
                value: identifier,
                onChangeText: setIdentifier,
                keyboardType: "email-address",
              })}
              {field({
                placeholder: "Password",
                value: password,
                onChangeText: setPassword,
                secureTextEntry: true,
                returnKeyType: "go",
                onSubmitEditing: submit,
              })}
            </>
          ) : (
            <>
              {field({
                placeholder: "Email",
                value: email,
                onChangeText: setEmail,
                keyboardType: "email-address",
              })}
              {field({ placeholder: "Username", value: username, onChangeText: setUsername })}
              {field({ placeholder: "Display name", value: name, onChangeText: setName, autoCapitalize: "words" })}
              {field({
                placeholder: "Password (8+ characters)",
                value: password,
                onChangeText: setPassword,
                secureTextEntry: true,
                returnKeyType: "go",
                onSubmitEditing: submit,
              })}
            </>
          )}

          {error && <Text style={[styles.error, { color: colors.danger }]}>{error}</Text>}

          <Pressable
            style={[styles.button, { backgroundColor: colors.tint, opacity: busy ? 0.6 : 1 }]}
            onPress={submit}
            disabled={busy}
          >
            {busy ? (
              <ActivityIndicator color={colors.background} />
            ) : (
              <Text style={[styles.buttonLabel, { color: colors.background }]}>
                {mode === "login" ? "Sign in" : "Create account"}
              </Text>
            )}
          </Pressable>

          <Pressable
            onPress={() => {
              setMode(mode === "login" ? "register" : "login");
              setError(null);
            }}
            disabled={busy}
          >
            <Text style={[styles.link, { color: colors.tint }]}>
              {mode === "login" ? "New here? Create an account" : "Have an account? Sign in"}
            </Text>
          </Pressable>

          <Pressable onPress={() => changeServer()} disabled={busy}>
            <Text style={[styles.link, { color: colors.muted }]}>
              Connect to a different server
            </Text>
          </Pressable>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1 },
  flex: { flex: 1 },
  container: { flexGrow: 1, justifyContent: "center", paddingHorizontal: 28, gap: 12 },
  logo: { fontSize: 34, fontWeight: "800", textAlign: "center" },
  server: { fontSize: 13, textAlign: "center", marginBottom: 14 },
  input: { borderWidth: 1, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 12, fontSize: 16 },
  error: { fontSize: 14, textAlign: "center" },
  button: { borderRadius: 12, paddingVertical: 14, alignItems: "center", marginTop: 4 },
  buttonLabel: { fontSize: 16, fontWeight: "600" },
  link: { fontSize: 14, textAlign: "center", paddingVertical: 6 },
});
