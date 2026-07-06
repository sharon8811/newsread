// Q&A streaming. React Native's built-in fetch can't read response bodies
// incrementally, so this uses expo/fetch (WinterCG-compliant, streams).

import { fetch as expoFetch } from "expo/fetch";

import { ApiError, getApiConfig } from "./api";
import { createSSEDecoder } from "./sse";
import type { ChatMessage } from "./types";

export type QAStreamEvent =
  | { type: "status"; state: string }
  | { type: "tool_call"; id: string; name: string; args: Record<string, unknown> }
  | { type: "tool_result"; id: string; summary: string }
  | { type: "delta"; text: string }
  | { type: "done"; message: ChatMessage }
  | { type: "error"; detail: string };

export async function streamQA(
  articleId: number,
  content: string,
  onEvent: (event: QAStreamEvent) => void,
): Promise<void> {
  const { baseUrl, token } = getApiConfig();
  if (!baseUrl) throw new ApiError("No server configured", 0);
  const res = await expoFetch(`${baseUrl}/api/articles/${articleId}/qa/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ content }),
  });
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => null);
    throw new ApiError(
      typeof data?.detail === "string" ? data.detail : `HTTP ${res.status}`,
      res.status,
    );
  }

  const reader = res.body.getReader();
  const textDecoder = new TextDecoder();
  const decodeFrames = createSSEDecoder();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    const events = decodeFrames(textDecoder.decode(value, { stream: true }));
    for (const event of events as QAStreamEvent[]) {
      if (event.type === "error") throw new ApiError(event.detail, 502);
      onEvent(event);
    }
  }
}
