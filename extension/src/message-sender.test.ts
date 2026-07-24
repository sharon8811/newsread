import { describe, expect, it } from "vitest";
import { isAllowedMessageSender } from "./message-sender.js";

const privilegedMessageTypes = [
  "GET_STATUS",
  "PAIR",
  "SET_PAUSED",
  "SET_CAPTURE_MODE",
  "INDEX_CURRENT",
  "SYNC_NOW",
  "CLEAR_QUEUE",
  "DISCONNECT",
  "SAVE_EXCLUSIONS",
  "IMPORT_HISTORY",
];

describe("extension message sender policy", () => {
  it("allows page captures from a content script", () => {
    expect(isAllowedMessageSender("CAPTURE_PAGE", { tab: {} })).toBe(true);
  });

  it.each(privilegedMessageTypes)(
    "rejects %s from a content script",
    (messageType) => {
      expect(isAllowedMessageSender(messageType, { tab: {} })).toBe(false);
    },
  );

  it.each(privilegedMessageTypes)(
    "allows %s from an extension page",
    (messageType) => {
      expect(isAllowedMessageSender(messageType, {})).toBe(true);
    },
  );
});
