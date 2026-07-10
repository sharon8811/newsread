import { useEffect, useMemo, useRef, useState } from "react";
import {
  AccessibilityInfo,
  Animated,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from "react-native";

import type { Palette } from "@/lib/theme";

// Honour the OS "reduce motion" setting so the pulse/dots hold still for
// users who opt out of animation (the RN equivalent of the web's
// prefers-reduced-motion gate).
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    let active = true;
    AccessibilityInfo.isReduceMotionEnabled().then((value) => {
      if (active) setReduced(value);
    });
    const sub = AccessibilityInfo.addEventListener("reduceMotionChanged", setReduced);
    return () => {
      active = false;
      sub.remove();
    };
  }, []);
  return reduced;
}

// Three dots that blink in sequence — the "thinking" motion mirrored from the
// web QA panel / generating label. Opacity is native-driver friendly.
function TypingDots({ color }: { color: string }) {
  const reduced = useReducedMotion();
  const a = useRef(new Animated.Value(0.3)).current;
  const b = useRef(new Animated.Value(0.3)).current;
  const c = useRef(new Animated.Value(0.3)).current;
  const dots = useMemo(() => [a, b, c], [a, b, c]);

  useEffect(() => {
    if (reduced) return;
    const loops = dots.map((value, i) =>
      Animated.loop(
        Animated.sequence([
          Animated.delay(i * 180),
          Animated.timing(value, { toValue: 1, duration: 300, useNativeDriver: true }),
          Animated.timing(value, { toValue: 0.3, duration: 300, useNativeDriver: true }),
          Animated.delay((dots.length - 1 - i) * 180),
        ]),
      ),
    );
    loops.forEach((loop) => loop.start());
    return () => loops.forEach((loop) => loop.stop());
  }, [dots, reduced]);

  return (
    <View style={styles.dots}>
      {dots.map((value, i) => (
        <Animated.View
          key={i}
          style={[styles.dot, { backgroundColor: color, opacity: reduced ? 0.6 : value }]}
        />
      ))}
    </View>
  );
}

// Placeholder shown wherever an AI illustration is still rendering (article
// hero, inbox cards, list thumbnails): a gently pulsing skeleton surface with
// the app's ✦ AI marker and a live "generating" label. Pass the frame's
// size/shape via `style`; `compact` drops the label for thumbnail-sized
// frames where it wouldn't fit. Announced politely to assistive tech.
export default function GeneratingImage({
  colors,
  style,
  compact = false,
}: {
  colors: Palette;
  style?: StyleProp<ViewStyle>;
  compact?: boolean;
}) {
  const reduced = useReducedMotion();
  const pulse = useRef(new Animated.Value(0.4)).current;

  useEffect(() => {
    if (reduced) return;
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 850, useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0.4, duration: 850, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [pulse, reduced]);

  return (
    <View
      style={[styles.frame, { backgroundColor: colors.card, borderColor: colors.border }, style]}
      accessible
      accessibilityRole="progressbar"
      accessibilityLabel="Generating illustration"
      accessibilityLiveRegion="polite"
    >
      <Animated.View
        style={[
          StyleSheet.absoluteFill,
          { backgroundColor: colors.border, opacity: reduced ? 0.5 : pulse },
        ]}
      />
      <View style={styles.label}>
        <Text style={[styles.spark, { color: colors.tint }]}>✦</Text>
        {!compact && (
          <>
            <Text style={[styles.labelText, { color: colors.muted }]}>
              generating illustration
            </Text>
            <TypingDots color={colors.muted} />
          </>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  frame: { alignItems: "center", justifyContent: "center", overflow: "hidden" },
  label: { flexDirection: "row", alignItems: "center", gap: 6 },
  spark: { fontSize: 13, fontWeight: "600" },
  labelText: { fontSize: 13 },
  dots: { flexDirection: "row", alignItems: "center", gap: 4, marginLeft: 2 },
  dot: { width: 4, height: 4, borderRadius: 2 },
});
