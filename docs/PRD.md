# NewsRead — Product Requirements Document (PRD)

> **Version:** 2.2 (Decisions Locked)
> **Date:** July 3, 2026
> **Status:** Ready for Development
> **License:** FSL-1.1-Apache-2.0 (Functional Source License) — free to use, modify, and self-host; no competing commercial use; each release converts to Apache 2.0 after two years

---

## 1. Vision

**NewsRead** is a source-available social news reader where discovering great content is only half the experience — the other half is **sharing it with context**. Unlike traditional RSS readers, where you read alone, NewsRead makes every article a conversation starter: @mention friends and colleagues, attach your take, and build a shared reading culture.

**One-liner:** The social news reader — discover, summarize, and share articles with your take attached.

**The wedge:** Social sharing with @mentions and personal notes. Feedly has summaries. Readwise has highlighting. Nobody does "share an article with your commentary attached" well. That is NewsRead's opening.

---

## 2. Problem Statement

Modern news consumption is **isolated**:

- **Sharing strips context** — passing along an article means pasting a raw link; the recipient doesn't know *why* you shared it or what to focus on
- **No shared reading culture** — teams, friend groups, and communities lack a dedicated place where articles flow with commentary
- **Information overload** — too many sources, too little time (the classic RSS problem)
- **Passive consumption** — readers consume without engaging, discussing, or retaining

**The insight:** The value isn't just reading articles — it's reading what *others think is worth reading*, and *why*.

---

## 3. Locked Decisions

> These were debated and decided during the brainstorming session. They replace the previous "Open Questions" section.

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Demo cloud instance from day 1** | A minimal instance is deployed alongside Phase 1 development; it later becomes the paid cloud tier |
| 2 | **User-provided API key + local models** | API key is the default (quality); local models via Ollama serve as the free fallback |
| 3 | **React Native from day 1** | Native mobile experience is worth the extra effort — push notifications, offline support, best UX |
| 4 | **Basic sharing from day 1** | @mentions and shared feeds ship in the MVP; workspaces and teams come later |
| 5 | **Plugin-based learning (NotebookLM first)** | Abstract interface, NotebookLM as the first plugin, custom generators later |
| 6 | **Store full article text** | Enables in-app reading and the best Q&A experience; see §7.3 for the content policy and its risks |
| 7 | **Python/FastAPI backend** | Best LLM ecosystem, async support, fast development |
| 8 | **Developers/tech readers as first audience** | Already RSS users; likely to self-host, contribute code, and report bugs |
| 9 | **Social sharing is the wedge** | @mentions with notes — no competitor does this well |
| 10 | **FSL-1.1-Apache-2.0 license** | Free self-hosting and contribution, no competing commercial use; converts to Apache 2.0 after 2 years (Fair Source); paid features stay cloud-only |

---

## 4. Solution Overview

NewsRead is a web + native mobile application that:

1. **Aggregates** news from RSS feeds (initially), with an extensible source model
2. **Summarizes** articles with AI, so users grasp the essence in seconds
3. **Enables social sharing** — @mention people, attach notes, share collections (**the core differentiator**)
4. **Supports article Q&A** — ask an LLM agent questions grounded in the full article text
5. **Remembers context** — tracks read/unread state, saves for later, syncs seamlessly across devices
6. **Notifies proactively** — @mention and new-article notifications on web and mobile
7. **Creates learning experiences** *(post-MVP)* — plugin-based integrations (NotebookLM first) for podcasts and study guides

---

## 5. Feature Requirements

Features 5.1–5.7 constitute the MVP. Feature 5.8 is post-MVP and is included here to anchor the plugin architecture decision.

### 5.1 RSS Feed Aggregation

**Priority:** P0 (Core)

