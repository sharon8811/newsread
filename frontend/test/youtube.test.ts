import { describe, it, expect } from "vitest";
import { isYouTubeChannelFeed, isYouTubeVideoUrl } from "@/lib/youtube";

describe("isYouTubeChannelFeed", () => {
  it.each([
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCBJycsmduvYEL83R_U4JriQ",
    "https://youtube.com/feeds/videos.xml?channel_id=UCBJycsmduvYEL83R_U4JriQ",
    "https://m.youtube.com/feeds/videos.xml?user=marquesbrownlee",
    "http://www.youtube.com/feeds/videos.xml?playlist_id=PL1234",
  ])("recognises %s", (url) => {
    expect(isYouTubeChannelFeed(url)).toBe(true);
  });

  it.each([
    "https://feed.example/rss",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com/@mkbhd",
    // A lookalike host must not pass — subdomain and suffix both.
    "https://youtube.com.evil.test/feeds/videos.xml?channel_id=UC1",
    "https://notyoutube.com/feeds/videos.xml?channel_id=UC1",
    // The hidden "Imported" feed and other non-http schemes.
    "newsread://imported/7",
    "not a url",
    "",
    null,
    undefined,
  ])("rejects %s", (url) => {
    expect(isYouTubeChannelFeed(url)).toBe(false);
  });
});

describe("isYouTubeVideoUrl", () => {
  it.each([
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com/watch/?v=dQw4w9WgXcQ&t=42",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ?t=42",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    "https://www.youtube.com/embed/dQw4w9WgXcQ",
    "https://www.youtube.com/live/dQw4w9WgXcQ",
    "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
  ])("recognises %s", (url) => {
    expect(isYouTubeVideoUrl(url)).toBe(true);
  });

  it.each([
    "https://example.com/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com/watch",
    "https://www.youtube.com/watch?v=tooshort",
    "https://www.youtube.com/@mkbhd",
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCBJycsmduvYEL83R_U4JriQ",
    "https://youtu.be/",
    "https://youtu.be/dQw4w9WgXcQ/extra",
    "https://www.youtube.com/playlist/dQw4w9WgXcQ",
    "",
    null,
    undefined,
  ])("rejects %s", (url) => {
    expect(isYouTubeVideoUrl(url)).toBe(false);
  });
});
