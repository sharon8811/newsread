import { useColorScheme } from "react-native";

const light = {
  background: "#ffffff",
  card: "#f4f6f8",
  text: "#1f2328",
  muted: "#66707b",
  border: "#e3e6ea",
  tint: "#0b62d6",
  danger: "#c62828",
};

const dark: typeof light = {
  background: "#0b0d10",
  card: "#15181d",
  text: "#e8eaed",
  muted: "#9aa0a6",
  border: "#2a2e35",
  tint: "#8ab4f8",
  danger: "#ef9a9a",
};

export type Palette = typeof light;

export function usePalette(): { colors: Palette; isDark: boolean } {
  const isDark = useColorScheme() === "dark";
  return { colors: isDark ? dark : light, isDark };
}
