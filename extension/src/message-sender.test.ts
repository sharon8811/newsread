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
const extensionId = "newsread-extension-id";

describe("extension message sender policy", () => {
  it("allows page captures from a content script", () => {
    expect(
      isAllowedMessageSender(
        "CAPTURE_PAGE",
        {
          id: extensionId,
          tab: {},
          url: "https://article.example.com/",
        },
        extensionId,
      ),
    ).toBe(true);
  });

  it.each(privilegedMessageTypes)(
    "rejects %s from a content script",
    (messageType) => {
      expect(
        isAllowedMessageSender(
          messageType,
          {
            id: extensionId,
            tab: {},
            url: "https://article.example.com/",
          },
          extensionId,
        ),
      ).toBe(false);
    },
  );

  it.each(privilegedMessageTypes)(
    "allows %s from an extension page",
    (messageType) => {
      expect(isAllowedMessageSender(messageType, {}, extensionId)).toBe(true);
    },
  );

  it("allows a NewsRead options page opened in a tab", () => {
    expect(
      isAllowedMessageSender(
        "CLEAR_QUEUE",
        {
          id: extensionId,
          tab: {},
          url: `chrome-extension://${extensionId}/options/options.html`,
        },
        extensionId,
      ),
    ).toBe(true);
  });

  it("rejects another extension page opened in a tab", () => {
    expect(
      isAllowedMessageSender(
        "CLEAR_QUEUE",
        {
          id: "other-extension",
          tab: {},
          url: "chrome-extension://other-extension/options.html",
        },
        extensionId,
      ),
    ).toBe(false);
  });
});
