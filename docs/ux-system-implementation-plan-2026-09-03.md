# Product Atelier UX System Implementation Plan

Date: 2026-09-03
Branch: `codex/excalidraw-infinite-canvas`
Worktree: `D:\ProductAtelier-Desktop`
Integrated HEAD: `dc53c3b75b42e0b76f28c5e46f2da25d0fa7cf46`
Status: Milestones 1–3 integrated and packaged as an isolated candidate; final Tauri acceptance remains open

## Isolation Boundary

The protected formal portable version remains untouched in `D:\ProductAtelier-Desktop`. UX integration uses a separate Git worktree, separate runtime data, and separate ports for manual testing. Candidate builds are never promoted or wired to the formal desktop shortcut until all gates pass and the user explicitly authorizes promotion.

## Milestone 1: System Foundation

- Freeze the Product Atelier UX source of truth.
- Add the independent `warm|mono` colorway axis.
- Add the real task-presence model and accessible SVG mark.
- Preserve existing task, API, schema, and release contracts.
- Add focused automated tests.

Exit gate: frontend test suite and production build pass; warm and mono views render without overlap at 1440 x 900, 1280 x 720, and 960 x 600.

### Milestone 1 Verification

- `npm test`: 231 passed, 0 failed.
- `npm run build`: passed in 9m 29s. Existing Radix `use client` and large Excalidraw chunk warnings remain non-blocking.
- Browser runtime: isolated backend on `127.0.0.1:8766`, isolated Vite frontend on `127.0.0.1:1422`, and isolated data root under `D:\ProductAtelier-UX-Runtime`.
- Visual matrix: warm/light and mono/light checked at 1440 x 900; mono/light checked at 1280 x 720 and 960 x 600. Mono intentionally resolves to light and cannot become a funeral-like full dark composition.
- Mono surface contract: left navigation is `rgb(19, 20, 22)` while the task dock is `rgb(255, 255, 255)`; dark is confined to navigation and primary actions.
- Interaction: the task-presence mark follows the pointer with inertia, changes to a curious expression on hover/focus, opens the real task center, and receives focus again after Escape closes it.
- Motion: the event-driven SVG lifeform, segmented import orbit, and single stage guide all changed during a 900 ms browser sample. Reduced motion held the lifeform's body path exactly stable across the same 900 ms interval and reduces the other repeating animations to one `0.01 ms` final-state iteration.
- Compact layout: 1280 x 720 fits the full task body without clipping after compact spacing. At 960 x 600 the task body has a 394 px viewport over 486 px of content and is intentionally scrollable; the fixed primary action remains inside the drawer.
- Accessibility: comfortable text remains usable at 960 x 600; no page-level horizontal or vertical overflow was found at any acceptance viewport.
- Runtime logs: no frontend warning or error was emitted during the manual pass.
- Expression refinement: replaced the CSS robot face and orange tile with a deep-ink SVG lifeform whose eyes are real cutouts. The startup mark uses the same pupil-free capsule-eye grammar, and regression coverage prevents a return to the old static face, orange body, or decorative CSS loop.

### Task Presence 2.0 Correction

- Vendored only the framework-free animation core from `jeremy-prt/bloub` 0.1.1 at revision `b4bb3c1`: 13 files and 57.5 KB of source including origin and license records. Vue, media export, Rive, Lottie, and model dependencies are not included.
- Mapped durable task truth to distinct motion phrases: queued orbit, active thinking rhythm, paused sleeping core, completion burst/wink/rest, attention alert/suspicious rest, error exclamation/sad rest, and unavailable sleeping core.
- Added direct interaction phrases: inertial pointer gaze, curious hover/focus, surprised press, and excited file-drag response. Durable task states always take priority over decorative interaction.
- Kept the existing 48 px accessible button, task-center action, title, focus return, and single live-announcement source. The lifeform animation stops while the document is hidden or reduced motion is active.
- Restricted warm-mode coral to the queued/status signal. The body remains independent deep ink, the two eyes remain background-colored cutouts, and the startup shell uses the same visual grammar without loading the runtime before first paint.
- Added the exact upstream MIT copyright to both distributable third-party notice files and kept the source revision in `src/vendor/bloub/ORIGIN.md`.

### Reference Comparison

Reference: the supplied Be.Healthy dashboard screenshot.

