import { DarkTheme, DefaultTheme, ThemeProvider } from "@react-navigation/native";
import { Stack } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";
import { useEffect } from "react";
import { useColorScheme } from "react-native";
import { SWRConfig } from "swr";

import { fetcher } from "@/lib/api";
import { AuthProvider, useAuth } from "@/lib/auth";

SplashScreen.preventAutoHideAsync();

function RootNavigator() {
  const { status } = useAuth();

  useEffect(() => {
    if (status !== "loading") SplashScreen.hideAsync();
  }, [status]);

  if (status === "loading") return null; // splash screen stays up

  // Exactly one guard is true at a time; expo-router lands on the first
  // available screen and re-routes automatically whenever `status` changes.
  return (
    <Stack>
      <Stack.Protected guard={status === "signed-in"}>
        <Stack.Screen name="index" options={{ title: "NewsRead" }} />
        <Stack.Screen name="catalog" options={{ title: "Catalog" }} />
        <Stack.Screen name="imported" options={{ title: "Imported" }} />
        <Stack.Screen name="article/[id]/index" options={{ title: "" }} />
        <Stack.Screen name="article/[id]/qa" options={{ title: "Ask the article" }} />
        <Stack.Screen name="entity/[id]" options={{ title: "" }} />
      </Stack.Protected>
      <Stack.Protected guard={status === "signed-out"}>
        <Stack.Screen name="login" options={{ headerShown: false }} />
      </Stack.Protected>
      <Stack.Protected guard={status === "no-server"}>
        <Stack.Screen name="onboarding" options={{ headerShown: false }} />
      </Stack.Protected>
    </Stack>
  );
}

export default function RootLayout() {
  const colorScheme = useColorScheme();
  return (
    <AuthProvider>
      <SWRConfig value={{ fetcher }}>
        <ThemeProvider value={colorScheme === "dark" ? DarkTheme : DefaultTheme}>
          <StatusBar style="auto" />
          <RootNavigator />
        </ThemeProvider>
      </SWRConfig>
    </AuthProvider>
  );
}
