// Shared helpers for recording a polished product-demo video with Playwright.
// Injects a visible animated cursor, lower-third captions, and a full-screen
// title card, and provides human-paced mouse/scroll primitives so the
// recording reads like a person demoing the app rather than a robot.

/**
 * Install the fake cursor + caption overlay into every page of the context.
 * Call once on the BrowserContext before any page navigates.
 */
async function installOverlays(context) {
  await context.addInitScript(() => {
    if (window.__demoOverlayInstalled) return;
    window.__demoOverlayInstalled = true;

    const ready = (fn) => {
      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", fn, { once: true });
      } else {
        fn();
      }
    };

    ready(() => {
      const style = document.createElement("style");
      style.textContent = `
        #__demo-cursor {
          position: fixed; top: 0; left: 0; z-index: 2147483647;
          width: 26px; height: 26px; pointer-events: none;
          transform: translate(-4px, -3px);
          transition: opacity 200ms ease;
          filter: drop-shadow(0 1px 3px rgba(0,0,0,.45));
        }
        #__demo-cursor.__demo-click { animation: __demo-click-pulse 320ms ease; }
        @keyframes __demo-click-pulse {
          0% { transform: translate(-4px,-3px) scale(1); }
          40% { transform: translate(-4px,-3px) scale(.78); }
          100% { transform: translate(-4px,-3px) scale(1); }
        }
        #__demo-caption {
          /* bottom offset clears the app's own "{n} unread" floating pill */
          position: fixed; left: 50%; bottom: 96px; z-index: 2147483646;
          transform: translateX(-50%) translateY(16px);
          max-width: 72vw;
          padding: 13px 26px; border-radius: 999px;
          background: rgba(17, 20, 28, .82);
          -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px);
          border: 1px solid rgba(255,255,255,.14);
          color: #fff; font: 500 17px/1.4 -apple-system, "Segoe UI", sans-serif;
          letter-spacing: .01em; white-space: nowrap;
          opacity: 0; transition: opacity 420ms ease, transform 420ms ease;
          pointer-events: none;
        }
        #__demo-caption.__show { opacity: 1; transform: translateX(-50%) translateY(0); }
        #__demo-title-card {
          position: fixed; inset: 0; z-index: 2147483647;
          display: flex; flex-direction: column; align-items: center; justify-content: center;
          gap: 14px; background: #0b0e14;
          opacity: 0; transition: opacity 600ms ease; pointer-events: none;
        }
        #__demo-title-card.__show { opacity: 1; }
        #__demo-title-card h1 {
          color: #fff; font: 650 52px/1.15 -apple-system, "Segoe UI", sans-serif;
          letter-spacing: -.02em; margin: 0;
        }
        #__demo-title-card p {
          color: rgba(255,255,255,.66); font: 400 21px/1.5 -apple-system, "Segoe UI", sans-serif;
          margin: 0;
        }
      `;
      document.head.appendChild(style);

      const cursor = document.createElement("div");
      cursor.id = "__demo-cursor";
      cursor.innerHTML =
        '<svg viewBox="0 0 24 24" width="26" height="26">' +
        '<path d="M5.5 3.2 19 12.4l-6.6 1.1-3.4 5.9z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>';
      cursor.style.opacity = "0";
      document.body.appendChild(cursor);

      window.__demoCursorTo = (x, y) => {
        cursor.style.opacity = "1";
        cursor.style.left = x + "px";
        cursor.style.top = y + "px";
      };
      document.addEventListener(
        "mousemove",
        (e) => window.__demoCursorTo(e.clientX, e.clientY),
        true
      );
      document.addEventListener(
        "mousedown",
        () => {
          cursor.classList.remove("__demo-click");
          void cursor.offsetWidth; // restart animation
          cursor.classList.add("__demo-click");
        },
        true
      );

      const caption = document.createElement("div");
      caption.id = "__demo-caption";
      document.body.appendChild(caption);
      window.__demoCaption = (text) => {
        if (!text) {
          caption.classList.remove("__show");
          return;
        }
        caption.textContent = text;
        caption.classList.add("__show");
      };

      const card = document.createElement("div");
      card.id = "__demo-title-card";
      document.body.appendChild(card);
      window.__demoTitleCard = (title, subtitle) => {
        if (!title) {
          card.classList.remove("__show");
          return;
        }
        card.innerHTML = `<h1></h1><p></p>`;
        card.querySelector("h1").textContent = title;
        card.querySelector("p").textContent = subtitle || "";
        card.classList.add("__show");
      };
    });
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Show a full-screen title card for `holdMs`, then fade it away. */
async function titleCard(page, title, subtitle, holdMs = 2600) {
  await page.evaluate(
    ([t, s]) => window.__demoTitleCard(t, s),
    [title, subtitle]
  );
  await sleep(holdMs);
  await page.evaluate(() => window.__demoTitleCard(null));
  await sleep(700);
}

/** Show a lower-third caption. Pass null/omit text to hide it. */
async function caption(page, text) {
  await page.evaluate((t) => window.__demoCaption(t || null), text ?? null);
  if (text) await sleep(500);
}

/** Move the mouse to a point along an eased path so the cursor glides. */
async function glideTo(page, x, y, durationMs = 650) {
  const steps = Math.max(12, Math.round(durationMs / 16));
  await page.mouse.move(x, y, { steps });
}

/** Glide to the center of a locator; returns the target point. */
async function glideToLocator(page, locator, durationMs = 650) {
  await locator.scrollIntoViewIfNeeded();
  const box = await locator.boundingBox();
  if (!box) throw new Error("glideToLocator: element has no bounding box");
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await glideTo(page, x, y, durationMs);
  return { x, y };
}

/** Human-paced click: glide over, brief hover, click, settle. */
async function humanClick(page, locator, { hoverMs = 350, settleMs = 600 } = {}) {
  await glideToLocator(page, locator);
  await sleep(hoverMs);
  await locator.click();
  await sleep(settleMs);
}

/** Type text with per-key delay so it reads naturally on video. */
async function humanType(page, locator, text, { delay = 55 } = {}) {
  await humanClick(page, locator, { settleMs: 200 });
  await locator.fill("");
  await locator.pressSequentially(text, { delay });
}

/**
 * Smooth-scroll the main scroll container (or window) by `totalPx` over
 * `durationMs`, using many small wheel events so scroll-linked UI
 * (IntersectionObservers, auto-read) behaves exactly as it does for a user.
 */
async function smoothWheel(page, totalPx, durationMs = 2200) {
  const tickMs = 40;
  const ticks = Math.max(1, Math.round(durationMs / tickMs));
  const per = totalPx / ticks;
  for (let i = 0; i < ticks; i++) {
    // ease-in-out weighting
    const t = i / ticks;
    const ease = 0.5 - 0.5 * Math.cos(Math.PI * 2 * Math.min(t, 1 - t) + Math.PI * (t > 0.5 ? 1 : 0));
    await page.mouse.wheel(0, per * (0.6 + 0.8 * ease));
    await sleep(tickMs);
  }
}

module.exports = {
  installOverlays,
  titleCard,
  caption,
  glideTo,
  glideToLocator,
  humanClick,
  humanType,
  smoothWheel,
  sleep,
};
