# NewsRead

> 📰 The social news reader — discover, summarize, and share articles with your take attached.

[![License: FSL-1.1-Apache-2.0](https://img.shields.io/badge/License-FSL--1.1--Apache--2.0-blue.svg)](LICENSE)
[![Status: In Development](https://img.shields.io/badge/Status-In%20Development-blue.svg)]()

## What Is NewsRead?

NewsRead is a source-available news reader built around a simple idea: **sharing an article should carry your context with it.** @mention friends and colleagues, attach a note explaining why the article matters, and build a shared reading culture with the people around you.

Traditional readers like Feedly treat sharing as an afterthought — you get a raw link. Chat tools like Slack are where articles actually get shared today, but the context gets lost in the scroll. NewsRead makes the share itself the product: every article travels with your commentary, so the recipient knows *why* you sent it and *what* to focus on.

## Features

**Working today:**

- **📡 Feed Aggregation** — Subscribe to RSS, Atom, and JSON feeds; a background worker keeps them fresh
- **📢 Social Sharing** *(the flagship feature)* — @mention users and attach a note; recipients see your commentary front and center in "Shared with me"
- **🤖 AI Summaries** — Full article text fetched via [Scrapling](https://github.com/D4Vinci/Scrapling) + trafilatura, summarized by any OpenAI-compatible LLM (OpenAI, vLLM, LiteLLM, Ollama); cached per article
- **💬 Ask the Article** — A Q&A agent grounded in the article's full text, with per-article conversation history — and honest "the article doesn't say" answers
- **📖 Read Continuity** — Per-user read/unread tracking, save-for-later, mark-all-read
- **⌨️ Power-user keyboard** — `j`/`k` navigate, `enter` opens, `s` saves, `m` toggles read
- **🔍 Search** — Full-text search across titles and excerpts

**Planned:**

- **🏷️ Tagging** — Color-coded, searchable tags and shareable collections
- **🔔 Push Notifications** — Native mobile alerts for @mentions (React Native app)
- **📚 Learning Experiences** — Plugin-based integrations (NotebookLM first) for podcasts and study guides

## Quick Start

Run everything (web app, API, worker, Postgres, Redis) with Docker:

```bash
cp .env.example .env   # optional — enables AI summaries and Q&A
docker compose up -d --build
```

Then open [http://localhost:3000](http://localhost:3000), create an account, and add a feed — for example:

```
https://hnrss.org/newest.jsonfeed?points=100
```

To try the social loop, register a second account in a private browser window and share an article at it with a note.

### Local development

```bash
# Postgres + Redis only
docker compose up -d db redis

# Backend (http://localhost:8000, docs at /docs)
cd backend
uv venv .venv && uv pip install -p .venv/bin/python -r requirements.txt
.venv/bin/uvicorn app.main:app --reload

# Feed-polling worker (optional in dev; the API fetches on subscribe)
.venv/bin/arq app.worker.WorkerSettings

# Frontend (http://localhost:3000)
cd frontend
npm install && npm run dev
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Web Frontend | Next.js (App Router) + Tailwind CSS + SWR |
| Backend API | Python / FastAPI (async SQLAlchemy) |
| Background Jobs | ARQ worker on Redis (feed polling) |
| Database | PostgreSQL |
| Auth | Backend-issued JWT (email/username + password) |
| Full-text Extraction | Scrapling (fetch) + trafilatura (article extraction) |
| LLM | Any OpenAI-compatible endpoint (OpenAI, vLLM, LiteLLM, Ollama) |
| Mobile App *(planned)* | React Native (Expo) + push notifications |

## Project Structure

```
newsread/
├── docs/              # Documentation (PRD)
├── frontend/          # Next.js web application
├── backend/           # FastAPI REST API + ARQ feed-polling worker
│   └── app/
│       ├── routers/   # auth, users, feeds, articles, shares
│       ├── fetcher.py # RSS/Atom/JSON Feed parsing + sanitization
│       └── worker.py  # periodic feed refresh
├── docker-compose.yml # Full stack: web, api, worker, Postgres, Redis
└── mobile/            # React Native (Expo) app — planned, Phase 3
```

## License

NewsRead is [Fair Source](https://fair.io) software under the **Functional Source License (FSL-1.1-Apache-2.0)**. Each release automatically becomes **Apache 2.0** two years after it is published.

- ✅ Free to use, modify, and self-host — personally or inside your organization
- ✅ Free to redistribute and contribute back
- ❌ May not be sold or offered as a competing commercial product or service

See [LICENSE](LICENSE) for the exact terms.

## Documentation

- [Product Requirements Document (PRD)](docs/PRD.md)

## Contributing

Contributions are welcome once development begins. On the roadmap:

- `CONTRIBUTING.md` with setup and workflow guidelines
- Issue and pull request templates
- Curated "good first issue" labels

---

*Built for people who care about sharing what they read.*
