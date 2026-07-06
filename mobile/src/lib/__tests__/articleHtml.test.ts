import { buildArticleHtml, escapeHtml } from "../articleHtml";
import type { ArticleDetail } from "../types";

const article: ArticleDetail = {
  id: 1,
  feed_id: 1,
  feed_title: "A <Feed>",
  title: 'Big "News" <script>',
  url: "https://example.com/a",
  comments_url: null,
  author: "Jane & Co",
  published_at: "2026-01-15T00:00:00Z",
  excerpt: "an excerpt",
  image_url: null,
  enriching: false,
  is_read: false,
  is_saved: false,
  summary: "First paragraph.\n\nSecond paragraph.",
  summary_short: "",
  summary_medium: "",
  content_html: "<p>Server-sanitized <em>body</em></p>",
  summary_model: null,
};

describe("escapeHtml", () => {
  it("escapes markup-significant characters", () => {
    expect(escapeHtml('<a href="x">&</a>')).toBe("&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;");
  });
});

describe("buildArticleHtml", () => {
  it("escapes composed fields but embeds the sanitized body as-is", () => {
    const html = buildArticleHtml(article, false);
    expect(html).toContain("Big &quot;News&quot; &lt;script&gt;");
    expect(html).toContain("A &lt;Feed&gt; · Jane &amp; Co");
    expect(html).toContain("<p>Server-sanitized <em>body</em></p>");
  });

  it("splits the summary into paragraphs", () => {
    const html = buildArticleHtml(article, false);
    expect(html).toContain("<p>First paragraph.</p><p>Second paragraph.</p>");
  });

  it("omits the summary block when there is no summary", () => {
    const html = buildArticleHtml({ ...article, summary: "" }, false);
    expect(html).not.toContain("AI summary");
  });

  it("falls back to the excerpt without content_html", () => {
    const html = buildArticleHtml({ ...article, content_html: "" }, true);
    expect(html).toContain("<p>an excerpt</p>");
  });

  it("themes light and dark differently", () => {
    expect(buildArticleHtml(article, false)).toContain("background: #ffffff");
    expect(buildArticleHtml(article, true)).toContain("background: #0b0d10");
  });
});