- Users can subscribe to RSS/Atom feeds by URL
- A background worker periodically fetches and parses new articles
- Feeds are stored once globally; users hold subscriptions to them (avoids duplicate fetching and duplicate article rows)
- Deduplication across feeds via canonical URL, with content-hash fallback
- Configurable refresh intervals per feed
- Support for common formats: RSS 2.0, Atom, JSON Feed
- Full-text extraction: feeds that provide only excerpts are fetched and parsed into clean article text (readability-style extraction)
- OPML import for bulk feed setup

**Out of scope for MVP:**
- Non-RSS sources (social platforms, newsletters, arbitrary websites)
- Feed discovery and recommendations

---

### 5.2 AI-Powered Summarization

**Priority:** P0 (Core)

- Each article gets an AI-generated summary of configurable length
- The summary appears alongside the article preview; the full article is readable in-app (reader view), with a link to the original source
- Multiple summary styles: bullet points, paragraph, key takeaways
- **LLM backend:** user-provided API key (OpenAI/Anthropic) by default, local models via Ollama as the free fallback
- Configurable model selection per user
- Summarization is optional — the reader is fully functional without an LLM configured

**Technical notes:**
- Summaries are cached per article to avoid re-processing
- Per-summary cost tracking, visible to the user
- Fallback to the article excerpt if summarization fails or no LLM is configured

---

### 5.3 Article Q&A (LLM Agent)

**Priority:** P1 (High — enables deep engagement, but not the wedge)

- Users can ask questions about any article in a chat interface
- The agent's context is the full article text stored in the database
- Conversations are saved per article for later reference
- Follow-up questions are supported in a threaded conversation
- Uses the same LLM backend as summarization

**Example flow:**
1. User reads a summary of an article about AI regulation
2. User asks: *"What are the main concerns raised in this article?"*
3. The agent responds with insights drawn from the article
4. User follows up: *"How does this compare to the EU AI Act?"*

---

### 5.4 Social Sharing — The Core Feature

**Priority:** P0 (Core — the differentiator)

**@Mentions:**
- @mention any user on any article, with multiple recipients per share
- Mentioned users see the article in their "Shared With Me" feed
- Mentions trigger a notification (web in Phase 1, push once the mobile app ships)
- Users are discoverable by username search; email invites bring new users onto an instance

**Notes / commentary:**
- Every share includes an optional note — your take, the context, the "why I'm sharing this"
- The note appears prominently when the recipient opens the shared article
- Notes are stored permanently with the share

**Shared feeds:**
- **Shared With Me** — articles others have shared with you
- **I Shared** — articles you've shared with others
- Filter shared articles by person, date, or tag

**Collections:**
- Group multiple articles into a shareable collection
- Share the whole collection with a single @mention
- Example: *"Here are 5 articles about the AI regulation debate"*

**Access model (MVP):**
- Private shares (specific users only) — the default
- Public shares (anyone with the link) — opt-in

**Abuse controls (MVP baseline):**
- Users can block other users; blocked users cannot @mention them
- Per-user mention rate limits to prevent spam

**Out of scope for MVP:**
- Team workspaces, roles, SSO
- Group chats / discussion threads on articles

---

### 5.5 Tagging

**Priority:** P1 (High)

- Users can add custom tags to any article
- Predefined starter tags (e.g., "AI", "Politics", "To Read", "Important")
- Search and filter articles by tag
- Color-coded tags for visual scanning
- Tags can be used to organize collections

---

### 5.6 Read State & Continuity

**Priority:** P0 (Core)

- **Read/unread tracking** per user (including for articles received via shares)
- **Continue where you left off:** unread articles surface first on return
- **Save for later:** bookmark articles to read later, separate from unread state
- **Reading progress:** scroll position is tracked within long articles
- **Cross-device sync:** read state syncs across web and mobile (account-based)
- **Smart inbox:** unread counts, with filters by date, source, and tag

---

### 5.7 Notifications

**Priority:** P0 (Core — closes the social loop)

