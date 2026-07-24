type MessageSenderContext = Pick<chrome.runtime.MessageSender, "tab">;

export function isAllowedMessageSender(
  messageType: unknown,
  sender: MessageSenderContext,
): boolean {
  return messageType === "CAPTURE_PAGE" || sender.tab === undefined;
}
