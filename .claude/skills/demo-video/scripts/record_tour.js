#!/usr/bin/env node
// Records the canonical NewsRead demo tour as a .webm video.
//
//   node record_tour.js --base-url http://localhost:3010 \
//     --api-url http://localhost:8010 --manifest manifest.json \
//     --out-dir /tmp/demo [--scheme dark|light]
//
// The tour: inbox scroll-to-read → flagship article (AI summary → related
// coverage → live HN comments) → catalog search → preview → subscribe.
// Prints `VIDEO: <path>` on success.

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright-core");
const {
  installOverlays,
  titleCard,
  caption,
  glideToLocator,
  humanClick,
  humanType,
  smoothWheel,
  sleep,
} = require("./demo_helpers");

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : fallback;
}

const BASE_URL = arg("base-url", "http://localhost:3010");
const API_URL = arg("api-url", "http://localhost:8010");
const MANIFEST = arg("manifest");
const OUT_DIR = arg("out-dir", process.cwd());
const SCHEME = arg("scheme", "dark");
const CHROME =
  process.env.CHROME_PATH ||
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const VIEW = { width: 1600, height: 900 };

/** Wheel-scroll until the locator sits in the upper part of the viewport —
 * keeps scroll-linked UI (auto-read) behaving naturally, unlike an instant
 * scrollIntoView jump. The article list is WINDOWED (off-screen items are
 * unmounted), so never auto-wait on the element: probe with count() and keep
 * scrolling in `seek` direction until it mounts. */
async function wheelToLocator(page, locator, { target = 0.32, seek = 1 } = {}) {
  let last = "never-attached";
  let prevY = null;
  let stalled = 0;
  for (let i = 0; i < 60; i++) {
    const attached = (await locator.count().catch(() => 0)) > 0;
    const box = attached ? await locator.boundingBox().catch(() => null) : null;
    if (box) {
      const delta = box.y + box.height / 2 - VIEW.height * target;
      last = `box.y=${Math.round(box.y)} delta=${Math.round(delta)}`;
      if (Math.abs(delta) < 60) return;
      // Visible but the container can't scroll any further (e.g. the section
      // sits near the bottom of the page): that's as good as it gets.
      if (prevY !== null && Math.abs(box.y - prevY) < 2 && ++stalled >= 2) return;
      if (prevY === null || Math.abs(box.y - prevY) >= 2) stalled = 0;
      prevY = box.y;
      await smoothWheel(page, Math.max(-500, Math.min(500, delta)), 260);
    } else {
      last = attached ? "attached-no-box" : "not-attached";
      prevY = null;
      await smoothWheel(page, 500 * seek, 260);
    }
  }
  throw new Error(`wheelToLocator: never reached viewport (last: ${last})`);
}