- **Phase 1 (web):** in-app notifications and email for @mentions, so the social loop works before the mobile app exists
- **Phase 3 (mobile):** native push notifications via Expo for:
  - **@mentions** — "Sarah shared an article with you"
  - **New articles** matching user preferences
- Configurable notification rules:
  - By feed/source
  - By keyword/tag
  - By time of day (quiet hours)
- Notifications contain the article title and a short summary
- Tapping a push notification opens the article in the native app

---

### 5.8 Learning Experiences (Plugin Architecture) — Post-MVP

**Priority:** P2

- Plugin-based architecture for learning integrations
- **First plugin:** Google NotebookLM
- Select multiple articles → create a "learning session"
- Generate:
  - Podcast-style discussions between AI hosts about the articles
  - Study guides and flashcards
  - Comparative analysis across articles
- The abstract interface allows adding further plugins (e.g., a custom podcast generator)
- Learning sessions can be exported as shareable content

---

## 6. User Personas

### 6.1 The Developer Reader (Primary — First Audience)
| Attribute | Detail |
|-----------|--------|
| **Name** | Alex, 29, Software Engineer |
| **Needs** | Stay on top of tech news, share interesting finds with colleagues |
| **Behavior** | Reads 10+ tech feeds daily, shares 3–5 articles/week with the team |
| **Value prop** | Summaries save time; @mentions with notes make sharing curated |
| **Self-hosting** | Will self-host, contribute code, report bugs |

### 6.2 The Team Curator (Secondary)
| Attribute | Detail |
|-----------|--------|
| **Name** | Maya, 35, Engineering Manager |
| **Needs** | Distribute relevant content to her team with context |
| **Behavior** | Scans feeds, picks what matters, shares with notes attached |
| **Value prop** | "Here's why this matters" attached to every share |

### 6.3 The Learning Enthusiast (Tertiary)
| Attribute | Detail |
|-----------|--------|
| **Name** | David, 28, Data Scientist |
| **Needs** | Deep-dive into AI/ML news, create study materials from articles |
| **Behavior** | Reads extensively, takes notes, wants to turn articles into podcasts |
| **Value prop** | Q&A agent + learning plugins = studying from the news |

---

## 7. Technical Architecture

### 7.1 Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| **Web Frontend** | React / Next.js | SSR, strong ecosystem |
| **Mobile App** | React Native (Expo) | Native iOS/Android, shares logic with web via the API |
| **Backend API** | Python / FastAPI | Best LLM ecosystem, async, fast development |
| **Background Jobs** | Worker + scheduler (e.g., ARQ or Celery on Redis) | Feed polling, summarization, notification fan-out |
| **Database** | PostgreSQL | Relational data, full-text search |
| **Cache / Queues** | Redis | Caching, job queues, rate limiting |
| **RSS Parsing** | feedparser (Python) + JSON Feed support | Mature, handles edge cases |
| **Content Extraction** | trafilatura / readability | Full text from feeds that only provide excerpts |
| **LLM Integration** | OpenAI/Anthropic API + Ollama (local) | User API key by default, local fallback |
| **Auth** | Auth.js (web) + backend-issued JWT (shared by web and mobile) | Single token model across clients; social login + email |
| **Push Notifications** | Expo Push Notifications | Native push for iOS/Android |
| **Email** | Transactional email provider (e.g., Resend/Postmark) | @mention notifications before the mobile app ships |
| **Search** | PostgreSQL full-text search | Fast article search without extra infrastructure |
| **Hosting** | Vercel (web) + Railway (API + workers) + Expo (mobile) | Simple deployment for a small team |

### 7.2 Fair Source Strategy

