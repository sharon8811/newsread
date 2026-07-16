---
name: demo-video
description: Record a polished .mp4 product-demo video of the NewsRead web app for the GitHub README. Use this whenever the user asks for a demo video, screen recording, product walkthrough, promo clip, animated demo, or a video/GIF showing off the app — even if they don't say "demo video" explicitly. Runs a one-command pipeline (isolated servers + seeded demo data + scripted Playwright tour + ffmpeg) and produces a GitHub-ready mp4.
---

# NewsRead README demo video

One command produces the canonical demo mp4:

```bash
.claude/skills/demo-video/scripts/run_demo.sh [output.mp4]
# default output: docs/assets/newsread-demo.mp4
```

The pipeline: boot an isolated backend (:8010, `newsread_test` DB) → seed
demo content → boot the frontend (:3010, dev mode) → record a scripted
Playwright tour with system Chrome (headless, animated cursor + captions
injected) → convert to H.264 mp4 with ffmpeg. Servers are killed and the
test DB is the only one touched — the user's real docker-compose stack on
:3000/:8000 is never involved.

Knobs: `DEMO_SCHEME=dark|light` (default dark), `DEMO_SPEED=1.15` (playback
speed-up in the mp4), `CHROME_PATH` (alternative browser binary).

## Prerequisites (all usually already true on this machine)

- docker-compose Postgres up on :5433 (only the `newsread_test` DB is used)
- `backend/.venv` synced (`uv sync`), `frontend/node_modules` installed
- ffmpeg on PATH, Google Chrome installed
- Internet access — the tour fetches live HN comments, catalog feed
  previews, and picsum thumbnails. Offline the video still records but
  those sections degrade.
- Ports 8010/3010 free; the **arq worker must not be running** against the
  test DB (it would try to refresh the fake demo feeds — harmless but noisy).

## What the canonical tour shows (~80s before speed-up)

1. Title card over the loading inbox
2. Inbox scroll — auto-read marks articles as they pass; unread pill ticks down
3. Flagship article ("SQLite: 35% faster than the filesystem") — markdown AI summary
4. Related coverage — seeded embedding clusters produce SAME STORY + related tiers
5. Live Hacker News comments ("Show comments" on the real HN thread)
6. Catalog — search "nasa", open a feed preview modal, subscribe with quick settings
7. Outro card

## The moving parts (edit these to change the demo)

- `scripts/seed_demo_data.py` — demo user (`demo` / `demo-pass-1234`), 4
  fictional feeds, ~16 hand-written articles with markdown teaser summaries,
  hand-crafted pgvector embeddings (cosine ~0.08 → "same story", ~0.50 →
  "related", random ≈ orthogonal → hidden). The flagship's HN thread id is
  resolved live via the Algolia API. Idempotent; refuses any DB URL that
  isn't `newsread_test`.
- `scripts/record_tour.js` — the scene list. Does an unrecorded warm-up pass
  first so Next dev-mode compiles and images are cached before recording.
- `scripts/demo_helpers.js` — injected cursor/caption/title-card overlays and
  human-paced input primitives. Scrolling uses real wheel events so the
  IntersectionObserver auto-read behaves exactly as it does for a user.
- `scripts/convert_to_mp4.sh` — webm → H.264/yuv420p/faststart mp4.

## Verifying the result

Don't ship a video you haven't looked at. Extract spot-check frames:

```bash
ffmpeg -i docs/assets/newsread-demo.mp4 -vf fps=1/6 /tmp/demo-frames/f%02d.png
```

and read a few — confirm captions are legible, the summary/related/HN
sections actually appeared, and the subscribe button flipped to "Subscribed".
If a section is missing, check the recorder log and `$WORK/backend.log`
(the work dir is printed at the start of the run).

## Getting it into the README

GitHub only renders a video player for mp4s uploaded through its web editor
(drag-and-drop while editing README.md, which mints a
`github.com/user-attachments/assets/...` URL) — a committed `.mp4`
referenced by path will NOT render inline. So: hand the finished mp4 to the
user and tell them to drag it into the README on github.com. Committing the
file under `docs/assets/` as a canonical copy is still fine.

## Known gotchas

- Subscribe in the catalog scene triggers a synchronous real fetch of that
  feed on the backend — pick reliable feeds (the tour searches "nasa"); the
  wait allows 30s.
- Never seed via `POST /api/feeds` — it synchronously fetches the feed URL.
  The seed script inserts ORM rows directly.
- Frontend AI-summary UI hides entirely if `/api/ai/status` reports
  unconfigured; `run_demo.sh` exports a placeholder `NEWSREAD_OPENAI_API_KEY`
  so it renders. No LLM is ever called: summaries are pre-filled, and the
  summarize endpoint short-circuits when they exist.
- The embedding `model` tag on seeded vectors must equal the server's
  `NEWSREAD_OPENAI_EMBEDDING_MODEL` — run_demo.sh exports it for both
  processes; don't run the seed script with different env than the server.
