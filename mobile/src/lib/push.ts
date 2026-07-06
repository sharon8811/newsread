// Best-effort Expo push registration. Remote push needs a development build
// with an EAS project id (Expo Go dropped remote notifications in SDK 53), so
// every step here degrades to a silent no-op — the app must work fine without.

import Constants from "expo-constants";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";

import { api } from "./api";

let registeredToken: string | null = null;

export async function registerDevicePush(): Promise<void> {
  if (Platform.OS !== "ios" && Platform.OS !== "android") return;
  const projectId = Constants.expoConfig?.extra?.eas?.projectId;
  if (!projectId) return;
  const permission = await Notifications.requestPermissionsAsync();
  if (!permission.granted) return;
  const { data: token } = await Notifications.getExpoPushTokenAsync({ projectId });
  await api("/devices", {
    method: "POST",
    body: { push_token: token, platform: Platform.OS },
  });
  registeredToken = token;
}

export async function unregisterDevicePush(): Promise<void> {
  if (!registeredToken) return;
  await api(`/devices?push_token=${encodeURIComponent(registeredToken)}`, { method: "DELETE" });
  registeredToken = null;
}
