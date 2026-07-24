type MessageSenderContext = Pick<
  chrome.runtime.MessageSender,
  "id" | "tab" | "url"
>;

function httpOrigin(value: string | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:"
      ? url.origin
      : null;
  } catch {
    return null;
  }
}

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

export function isAllowedCitationSourceSender(
  sender: MessageSenderContext,
  extensionId: string,
  pairedServerUrl: string,
): boolean {
  const senderOrigin = httpOrigin(sender.url);
  const pairedOrigin = httpOrigin(pairedServerUrl);
  return (
    sender.id === extensionId &&
    sender.tab?.id !== undefined &&
    senderOrigin !== null &&
    senderOrigin === pairedOrigin
  );
}

export function isAllowedCitationTargetSender(
  sender: MessageSenderContext,
  extensionId: string,
): boolean {
  return (
    sender.id === extensionId &&
    sender.tab?.id !== undefined &&
    httpOrigin(sender.url) !== null
  );
}
