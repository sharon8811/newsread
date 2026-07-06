import type { ArticleDetail } from "./types";

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Full HTML document for the reader WebView. content_html is already
 * sanitized server-side (nh3) before it's stored, so it's embedded as-is;
 * everything user-visible that we compose ourselves is escaped. */
export function buildArticleHtml(article: ArticleDetail, dark: boolean): string {
  const colors = dark
    ? { bg: "#0b0d10", text: "#e8eaed", muted: "#9aa0a6", border: "#2a2e35", link: "#8ab4f8" }
    : { bg: "#ffffff", text: "#1f2328", muted: "#66707b", border: "#e3e6ea", link: "#0b62d6" };
  const byline = [article.feed_title, article.author]
    .filter(Boolean)
    .map((part) => escapeHtml(part as string))
    .join(" · ");
  const published = article.published_at
    ? new Date(article.published_at).toLocaleDateString(undefined, {
        year: "numeric",
        month: "long",
        day: "numeric",
      })
    : "";
  const summaryBlock = article.summary
    ? `<section class="summary"><h2>AI summary</h2>${article.summary
        .split(/\n+/)
        .map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`)
        .join("")}</section>`
    : "";
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<style>
  body { margin: 0; padding: 16px 16px 48px; background: ${colors.bg}; color: ${colors.text};
         font: 17px/1.6 -apple-system, Roboto, "Segoe UI", sans-serif; word-wrap: break-word; }
  h1.title { font-size: 24px; line-height: 1.25; margin: 0 0 8px; }
  .byline { color: ${colors.muted}; font-size: 14px; margin-bottom: 20px; }
  .summary { border: 1px solid ${colors.border}; border-radius: 12px; padding: 4px 14px;
             margin-bottom: 20px; font-size: 15px; }
  .summary h2 { font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em;
                color: ${colors.muted}; margin: 10px 0 0; }
  img, video, iframe { max-width: 100%; height: auto; }
  pre { overflow-x: auto; padding: 12px; border-radius: 8px; background: ${dark ? "#15181d" : "#f4f6f8"}; }
  code { font-size: 14px; }
  blockquote { border-left: 3px solid ${colors.border}; margin-left: 0; padding-left: 14px; color: ${colors.muted}; }
  a { color: ${colors.link}; }
  hr { border: none; border-top: 1px solid ${colors.border}; }
</style>
</head>
<body>
<h1 class="title">${escapeHtml(article.title)}</h1>
<div class="byline">${[byline, published].filter(Boolean).join(" · ")}</div>
${summaryBlock}
<article>${article.content_html || `<p>${escapeHtml(article.excerpt)}</p>`}</article>
</body>
</html>`;
}