- Adopted: one uninterrupted black navigation spine; bright white work regions; a neutral gray content surface; small mint, coral, and amber accents; one dominant interaction point per work region.
- Corrected from the rejected pass: the right task dock is no longer dark in mono, the old PA logo is removed, the status mark visibly morphs, and the empty canvas is no longer a flat gray dead zone.
- Intentionally different: the reference obtains most of its color from health charts and a product photograph. Product Atelier does not fake charts, results, or example images in an empty workspace. Editor guides and readiness motion provide life until the user's real product image becomes the primary visual asset.
- Not copied: the reference's medical information architecture, card arrangement, logo, illustration content, and large rounded dashboard styling.

Visual evidence is stored under `artifacts/ux-system-2026-09-03/` for 1440 x 900, 1280 x 720, and the 960 x 600 open-drawer state.

Milestone 1 was committed as `cd914f2` on `codex/ui-experience-system`, then reviewed and merged onto the current infinite-canvas baseline `9c25142` as integration checkpoint `e854117`. The combined checkpoint passed 244 frontend tests, the production build, the lazy-canvas boundary check, and a projected formal-package size gate of 367.27 MiB.

## Milestone 2: Task Center Coherence

- Align the header task pill, left-rail notice, task mark, drawer summary, and job cards to one status vocabulary.
- Replace bare symbols with Lucide icons where an established icon exists.
- Make attention and recovery actions visible without expanding every job.
- Keep one polite live announcement source.

Exit gate: every durable job status maps to one label, tone, icon, next action, and recovery path.

### Milestone 2 Verification

- Added one immutable task-status contract covering queued, running, paused, completed, partial, failed, canceling, canceled, and interrupted states.
- The header task pill, left-rail notice, task lifeform, drawer summary, and task cards now derive presentation from the same contract instead of maintaining separate status rules.
- Replaced bare status punctuation with accessible Lucide icons and retained one polite live-announcement source.
- Each task card exposes a concrete next action and contextual recovery guidance; unavailable task services present an error state without a misleading attention count.
- `npm test`: 248 passed, 0 failed.
- `npm run build`: passed. Existing Excalidraw chunk-size and Radix `use client` warnings remain non-blocking.
- Lazy-canvas bundle boundary and projected formal-package size remain within the 450 MiB gate.
- Real Tauri interaction, DWM/DPI, and minimum-window verification are intentionally deferred until the approved evening acceptance window. The formal portable build has not been modified or promoted.

## Milestone 3: Core Workflow Pass

- Audit creation, canvas, review, sessions, growth, and settings as one journey.
- Remove duplicated instructions and expose the next action at each completion point.
- Standardize empty, loading, offline, conflict, partial, recovered, and error states.
- Validate keyboard navigation and minimum-window behavior.

Exit gate: the user can start, leave, recover, inspect, revise, and export a task without losing context.

### Milestone 3 Workflow Audit

- Creation already exposes import, durable offline recovery, processing state, result review, send-to-canvas, export, and explicit workspace completion.
- Infinite canvas already exposes new-canvas, load retry, conflict-copy recovery, task/result lineage, and a tested return path from Fabric fine edit.
- Result review already exposes a visible return to creation, persisted A/B state, durable feedback, immediate derived-version adjustment, and knowledge-suggestion follow-up.
- Sessions now turn both a genuinely empty ledger and an empty project filter into actionable states: start creation or restore the all-project view.
- Growth now returns directly to the current result when one exists, otherwise it starts creation; it never treats an empty suggestion queue as an error.
- Settings now keeps loading, recovered, offline, and save-error states visible inside the page with exact retry actions instead of relying on a transient toast.
- All added navigation returns to existing durable state; no second workflow store or parallel task identity was introduced.

Source gate: 249 frontend tests, JavaScript syntax checks, production build, and the lazy-canvas boundary pass. The production `dist` is 9,141,995 bytes with a projected formal package size of 367.28 MiB. Real Tauri minimum-window and keyboard verification remain before this milestone can be closed.

Milestones 1–3 were fast-forward integrated into `codex/excalidraw-infinite-canvas` at `dc53c3b`. A clean candidate was rebuilt from that exact pushed identity and passed the packaged sidecar and schema-upgrade gates. These milestones remain release-incomplete until the evening real-window checks pass; the protected formal portable directory and shortcut are unchanged.

## Milestone 4: Debt Reduction

- Obtain explicit approval for exact cleanup targets.
- Remove or archive ignored historical CSS/JS after hashing and inventory.
- Remove generated build failures and compiled artifacts embedded inside old source snapshots.
- Protect `release` and the current formal portable version.
- Split active monoliths only along tested ownership boundaries.

Exit gate: search results and AI context contain active code by default; cleanup is recoverable and does not change the formal release.

## Merge Strategy

Each milestone stays reviewable and can be cherry-picked independently. No UX commit may include IC6 candidate-build changes, release artifacts, database content, API keys, model files, or user data.