- **Repository:** GitHub (public)
- **License:** FSL-1.1-Apache-2.0 — free to use, modify, self-host, and redistribute; only *competing commercial use* (selling NewsRead or offering it as a competing service) is prohibited; each release converts to Apache 2.0 two years after publication
- **Positioning note:** FSL is *Fair Source / source-available*, not OSI-approved open source. All public copy says "Fair Source" or "source-available" — the developer audience will call out anything else
- **Self-hostable:** anyone can run their own instance, including for internal business use
- **Cloud demo:** a public instance runs from Phase 1 and becomes the paid tier at launch
- **Paid features (cloud-only):** managed AI (no API key required), team workspaces, priority support
- **Contributor-friendly:** clear CONTRIBUTING.md, a CLA covering the FSL grant, issue templates, good first issues

### 7.3 Content Storage Policy

- Full article text is stored per instance to power in-app reading and Q&A
- **Risk posture, not a settled question:** storing and re-displaying full text rests on a fair-use argument that is untested for this use case and weaker outside the US
- Mitigations:
  - Content is never re-published, syndicated, or exposed to non-subscribers
  - A prominent link to the original source is always preserved
  - Publisher opt-out honored on request; `noarchive`/robots signals respected for full-text crawling
  - Paywalled content is never bypassed
- This policy is revisited before the cloud tier takes payment (commercial use weakens the fair-use posture)

---

## 8. Data Model (Initial)

Feeds are global and fetched once; per-user state lives in subscription and article-state tables. This is what makes deduplication, shared read-state, and share-recipient tracking work.

```
User
  id, email, username, name, avatar, created_at

Feed                       -- global, fetched once per feed
  id, url, title, site_url, last_fetched_at, refresh_interval

Subscription               -- a user's membership of a feed
  id, user_id, feed_id, custom_title, created_at

Article                    -- global, deduplicated by canonical URL
  id, feed_id, canonical_url, title, author, published_at,
  content (full text), excerpt, image_url, fetched_at

ArticleSummary             -- cached per article (+ style/model variant)
  id, article_id, style, model, content, cost, created_at

UserArticleState           -- per-user read state, incl. shared articles
  id, user_id, article_id, is_read, is_saved_for_later,
  scroll_position, updated_at

Tag
  id, user_id, name, color

ArticleTag
  article_id, tag_id, user_id

Share                      -- one share, possibly many recipients
  id, from_user_id, article_id (nullable), collection_id (nullable),
  note, is_public, created_at
  -- exactly one of article_id / collection_id is set

ShareRecipient
  id, share_id, to_user_id, seen_at

Block
  id, user_id, blocked_user_id, created_at

Conversation
  id, article_id, user_id, created_at

Message
  id, conversation_id, role (user/assistant), content, created_at

NotificationPreference
  id, user_id, feed_id (nullable = global),
  keywords, quiet_hours_start, quiet_hours_end, enabled

Device                     -- push targets for mobile
  id, user_id, expo_push_token, platform, created_at

Collection
  id, user_id, name, description, created_at

CollectionArticle
  collection_id, article_id, position
```

---

## 9. User Flows

### 9.1 Onboarding
1. User signs up (email or social login)
2. User adds RSS feeds (URL input or OPML import)
3. User optionally connects an LLM (API key or local Ollama) — skippable; the reader works without it
4. The system fetches initial articles (and generates summaries if an LLM is configured)
5. User lands on their personalized feed

### 9.2 Daily Reading
1. User opens the app and sees unread articles with summaries
2. User scans summaries and expands the interesting ones
3. User asks questions about articles via Q&A
4. User tags articles for organization
5. Articles are auto-marked as read when viewed

### 9.3 Sharing Flow (The Core Loop)
1. User finds an interesting article
2. User clicks "Share" and types @username
3. User writes a note: *"This changes how we think about X"*
4. The recipient gets a notification: *"Alex shared an article with you"*
5. The recipient opens it and sees the article with Alex's note displayed prominently
6. The recipient can read, save, or re-share it with their own note

### 9.4 Learning Session (Post-MVP)
1. User selects 3–5 related articles
2. Clicks "Create Learning Session"
3. Chooses a format (podcast, study guide, debate)
4. The plugin generates content from the article collection
5. User can listen, review, or share the session

