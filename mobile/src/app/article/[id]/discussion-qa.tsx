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
import Markdown from "react-native-markdown-display";
import useSWR from "swr";

import {
  discussionRefFor,
  fetchHNItem,
  fetchHNThread,
  type HNItem,
} from "@/lib/discussions";
import { streamDiscussionQA, type QAStreamEvent } from "@/lib/qa";
import { usePalette, type Palette } from "@/lib/theme";
import type { AiStatus, ArticleDetail, ChatMessage } from "@/lib/types";

const SUGGESTIONS = [
  "Summarize the discussion",
  "Where do commenters disagree?",
  "What did commenters add beyond the article?",
  "Trace how the conversation evolved",
  "Find corrections and unresolved questions",
  "Draft a concise HN comment",
];

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
      <Markdown
        style={{
          body: { color: colors.text, fontSize: 15, lineHeight: 22 },
          paragraph: { marginTop: 0, marginBottom: 7 },
          link: { color: colors.tint },
        }}
      >
        {message.content}
      </Markdown>
    </View>
  );
}

export default function DiscussionQAScreen() {
  const params = useLocalSearchParams<{ id: string; prompt?: string }>();
  const { colors } = usePalette();
  const articleId = Number(params.id);
  const { data: ai } = useSWR<AiStatus>("/ai/status");
  const { data: article } = useSWR<ArticleDetail>(
    params.id ? `/articles/${params.id}` : null,
  );
  const ref = article ? discussionRefFor(article) : null;
  const { data: story } = useSWR<HNItem>(
    ref ? ["hackernews-story", ref.id] : null,
    () => fetchHNItem(ref!.id, { fresh: true }),
  );
  const { data: history, mutate } = useSWR<ChatMessage[]>(
    params.id ? `/articles/${params.id}/discussion/qa` : null,
  );
  const initialPrompt = Array.isArray(params.prompt) ? params.prompt[0] : params.prompt;
  const [input, setInput] = useState(initialPrompt ?? "");
  const [liveQuestion, setLiveQuestion] = useState<string | null>(null);
  const [liveText, setLiveText] = useState("");
  const [status, setStatus] = useState("Preparing discussion");
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<ScrollView>(null);

  const send = async (question: string) => {
    const trimmed = question.trim();
    if (!trimmed || liveQuestion || !story) return;
    setError(null);
    setInput("");
    setLiveQuestion(trimmed);
    setLiveText("");
    setStatus("Loading comments on this device");
    try {
      const snapshot = await fetchHNThread(story, 300);
      setStatus("Thinking");
      let finished = false;
      await streamDiscussionQA(articleId, trimmed, snapshot, (event: QAStreamEvent) => {
        if (event.type === "delta") setLiveText((text) => text + event.text);
        else if (event.type === "status") setStatus(event.state);
        else if (event.type === "tool_call") {
          setLiveText("");
          setStatus(`Using ${event.name}`);
        } else if (event.type === "done") finished = true;
      });
      if (!finished) throw new Error("The assistant's reply was interrupted");
      await mutate();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The assistant could not answer");
      setInput(trimmed);
    } finally {
      setLiveQuestion(null);
      setLiveText("");
    }
  };

  const messages = history ?? [];
  const showSuggestions = messages.length === 0 && !liveQuestion;

  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <Stack.Screen
        options={{ title: "Ask the discussion", headerBackButtonDisplayMode: "minimal" }}
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
              <Text style={[styles.hint, { color: colors.muted }]}>
                Ask about consensus, disagreements, corrections, or draft a comment.
              </Text>
              {SUGGESTIONS.map((suggestion) => (
                <Pressable
                  key={suggestion}
                  style={[styles.suggestion, { borderColor: colors.border }]}
                  onPress={() => send(suggestion)}
                  disabled={!story}
                >
                  <Text style={{ color: colors.tint, fontSize: 14 }}>{suggestion}</Text>
                </Pressable>
              ))}
            </View>
          )}

          {messages.map((message) => (
            <Bubble key={message.id} message={message} colors={colors} />
          ))}

          {liveQuestion && (
            <>
              <Bubble
                message={{
                  id: -1,
                  role: "user",
                  content: liveQuestion,
                  created_at: "",
                }}
                colors={colors}
              />
              <View style={[styles.assistantBubble, { borderLeftColor: colors.border }]}>
                {liveText ? (
                  <Markdown style={{ body: { color: colors.text, fontSize: 15, lineHeight: 22 } }}>
                    {liveText}
                  </Markdown>
                ) : (
                  <View style={styles.thinking}>
                    <ActivityIndicator size="small" color={colors.muted} />
                    <Text style={{ color: colors.muted, fontSize: 13 }}>{status}</Text>
                  </View>
                )}
              </View>
            </>
          )}

          {error && <Text style={{ color: colors.danger, textAlign: "center" }}>{error}</Text>}
          {!ai?.configured && (
            <Text style={{ color: colors.muted, textAlign: "center" }}>
              Add an AI provider in Settings to analyze this discussion.
            </Text>
          )}
        </ScrollView>

        {ai?.configured && (
          <View style={[styles.inputRow, { borderTopColor: colors.border }]}>
            <TextInput
              style={[
                styles.input,
                { borderColor: colors.border, color: colors.text, backgroundColor: colors.card },
              ]}
              placeholder="Ask or describe the comment you want to draft"
              placeholderTextColor={colors.muted}
              value={input}
              onChangeText={setInput}
              multiline
              editable={!liveQuestion}
            />
            <Pressable
              style={[
                styles.sendButton,
                { backgroundColor: colors.tint, opacity: liveQuestion || !story ? 0.5 : 1 },
              ]}
              onPress={() => send(input)}
              disabled={!!liveQuestion || !input.trim() || !story}
            >
              <Ionicons name="arrow-up" size={20} color={colors.background} />
            </Pressable>
          </View>
        )}
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  messages: { padding: 16, gap: 12, flexGrow: 1 },
  suggestions: { gap: 10, marginTop: 12 },
  hint: { fontSize: 14, textAlign: "center", marginBottom: 6 },
  suggestion: {
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
    maxWidth: "95%",
    borderLeftWidth: 2,
    paddingLeft: 12,
    alignSelf: "flex-start",
  },
  thinking: { flexDirection: "row", alignItems: "center", gap: 8, minHeight: 24 },
  inputRow: {
    borderTopWidth: StyleSheet.hairlineWidth,
    padding: 12,
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 8,
  },
  input: {
    flex: 1,
    minHeight: 42,
    maxHeight: 110,
    borderWidth: 1,
    borderRadius: 18,
    paddingHorizontal: 14,
    paddingVertical: 10,
    fontSize: 15,
  },
  sendButton: {
    width: 42,
    height: 42,
    borderRadius: 21,
    alignItems: "center",
    justifyContent: "center",
  },
});
