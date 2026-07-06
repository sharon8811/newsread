import { Ionicons } from "@expo/vector-icons";
import { Stack, useLocalSearchParams } from "expo-router";
import { useRef, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import useSWR from "swr";

import { usePalette, type Palette } from "@/lib/theme";
import { streamQA, type QAStreamEvent } from "@/lib/qa";
import type { AiStatus, ChatMessage, ToolEvent } from "@/lib/types";

const SUGGESTIONS = [
  "What are the key points?",
  "Explain this like I'm new to the topic",
  "What's the broader context?",
];

type LiveState = {
  question: string;
  text: string;
  tools: { id: string; name: string; done: boolean }[];
  status: string | null;
};

function ToolChips({ tools, colors }: { tools: LiveState["tools"]; colors: Palette }) {
  if (tools.length === 0) return null;
  return (
    <View style={styles.toolRow}>
      {tools.map((tool) => (
        <View key={tool.id} style={[styles.toolChip, { backgroundColor: colors.card }]}>
          <Ionicons
            name={tool.done ? "checkmark-circle-outline" : "sync-outline"}
            size={13}
            color={colors.muted}
          />
          <Text style={{ color: colors.muted, fontSize: 12 }}>{tool.name}</Text>
        </View>
      ))}
    </View>
  );
}

function Bubble({ message, colors }: { message: ChatMessage; colors: Palette }) {
  if (message.role === "user") {
    return (
      <View style={[styles.userBubble, { backgroundColor: colors.tint }]}>
        <Text style={{ color: colors.background, fontSize: 15, lineHeight: 21 }}>
          {message.content}
        </Text>
      </View>
    );
  }
  return (
    <View style={[styles.assistantBubble, { borderLeftColor: colors.border }]}>
      {(message.tool_events ?? []).length > 0 && (
        <ToolChips
          tools={(message.tool_events as ToolEvent[]).map((event, index) => ({
            id: String(index),
            name: event.name,
            done: true,
          }))}
          colors={colors}
        />
      )}
      <Text style={{ color: colors.text, fontSize: 15, lineHeight: 22 }} selectable>
        {message.content}
      </Text>
    </View>
  );
}

export default function QAScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { colors } = usePalette();
  const articleId = Number(id);
  const { data: ai } = useSWR<AiStatus>("/ai/status");
  const { data: history, mutate } = useSWR<ChatMessage[]>(id ? `/articles/${id}/qa` : null);
  const [input, setInput] = useState("");
  const [live, setLive] = useState<LiveState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<ScrollView>(null);

  const send = async (question: string) => {
    const trimmed = question.trim();
    if (!trimmed || live) return;
    setError(null);
    setInput("");
    setLive({ question: trimmed, text: "", tools: [], status: null });
    try {
      await streamQA(articleId, trimmed, (event: QAStreamEvent) => {
        setLive((current) => {
          if (!current) return current;
          switch (event.type) {
            case "status":
              return { ...current, status: event.state };
            case "tool_call":
              // Mirror the web app: text streamed before the first tool call
              // is preamble ("let me search…"), superseded by the final answer.
              return {
                ...current,
                text: "",
                tools: [...current.tools, { id: event.id, name: event.name, done: false }],
              };
            case "tool_result":
              return {
                ...current,
                tools: current.tools.map((tool) =>
                  tool.id === event.id ? { ...tool, done: true } : tool,
                ),
              };
            case "delta":
              return { ...current, text: current.text + event.text };
            default:
              return current;
          }
        });
      });
      await mutate();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLive(null);
    }
  };

  const messages = history ?? [];
  const showSuggestions = messages.length === 0 && !live;

  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <Stack.Screen
        options={{
          title: "Ask the article",
          headerBackButtonDisplayMode: "minimal",
        }}
      />
      <KeyboardAvoidingView
        style={styles.screen}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        keyboardVerticalOffset={Platform.OS === "ios" ? 100 : 0}
      >
        <ScrollView
          ref={scrollRef}
          contentContainerStyle={styles.messages}
          onContentSizeChange={() => scrollRef.current?.scrollToEnd({ animated: true })}
        >
          {showSuggestions && (
            <View style={styles.suggestions}>
              <Text style={[styles.suggestHint, { color: colors.muted }]}>
                Ask anything about this article
                {ai?.search ? " — the agent can also search the web" : ""}
              </Text>
              {SUGGESTIONS.map((suggestion) => (
                <Pressable
                  key={suggestion}
                  style={[styles.suggestChip, { borderColor: colors.border }]}
                  onPress={() => send(suggestion)}
                >
                  <Text style={{ color: colors.tint, fontSize: 14 }}>{suggestion}</Text>
                </Pressable>
              ))}
            </View>
          )}

          {messages.map((message) => (
            <Bubble key={message.id} message={message} colors={colors} />
          ))}

          {live && (
            <>
              <Bubble
                message={{
                  id: -1,
                  role: "user",
                  content: live.question,
                  created_at: "",
                }}
                colors={colors}
              />
              <View style={[styles.assistantBubble, { borderLeftColor: colors.border }]}>
                <ToolChips tools={live.tools} colors={colors} />
                {live.text ? (
                  <Text style={{ color: colors.text, fontSize: 15, lineHeight: 22 }}>
                    {live.text}
                  </Text>
                ) : (
                  <View style={styles.thinking}>
                    <ActivityIndicator size="small" color={colors.muted} />
                    <Text style={{ color: colors.muted, fontSize: 13 }}>
                      {live.status === "searching" ? "Searching…" : "Thinking…"}
                    </Text>
                  </View>
                )}
              </View>
            </>
          )}

          {error && (
            <Text style={{ color: colors.danger, fontSize: 14, textAlign: "center" }}>
              {error}
            </Text>
          )}
        </ScrollView>

        <View style={[styles.inputRow, { borderTopColor: colors.border }]}>
          <TextInput
            style={[
              styles.input,
              { borderColor: colors.border, color: colors.text, backgroundColor: colors.card },
            ]}
            placeholder="Ask a question…"
            placeholderTextColor={colors.muted}
            value={input}
            onChangeText={setInput}
            multiline
            editable={!live}
          />
          <Pressable
            style={[styles.sendButton, { backgroundColor: colors.tint, opacity: live ? 0.5 : 1 }]}
            onPress={() => send(input)}
            disabled={!!live || !input.trim()}
          >
            <Ionicons name="arrow-up" size={20} color={colors.background} />
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  messages: { padding: 16, gap: 12, flexGrow: 1 },
  suggestions: { gap: 10, marginTop: 12 },
  suggestHint: { fontSize: 14, textAlign: "center", marginBottom: 6 },
  suggestChip: {
    borderWidth: 1,
    borderRadius: 18,
    paddingHorizontal: 14,
    paddingVertical: 9,
    alignSelf: "center",
  },
  userBubble: {
    alignSelf: "flex-end",
    maxWidth: "85%",
    borderRadius: 16,
    borderBottomRightRadius: 4,
    paddingHorizontal: 14,
    paddingVertical: 9,
  },
  assistantBubble: {
    alignSelf: "stretch",
    borderLeftWidth: 2,
    paddingLeft: 12,
    gap: 8,
  },
  toolRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  toolChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderRadius: 10,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  thinking: { flexDirection: "row", alignItems: "center", gap: 8 },
  inputRow: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 10,
    padding: 12,
    borderTopWidth: StyleSheet.hairlineWidth,
  },
  input: {
    flex: 1,
    borderWidth: 1,
    borderRadius: 18,
    paddingHorizontal: 14,
    paddingTop: 9,
    paddingBottom: 9,
    fontSize: 15,
    maxHeight: 120,
  },
  sendButton: {
    width: 38,
    height: 38,
    borderRadius: 19,
    alignItems: "center",
    justifyContent: "center",
  },
});
