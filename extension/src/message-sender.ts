type MessageSenderContext = Pick<
  chrome.runtime.MessageSender,
  "id" | "tab" | "url"
>;

export function isAllowedMessageSender(
  messageType: unknown,
  sender: MessageSenderContext,
  extensionId: string,
): boolean {
  if (messageType === "CAPTURE_PAGE" || sender.tab === undefined) return true;
  return (
    sender.id === extensionId &&
    sender.url?.startsWith(`chrome-extension://${extensionId}/`) === true
  );
}
