(() => {
  function capture() {
    if (!["http:", "https:"].includes(location.protocol)) return;
    const root =
      document.querySelector<HTMLElement>("article") ??
      document.querySelector<HTMLElement>("main") ??
      document.body;
    const text = (root?.innerText ?? "").replace(/\s+/g, " ").trim().slice(0, 6000);
    const description =
      document
        .querySelector<HTMLMetaElement>('meta[name="description"]')
        ?.content.replace(/\s+/g, " ")
        .trim() ?? "";
    void chrome.runtime
      .sendMessage({
        type: "CAPTURE_PAGE",
        candidate: {
          url: location.href,
          title: document.title,
          text,
          textExcerpt: (description || text).slice(0, 500),
          capturedAt: new Date().toISOString(),
        },
      })
      .catch(() => undefined);
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === "CAPTURE_NOW") capture();
  });
  capture();
})();