async function main() {
  const manifest = JSON.parse(fs.readFileSync(MANIFEST, "utf8"));

  // Mint a token so the recording skips the login screen entirely.
  const loginRes = await fetch(`${API_URL}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      identifier: manifest.identifier,
      password: manifest.password,
    }),
  });
  if (!loginRes.ok) throw new Error(`login failed: ${loginRes.status}`);
  const { access_token: token } = await loginRes.json();

  const browser = await chromium.launch({
    executablePath: CHROME,
    headless: true,
  });

  const flagship = `[data-article-id="${manifest.flagship_article_id}"]`;

  // ---- Warm-up pass (not recorded): compile Next dev routes, prime SWR-side
  // backend caches, and preload images so the recorded run has no jank.
  {
    const warm = await browser.newContext({
      viewport: VIEW,
      colorScheme: SCHEME,
    });
    await warm.addInitScript(
      (t) => localStorage.setItem("newsread_token", t),
      token
    );
    const p = await warm.newPage();
    // Warm the article route with a bottom-of-list filler, NOT the flagship:
    // opening an article marks it read, and the inbox's default unread tab
    // would then hide the flagship from the recorded run.
    for (const route of [
      "/",
      `/article/${manifest.warmup_article_id}`,
      "/catalog",
    ]) {
      await p.goto(BASE_URL + route, { waitUntil: "networkidle" }).catch(() => {});
    }
    await warm.close();
    console.log("warm-up done");
  }

  // ---- Recorded tour ------------------------------------------------------
  const context = await browser.newContext({
    viewport: VIEW,
    colorScheme: SCHEME,
    recordVideo: { dir: OUT_DIR, size: VIEW },
  });
  await context.addInitScript(
    (t) => localStorage.setItem("newsread_token", t),
    token
  );
  await installOverlays(context);
  const page = await context.newPage();
  globalThis.__demoPage = page;

  // Scene 1 — intro card over the loading inbox.
  await page.goto(BASE_URL + "/");
  await titleCard(
    page,
    "NewsRead",
    "A self-hosted, AI-assisted RSS reader",
    2800
  );
  await page.waitForSelector("[data-article-id]", { timeout: 30000 });
  await sleep(600);

  // Scene 2 — scroll the inbox; auto-read marks articles as they pass.
  await caption(page, "One inbox for all your feeds — read marks itself as you scroll");
  const main = page.locator("main").first();
  await glideToLocator(page, main.locator("[data-article-id]").first());
  // Scroll ~4 cards: enough for auto-read to visibly fire while keeping the
  // flagship article (7th newest) still ahead of us for the next scene.
  await smoothWheel(page, 1500, 5500);
  await sleep(1200); // let the unread pill tick down on camera
  await caption(page, null);

  // Scene 3 — open the flagship article, show the AI summary.
  await wheelToLocator(page, page.locator(flagship));
  await sleep(400);
  await humanClick(page, page.locator(flagship), { settleMs: 900 });
  await page.waitForSelector("h1", { timeout: 20000 });
  await caption(page, "AI teaser summaries, rendered in Markdown");
  const summary = page.getByText("AI Summary", { exact: true }).first();
  await wheelToLocator(page, summary, { target: 0.22 });
  await sleep(2600);
  await smoothWheel(page, 350, 1400);
  await sleep(1400);
  await caption(page, null);

  // Scene 4 — related coverage (seeded embedding clusters).
  const related = page.getByText("Related coverage", { exact: true }).first();
  await related.waitFor({ timeout: 20000 });
  await caption(page, "Related coverage, linked across your feeds");
  await wheelToLocator(page, related, { target: 0.25 });
  await sleep(900);
  const relatedItem = page
    .locator("div.cursor-pointer", { has: page.locator("p, h3") })
    .filter({ hasText: /./ })
    .first();
  await glideToLocator(page, relatedItem).catch(() => {});
  await sleep(1800);
  await caption(page, null);

  // Scene 5 — live Hacker News discussion.
  const hn = page.locator("#hacker-news-discussion");
  await hn.waitFor({ timeout: 20000 });
  await caption(page, "The Hacker News conversation, fetched live");
  await wheelToLocator(page, hn, { target: 0.2 });
  await sleep(1000);
  const showComments = page.getByRole("button", { name: "Show comments" });
  if (await showComments.isVisible().catch(() => false)) {
    await humanClick(page, showComments, { settleMs: 500 });
    await page
      .waitForSelector('[aria-label="Loading comments"]', {
        state: "detached",
        timeout: 25000,
      })
      .catch(() => {});
    await sleep(800);
    await smoothWheel(page, 900, 4200);
    await sleep(1000);
  }
  await caption(page, null);

  // Scene 6 — catalog: search, preview, subscribe.
  const sidebar = page.locator("aside").first();
  await humanClick(page, sidebar.locator('a[href="/catalog"]'), {
    settleMs: 900,
  });
  await page.waitForSelector('[aria-label="Search feeds"]', { timeout: 20000 });
  await caption(page, "A curated catalog of hundreds of feeds");
  await sleep(800);
  await smoothWheel(page, 500, 2200);
  await sleep(600);
  await humanType(page, page.locator('[aria-label="Search feeds"]'), "nasa");
  await sleep(2200); // debounce + results
  await caption(page, null);

  const firstCard = page.locator("main article").first();
  await firstCard.waitFor({ timeout: 15000 });
  await humanClick(page, firstCard, { settleMs: 800 });
  const overlay = page.locator('[data-testid="modal-overlay"]');
  await overlay.waitFor({ timeout: 10000 });
  await caption(page, "Live preview, then subscribe with per-feed AI settings");
  await page
    .getByText("Latest stories", { exact: true })
    .waitFor({ timeout: 15000 })
    .catch(() => {}); // preview may fall back or stay skeletal; keep rolling
  await sleep(2200);

  // Radix portals Dialog.Content as a SIBLING of the overlay — scope to the
  // dialog role, not the overlay testid, or every locator comes up empty.
  const modal = page.getByRole("dialog");
  const chips = modal.getByText("AI images", { exact: true }).first();
  await glideToLocator(page, chips).catch(() => {});
  await sleep(900);
  // The button's accessible name includes its "+" icon — match loosely.
  const subscribeBtn = modal
    .getByRole("button", { name: /subscribe/i })
    .first();
  await humanClick(page, subscribeBtn, { settleMs: 400 });
  // Subscribe fetches the feed synchronously; success flips the modal's
  // footer button to "View feed" (the card behind says "Subscribed").
  await modal
    .getByText(/View feed|Subscribed/, { exact: false })
    .first()
    .waitFor({ timeout: 30000 })
    .catch(() => {});
  await sleep(1500);
  await caption(page, null);
  await humanClick(page, page.locator('[aria-label="Close"]').first(), {
    settleMs: 600,
  });

  // Scene 7 — outro.
  await titleCard(page, "NewsRead", "Your feeds. Your server. Read smarter.", 3000);

  const video = page.video();
  await page.close();
  await context.close(); // flushes the recording to disk
  await browser.close();
  console.log("VIDEO: " + (await video.path()));
}

main().catch(async (err) => {
  console.error(err);
  // Leave evidence for debugging: the recorder is headless, so a screenshot
  // of the failure moment is the fastest way to see what went wrong.
  try {
    if (globalThis.__demoPage && !globalThis.__demoPage.isClosed()) {
      const shot = path.join(OUT_DIR, "failure.png");
      await globalThis.__demoPage.screenshot({ path: shot });
      console.error("failure screenshot: " + shot);
    }
  } catch {}
  process.exit(1);
});
