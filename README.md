# NewsRead

> 📰 The social news reader — discover, summarize, and share articles with your take attached.

[![License: FSL-1.1-Apache-2.0](https://img.shields.io/badge/License-FSL--1.1--Apache--2.0-blue.svg)](LICENSE)
[![Status: In Development](https://img.shields.io/badge/Status-In%20Development-blue.svg)]()

## What Is NewsRead?

NewsRead is a source-available news reader built around a simple idea: **sharing an article should carry your context with it.** @mention friends and colleagues, attach a note explaining why the article matters, and build a shared reading culture with the people around you.

Traditional readers like Feedly treat sharing as an afterthought — you get a raw link. Chat tools like Slack are where articles actually get shared today, but the context gets lost in the scroll. NewsRead makes the share itself the product: every article travels with your commentary, so the recipient knows *why* you sent it and *what* to focus on.

## Features (Planned)

- **📡 RSS Feed Aggregation** — Subscribe to any RSS/Atom feed and read everything in a unified inbox
- **📢 Social Sharing** *(the flagship feature)* — @mention users, attach personal notes, and share curated collections
- **🤖 AI Summaries** — Instant summaries using your own API key, or local models via Ollama
- **💬 Article Q&A** — Ask an LLM questions about any article and get answers grounded in its full text
- **🏷️ Tagging** — Organize articles with color-coded, searchable tags
- **📖 Read Continuity** — Read/unread tracking, save-for-later, and cross-device sync
- **🔔 Push Notifications** — Native mobile alerts for @mentions and new articles
- **📚 Learning Experiences** — Plugin-based integrations (NotebookLM first) for podcasts and study guides

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Web Frontend | React / Next.js |
| Mobile App | React Native (Expo) |
| Backend API | Python / FastAPI |
| Database | PostgreSQL + Redis |
| LLM | User-provided API key (OpenAI/Anthropic), with Ollama as a local fallback |
| Auth | Auth.js (web) + backend-issued JWT (mobile) |
| Notifications | Expo Push Notifications |

## Project Structure (Planned)

```
newsread/
├── docs/              # Documentation (PRD, API, architecture)
├── frontend/          # Next.js web application
├── mobile/            # React Native (Expo) mobile app
├── backend/           # FastAPI REST API
├── docker-compose.yml # Local development environment
└── .github/           # CI/CD workflows
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
