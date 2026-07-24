import {
  canonicalizeCaptureDocument,
  extractCaptureDocument,
  hasHistoryCaptureOptOut,
} from "./capture-document.js";
import {
  handleCitationClick,
  requestPendingCitation,
} from "./citation-content.js";
import { highlightCitationWithRetry } from "./citation-highlight.js";
import { capturePageImages } from "./image-capture.js";

(() => {
  async function capture() {
    if (!["http:", "https:"].includes(location.protocol)) return;
    if (hasHistoryCaptureOptOut()) return;
    const root =
      document.querySelector<HTMLElement>("article") ??
      document.querySelector<HTMLElement>("main") ??
      document.body;
    const capturedDocument = extractCaptureDocument();
    const text = capturedDocument
      ? canonicalizeCaptureDocument(capturedDocument).text
      : "";
    const description =
      document
        .querySelector<HTMLMetaElement>('meta[name="description"]')
        ?.content.replace(/\s+/g, " ")
        .trim() ?? "";
    const images =
      capturedDocument && root
        ? await capturePageImages(root)
        : { leadImage: null, favicon: null };
    await chrome.runtime
      .sendMessage({
        type: "CAPTURE_PAGE",
        candidate: {
          url: location.href,
          title: document.title,
          document: capturedDocument,
          leadImage: images.leadImage,
          favicon: images.favicon,
          textExcerpt: (description || text).slice(0, 500),
          capturedAt: new Date().toISOString(),
        },
      })
      .catch(() => undefined);
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === "CAPTURE_NOW") void capture();
  });
  document.addEventListener("click", (event) => {
    void handleCitationClick(event, window, chrome.runtime);
  });
  void requestPendingCitation(chrome.runtime).then((anchor) => {
    if (anchor) highlightCitationWithRetry(document, window, anchor);
  });
  void capture();
})();