---

## 10. UI/UX Principles

- **Sharing is one tap** — the share button is always visible; @mentioning is instant
- **Notes are prominent** — when someone shares with you, their note is the first thing you see
- **Speed-first** — summaries visible at a glance, no loading spinners
- **Progressive disclosure** — summary → expand → full article → Q&A
- **Native mobile** — React Native for a real mobile UX, not a web wrapper
- **Dark mode** — dark theme by default, with a light option
- **Keyboard shortcuts** — power-user support (j/k navigation, mark read, share)

### Key Screens (MVP)

| Screen | Description |
|--------|-------------|
| **Feed View** | Article list with summaries and read/unread indicators |
| **Shared With Me** | Articles others shared with you, with their notes |
| **Article View** | Full article with summary, Q&A panel, and share button |
| **Share Modal** | @mention picker and note composer |
| **Q&A Chat** | Conversational interface for article discussion |
| **Saved / Bookmarks** | Saved-for-later and tagged articles |
| **Feed Management** | Add, remove, and configure RSS feeds |
| **Settings** | LLM configuration, notification preferences, account |

---

## 11. Competitive Landscape

| Product | Strengths | Weaknesses | NewsRead Differentiation |
|---------|-----------|------------|-------------------------|
| **Feedly** | Mature RSS reader, AI features | Expensive; sharing is just links | @mentions with notes = contextual sharing |
| **Inoreader** | Powerful rules and filters | Complex UI, dated sharing | Modern UX, social-first sharing |
| **Readwise Reader** | Excellent capture and highlighting | Expensive, closed source | Source-available, affordable, social sharing |
| **Google NotebookLM** | Great for research and podcasts | Not a news reader | Combines reading + social + learning |
| **Slack / Teams** | Where articles are actually shared today | Raw URLs, context lost in the scroll | Articles with summaries, notes, and notifications |

**Key insight:** the real competitor is not another RSS reader — it's the Slack channel where people paste links today. NewsRead replaces that pattern with rich, contextual sharing.

---

## 12. Monetization (Cloud-Only, Post-MVP)

### License Model

| Aspect | Self-Hosted (FSL) | Cloud (Paid) |
|--------|----------------------|--------------|
| **Core reader** | ✅ Full | ✅ Full |
| **AI summarization** | ✅ Own API key or Ollama | ✅ Included, no key needed (Pro) |
| **Article Q&A** | ✅ Own API key or Ollama | ✅ Included, no key needed (Pro) |
| **Social sharing** | ✅ Full | ✅ Full |
| **Team workspaces** | ❌ | ✅ (Team tier) |
| **SSO / admin controls** | ❌ | ✅ (Team tier) |
| **Priority support** | ❌ | ✅ (Pro+) |

### Pricing Tiers (Future)

| Tier | Price | Features |
|------|-------|----------|
| **Free (Cloud)** | $0 | Limited feeds, basic sharing, community support |
| **Pro** | TBD | Unlimited feeds, managed AI included, priority notifications |
| **Team** | TBD | Workspace management, admin controls, SSO |

### Revenue Streams
- Cloud hosting subscriptions
- Team/enterprise plans
- Premium integrations (advanced learning plugins)

---

## 13. Development Phases

