# FE Productionize Plan

Goal: align the web frontend to a production-ready state — a real design-system component
layer, less duplication/boilerplate, and resilient app-level plumbing — without a rewrite.
Based on a full audit of `frontend/` (July 2026).

## Where we are

The token layer is already solid and should be kept as-is:

- Semantic CSS tokens in `app/globals.css` (`--ink`, `--accent`, `--line`, `--bg-raised`, …)
  with zero raw Tailwind palette classes anywhere in `app/` or `components/`.
- Dark mode works via token redefinition under `prefers-color-scheme` — no per-component
  dark styling.
- CSS primitives with good adoption: `.btn` (146 uses), `.input` (87), `.icon-btn`,
  `.mono-label`, shimmer/spinner animations.
- Clean icon set (`components/icons.tsx`, consistent `{size, className}` API), a good
  Radix-based `Modal`, strong custom hooks (`useReadingWindow`, `useReadingTimer`).

The debt is in three layers above the tokens:

1. **No React component layer** over the CSS classes → compound patterns (modals, buttons,
   badges, avatars, form fields, error text, toggles, tabs, confirms) are copy-pasted with
   size drift and a11y gaps. 4 of 7 modals hand-roll overlays with no focus trap.
2. **Token plumbing**: tokens applied via ~342 inline `style={{}}` attributes because they
   are not exposed as Tailwind utilities; 24 distinct arbitrary `text-[NNpx]` sizes instead
   of a scale; custom classes unlayered (armed Tailwind-precedence trap).
3. **Data-layer boilerplate**: 73 hand-maintained types mirroring 88 backend Pydantic
   schemas; 59 inline `useSWR` calls with duplicated raw key literals; the
   `setBusy/try/catch/finally` mutation dance copy-pasted 30+ times; no toast system; no
   `error.tsx`/`loading.tsx`; no global `SWRConfig`; no 401 → login handling mid-session.

## Decisions (defaults — flag before a phase if you want to change one)

| Decision | Choice | Rationale |
|---|---|---|
| Component library | In-house on top of existing tokens + à-la-carte Radix primitives | shadcn would fight the established editorial aesthetic and `.btn` conventions |
| Variant styling | `cn()` helper (`clsx` + `tailwind-merge`); add `cva` only if variants get hairy | Minimal deps, matches current style |
| Backend types | `openapi-typescript` types only, keep the existing `api()` wrapper | Low churn; revisit `openapi-fetch` later if path-level checking is wanted |
| Toasts | `sonner` | Solved problem; a day saved vs in-house |
| Dark mode | Stay on `prefers-color-scheme` (no manual toggle) | No toggle requirement today; revisit before Phase 6 if that changes |
| Mobile token unification | **Out of scope** — filed as follow-up | Palettes intentionally(?) diverge; needs a design decision first |

## Phases

Each phase is one PR, in dependency order. Merge gate per PR: FE unit tests + coverage gate
(90% branch) green, plus a visual pass on the running app for anything that touches styling.

> Caveat for all phases: `frontend/AGENTS.md` warns this repo runs a modified Next.js build —
> read `node_modules/next/dist/docs/` before relying on standard conventions (matters most
> for Phase 1's `error.tsx`/`loading.tsx`).

### Phase 1 — Foundations (S)

Unblocks everything else; no visual changes intended.

- [ ] Add an `@theme` block to `globals.css` mapping existing vars to Tailwind v4 theme keys
      so `text-ink`, `text-ink-faint`, `bg-raised`, `border-line`, `bg-accent-soft`, etc.
      become utilities. Purely additive; existing `var()` references keep working.
- [ ] Wrap the custom component classes (`.btn`, `.icon-btn`, `.input`, `.dot-unread`,
      `.typing-dot`, …) in `@layer components` so Tailwind display/spacing utilities always
      win. Visual pass afterward — no live collisions exist today, but verify.
- [ ] Add `lib/cn.ts` (`clsx` + `tailwind-merge`).
- [ ] Add root `app/error.tsx`, `app/not-found.tsx`, and `app/(app)/loading.tsx`
      (check modified-Next docs first).
- [ ] Add a global `SWRConfig` provider in the root layout: default `fetcher`, and an
      `onError` that on `ApiError.status === 401` logs out and redirects to `/login`.
      Audit call sites for expected 401s first so optional resources don't force logout.

### Phase 2 — Core primitives (M)

New `components/ui/` directory. Build each primitive on the existing CSS classes/tokens,
then do a mechanical adoption sweep.

- [ ] `Button` — `variant: primary | secondary | ghost | danger`, `size`, `loading`,
      optional icon. Add `.btn-danger` to globals.css (destructive actions currently look
      like normal buttons). Folds in the `{busy ? "…" : "Label"}` pattern and the inline
      `min-h-11` touch-target overrides.
- [ ] `Badge` (static) + `Chip` (toggleable, `active`/`onClick`) — replaces ~15 hand-rolled
      pill sites with 10/10.5/11/11.5/12px drift.
- [ ] `Avatar` — initials + size `sm|md|lg`, deterministic bg color; replaces 6 copies.
- [ ] `ErrorText` — always sets `role="alert"`, fixed size; replaces ~26 danger `<p>`s
      (half currently miss `role="alert"` — this closes an a11y gap).
- [ ] `Field` — label (`mono-label`) + `.input` + `ErrorText` scaffolding used by
      login/register/projects/AISettings/ProjectPinCard.
- [ ] Promote the `Toggle` trapped in `FeedSettingsModal.tsx` to `components/ui/Toggle.tsx`;
      adopt in `AISettingsSection` and `SubscribeQuickSettings` (replacing raw checkboxes).

### Phase 3 — Modal consolidation (M)

- [ ] Migrate the 4 hand-rolled modals onto the shared Radix `Modal`:
      `NotInterestedModal`, `SmartFeedModal`, `CatalogFeedModal`, `FeedSettingsModal`.
      Deletes 4 duplicated overlay shells + 4 identical Escape-key effects, and gains focus
      trap + focus restoration for free.
- [ ] Add `ModalHeader` (mono-label eyebrow + serif title + `icon-btn` close with
      consistent `aria-label="Close"`).
- [ ] Leave the `StoriesView` lightbox as a special case.
- [ ] Standardize destructive confirms: a `ConfirmButton`/`useConfirm` primitive replacing
      the 3 bespoke confirm-in-place implementations and the one `window.confirm`
      (`settings/page.tsx`).

### Phase 4 — Generated backend types (M)

- [ ] Generate types from FastAPI's `/openapi.json` via `openapi-typescript` (checked-in
      output + an npm script; CI check that regeneration is clean).
