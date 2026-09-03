# Product Atelier UX System Implementation Plan

Date: 2026-09-03
Branch: `codex/ui-experience-system`
Worktree: `D:\ProductAtelier-UX-Lab`
Base: `cec6748`
Status: Milestone 1 visual correction implemented and verified in the isolated worktree

## Isolation Boundary

The active infinite-canvas work remains in `D:\ProductAtelier-Desktop`. At the start of this milestone it contained uncommitted IC6 test and build-script changes. UX work is isolated in a separate Git worktree, uses separate runtime data and ports for manual testing, and does not build or publish a formal installer.

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

The main infinite-canvas branch advanced from `cec6748` through `ced8a7e` to `03715ff` during this isolated work. The `ced8a7e` range touches `src/js/app.js` and `src/css/stable-ui.css`, while `03715ff` adds release-pipeline hardening. Reconcile the complete range with a reviewed three-way integration and rerun the full gate; do not merge or rebase it into this uncommitted UX worktree implicitly.

## Milestone 2: Task Center Coherence

- Align the header task pill, left-rail notice, task mark, drawer summary, and job cards to one status vocabulary.
- Replace bare symbols with Lucide icons where an established icon exists.
- Make attention and recovery actions visible without expanding every job.
- Keep one polite live announcement source.

Exit gate: every durable job status maps to one label, tone, icon, next action, and recovery path.

## Milestone 3: Core Workflow Pass

- Audit creation, canvas, review, sessions, growth, and settings as one journey.
- Remove duplicated instructions and expose the next action at each completion point.
- Standardize empty, loading, offline, conflict, partial, recovered, and error states.
- Validate keyboard navigation and minimum-window behavior.

Exit gate: the user can start, leave, recover, inspect, revise, and export a task without losing context.

## Milestone 4: Debt Reduction

- Obtain explicit approval for exact cleanup targets.
- Remove or archive ignored historical CSS/JS after hashing and inventory.
- Remove generated build failures and compiled artifacts embedded inside old source snapshots.
- Protect `release` and the current formal portable version.
- Split active monoliths only along tested ownership boundaries.

Exit gate: search results and AI context contain active code by default; cleanup is recoverable and does not change the formal release.

## Merge Strategy

Each milestone stays reviewable and can be cherry-picked independently. No UX commit may include IC6 candidate-build changes, release artifacts, database content, API keys, model files, or user data.
