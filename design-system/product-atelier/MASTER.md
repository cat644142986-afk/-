# Product Atelier UX System

Status: UX Lab source of truth
Updated: 2026-09-03

## Product Character

Product Atelier is a Windows creative production workstation. It is not a landing page, a generic image generator, or a gallery of decorative controls. The interface must optimize repeated image work, task recovery, inspection, comparison, and precise local editing.

## Information Architecture

1. 创作: fast, bounded workflows for common production jobs.
2. 无限画布: a full workspace for spatial composition, local edits, outpainting, lineage, and video tasks.
3. 评审: version comparison, decisions, and correction requests.
4. 会话: durable project recovery and history.
5. 成长: governed knowledge, evidence, and review-derived rules.
6. 设置: models, delivery, appearance, accessibility, and optional runtimes.

The left rail is the stable navigation spine. The top mark reports global task presence and opens the task center. Page changes never imply that background work stopped.

## Design Principles

- Work first: keep the current asset, next action, and task consequence visible.
- Durable truth: display persisted task and result state, never simulated progress.
- Progressive disclosure: common controls stay near the work; rare parameters live one level deeper.
- One visual grammar: Uiverse and other references may supply interaction mechanics, never independent styling islands.
- Calm density: compact enough for repeated desktop use, with 44 px minimum primary targets and visible keyboard focus.
- Honest motion: animation explains activity, queueing, completion, or attention. The task-presence lifeform may keep a very slow idle morph and blink; every other idle surface remains still.

## Appearance Axes

`data-theme="light|dark"` controls luminance. `data-colorway="warm|mono"` controls palette character. Classic black and white is intentionally a light-only composition; choosing it resolves the active theme to light.

### Warm

- Warm gray shell and canvas.
- Coral primary accent and yellow navigation contrast.
- Existing product identity and default behavior remain unchanged.

### Mono

- Black navigation and primary actions, white panels, neutral gray canvas.
- Dark surfaces are confined to the left navigation spine; the task dock and editing workspace stay light.
- Success, warning, and failure colors remain available only for operational meaning.
- Panel and control radii tighten modestly to distinguish the workstation treatment from dark mode without making the interface severe.

## Task Presence Contract

Priority across all durable jobs:

`failed > partial/interrupted > running/canceling > paused > queued > recent completed > idle`

| State | Lifeform phrase | Signal | Action |
| --- | --- | --- | --- |
| Failed | exclamation transition, then a sad resting face | red status dot | open task center |
| Needs attention | alert sweep, then a suspicious resting face | amber status dot | open recoverable items |
| Active | three-part thinking rhythm | green status dot | open running jobs |
| Paused | compact sleeping core | amber status dot | resume from task center |
| Queued | restrained three-color orbit | coral status dot | inspect queue |
| Completed | short burst, wink, then happy rest for 45 seconds | green status dot | open result/task |
| Idle | deep-ink organic body with two white capsule eyes; no PA letters, pupils, or inherited logo geometry | low-amplitude drift, blink, and inertial pointer gaze | open task center |

Animation pauses when the document is hidden and when reduced motion is active. Screen-reader announcements remain in the task center's existing live region; the mark updates its accessible name without creating a competing live region.

The default idle face must remain readable inside the 48 px target: both eyes stay visibly separated through the blink and never collapse the whole face into a single slash. State and interaction expressions may vary eye size or tilt deliberately. Personality comes from inertial gaze and expression changes, not pupils; body deformation stays below the threshold where the mark feels unstable or gelatinous.

The task-presence lifeform replaces the previous PA logo instead of wrapping or preserving it. Warm mode has no orange tile behind the body; coral is reserved for the small queued/status signal. The implementation uses the MIT-licensed, framework-free core from `jeremy-prt/bloub` and Product Atelier's own appearance, state mapping, renderer, and accessibility behavior. It does not reproduce x.ai/Grok geometry or brand assets.

Pointer hover and keyboard focus produce curiosity, press produces surprise, and a file drag produces an excited wide pose when no higher-priority durable task state is active. These responses are interrupted immediately by real task-state changes.

## Living Workspace Contract

- The creation page has at most two ambient motion systems: the global task-presence lifeform and the empty-stage readiness motion.
- The empty stage uses editor guides, one slow scanning guide, and one segmented import orbit. It must read as a working image surface, not a decorative hero.
- Mint, coral, and amber provide small operational and focus accents. They never tint an imported product image or replace neutral comparison surfaces.
- Once an image is present, the user's material becomes the primary source of color and visual energy; the shell recedes.
- Reduced motion freezes the lifeform and resolves every repeating stage animation to a stable readable frame.

## Component Rules

- Use Lucide or existing product icons. Do not use emoji as product controls.
- Icon-only controls require an accessible Chinese name and a tooltip/title.
- Do not nest cards. Panels define work regions; cards represent repeated records only.
- Prefer segmented controls for modes, native radios for mutually exclusive settings, and checkboxes/toggles for binary settings.
- Progress animation uses transforms or SVG stroke movement, not layout-changing width animation. Determinate progress may set a stable width once from real data.
- Keep transitions between 150 and 300 ms. Longer continuous motion is reserved for active or queued task presence.
- At heights of 760 px or less, the task control body may scroll while its primary action remains fixed and visible. Hiding lower parameters is never an acceptable compact-layout strategy.

## Acceptance Viewports

- 1440 x 900: primary desktop target.
- 1280 x 720: compact desktop target.
- 960 x 600: minimum supported window.
- No horizontal overflow, clipped button text, hidden primary action, or overlapping task feedback at any target.

## Exclusions

- No framework migration for visual polish.
- No GSAP, Vue, or animation runtime for the status mark.
- No GB-scale model or UI dependency in the main package.
- No direct visual recreation of x.ai or another product identity.