- [ ] Replace the 73 hand-maintained types in `lib/api.ts` with generated ones (aliases
      where FE names diverge: `Article`, `Feed`, `ProjectArticle`, …). Kills the
      "keep in sync" comments (e.g. `PROJECT_STATUSES`).
- [ ] Consolidate the 4 fetch wrappers (`api`, `apiWithHeaders`, `sendReadBatch`,
      `streamSSE`) so auth-header injection and the `data?.detail` error unwrap exist once.

### Phase 5 — Query/mutation layer (M–L)

- [ ] `lib/keys.ts` — central SWR key registry, generalizing the existing
      `articlesKey()`/`mutateArticleLists()` pattern to feeds/projects/AI/shares.
- [ ] Resource hooks (`useFeeds`, `useProject(id)`, `useAiStatus`, …) replacing the 59
      inline `useSWR` calls; related-mutate helpers replacing hand-paired `mutate()` calls.
- [ ] `useMutation`-style helper collapsing the `setBusy/setError/try/catch/finally`
      boilerplate (32 copies of the `err instanceof Error` fallback, ~25 error useStates).
      Use SWR `optimisticData`/`rollbackOnError` where we currently optimistic-write with
      no rollback (`ViewSwitcher`, catalog).
- [ ] `useDebouncedSearch` hook replacing 4 copy-pasted debounce effects.
- [ ] Toast system (`sonner`): route mutation errors/success through it; eliminate silent
      `.catch(() => {})` swallows; keep inline `ErrorText` for form validation.
- [ ] Shared `EmptyState` + `Skeleton` promoted out of `ArticleList`; adopt on
      activity/entity/projects/shares/usage pages.
- [ ] Test cleanup ride-along: where files are touched, mock hooks/client instead of
      `vi.stubGlobal("fetch")` (25 files) + local `okFetch` helpers (18 files).

### Phase 6 — Type scale (M, visually risky — do last)

- [ ] Define ~6 semantic text steps as `@theme` font-size tokens
      (caption ≈10.5, label ≈11.5, body ≈12.5, body-lg ≈13.5, title ≈17, display ≈22+ —
      exact buckets decided by screenshotting the dense clusters).
- [ ] Sweep the 24 arbitrary `text-[NNpx]` values into the buckets. Half-pixel neighbors
      (12/12.5, 13/13.5, 11/11.5) merge — small intentional visual shifts expected.
- [ ] Verify with before/after screenshots of inbox, article, settings, catalog, projects.

### Also in scope, opportunistic (any phase)

- Segmented control: one `SegmentedControl` for the pill-style switchers
  (`page.tsx` unread/all, `ViewSwitcher`), proper `role="tab"`/`aria-selected`.
- Icon dedup (`MenuIcon` == `ListIcon`).
- Migrate inline `style={{color: "var(--…)"}}` to the new token utilities in files already
  being touched (no dedicated big-bang sweep — too churny).

## Out of scope (follow-ups)

- **Mobile token unification** — `mobile/src/lib/theme.ts` duplicates the palette with
  different hex values (web accent `#2c49e0` vs mobile `#0b62d6`) and ~15 stray hex
  literals in screens. Needs a unify-vs-intentionally-different design decision first.
- Server components / RSC data fetching — the app is 100% client-rendered by design
  (auth'd, SWR-driven); revisit only if SEO/first-paint becomes a goal.
- Tooltip/dropdown primitives — add Radix Tooltip/DropdownMenu when a feature needs them.
- Full inline-style → utility migration sweep (opportunistic only, see above).

## Risks

- `@layer components` wrap changes precedence for any element currently relying on a custom
  class beating a utility — audit found no live collisions, but the visual pass in Phase 1
  is mandatory.
- Global 401 handling can log users out on expected 401s — audit before enabling.
- Phase 6 intentionally shifts pixel sizes; done last, screenshot-verified.
- Modified Next.js build (`frontend/AGENTS.md`) — verify app-router conventions against the
  bundled docs before Phase 1.