### Phase 1: Core Reader + Sharing (Weeks 1–6)
- [ ] Repository setup with FSL-1.1-Apache-2.0 license and CI
- [ ] User authentication (Auth.js + backend JWT)
- [ ] RSS ingestion worker: fetching, parsing, full-text extraction
- [ ] Article storage and canonical-URL deduplication
- [ ] Basic UI: feed view, article view (in-app reader)
- [ ] Per-user read/unread tracking
- [ ] **Social sharing: @mentions, notes, "Shared With Me" feed**
- [ ] Username search + email invites (user discovery)
- [ ] In-app + email notifications for @mentions
- [ ] Minimal demo cloud instance deployed (locked decision #1)

### Phase 2: AI Features (Weeks 7–10)
- [ ] AI summarization integration
- [ ] LLM configuration (user API keys + local Ollama)
- [ ] Article Q&A chat interface
- [ ] Summary caching and cost tracking

### Phase 3: Mobile App (Weeks 11–16)
- [ ] React Native (Expo) project setup
- [ ] Native feed view + article view
- [ ] Push notifications (@mentions + new articles)
- [ ] Cross-device read-state sync
- [ ] Mobile share flow

### Phase 4: Polish + Collections (Weeks 17–20)
- [ ] Article collections (group + share)
- [ ] Tagging system
- [ ] Notification preferences (rules, quiet hours)
- [ ] Search and filtering
- [ ] Public launch of the demo cloud instance

### Phase 5: Learning Integration (Weeks 21–24)
- [ ] Plugin architecture
- [ ] NotebookLM integration (first plugin)
- [ ] Learning session creation
- [ ] Shareable learning content

---

## 14. Success Metrics (Post-Launch)

| Metric | Target | Rationale |
|--------|--------|-----------|
| **Articles shared per active sharer/week** | ≥ 3 | Matches primary-persona behavior (Alex shares 3–5/week) |
| **Share open rate** | > 50% | Recipients actually open what's shared with them |
| **Share response rate** (recipient reads, saves, or re-shares) | > 30% | The social loop is generating action, not just deliveries |
| **DAU/MAU ratio** | > 0.4 | Daily engagement |
| **Articles read per session** | > 5 | Reading engagement |
| **Time to first share** | < 1 day from signup | Low-friction sharing once a user has someone to share with |
| **Day-7 retention** | > 40% | Stickiness |

---

## 15. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM API costs | High | User-provided keys, local model fallback (Ollama), per-user cost visibility |
| Network-effect cold start | High | Sharing requires recipients; seed with self-hosted teams (colleagues share with colleagues), email invites, public share links |
| React Native doubles frontend effort | High | All business logic in the API; Expo for faster iteration; mobile deferred to Phase 3 |
| Content copyright | Medium–High | Per-instance storage, source links preserved, publisher opt-out, no paywall bypass; policy re-reviewed before monetization (§7.3) |
| Spam / abuse via @mentions | Medium | Blocks, mention rate limits, public-share reporting |
| RSS feeds breaking or excerpt-only | Medium | Robust parser with fallbacks; readability-based full-text extraction; excerpt fallback |
| Privacy expectations for shares/notes | Medium | Private-by-default shares, clear data deletion, GDPR-aware design from day 1 |
| NotebookLM API changes | Low (P2 feature) | Abstract plugin interface, custom fallback generator |

---

## 16. Appendix

### A. Glossary

| Term | Definition |
|------|-----------|
| **RSS** | Really Simple Syndication — XML format for publishing frequently updated content |
| **OPML** | Outline Processor Markup Language — format for exchanging feed subscription lists |
| **LLM** | Large Language Model — AI model for text generation (GPT, Claude, etc.) |
| **NotebookLM** | Google's AI tool that creates insights and podcasts from uploaded documents |
| **FSL** | Functional Source License — Fair Source license permitting use, modification, and self-hosting while prohibiting competing commercial use; each release converts to Apache 2.0 after two years |
| **Expo** | React Native framework that simplifies mobile development and deployment |

### B. Inspiration & References

- Feedly (https://feedly.com) — RSS reader with AI features
- Readwise Reader (https://readwise.io/read) — modern reader with highlighting
- Google NotebookLM (https://notebooklm.google/) — AI-powered research assistant
- Inoreader (https://www.inoreader.com) — powerful RSS reader with rules
- Slack / Teams — where articles are shared today (raw links); the real competitor

---

*This PRD is a living document. Update it as the product evolves.*
