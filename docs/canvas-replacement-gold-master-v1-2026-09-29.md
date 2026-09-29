# Canvas Replacement Gold Master v1

Status: **Stage 1 closed — product/engineering contract only**. No Product Atelier runtime code, package, desktop entry, schema, or Provider state changed in this stage.

## 1. Frozen identities

### Product Atelier

- Source branch: `codex/excalidraw-infinite-canvas`
- Source HEAD at audit: `4a0eb09275395951a3cd862850d128e5b48a3774`
- Formal desktop executable: `D:\ProductAtelier-Desktop\release\ProductAtelier-Portable\Product Atelier.exe`
- Formal sidecar manifest commit: `4a0eb09275395951a3cd862850d128e5b48a3774`
- Contract: `2026-09-23.1`
- Ledger schema: `10`
- Desktop shortcut: `C:\Users\64414\Desktop\Product Atelier.lnk`
- Working tree at audit: clean

The current formal product stays available and unchanged while the replacement is developed in an isolated prototype.

### Interaction reference

- Repository: `hero8152/Infinite-Canvas`
- Audited commit: `67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e`
- Audited surface: `static/smart-canvas.html`, `static/js/smart-canvas.js`, `static/css/smart-canvas.css`
- Role: behavioral and presentation reference, not a source-code dependency
- License consequence: the repository prohibits modified commercial packaging and requires derivatives to remain open source. Its code, CSS, copy, icons, assets, and Provider/storage implementation must not be copied into Product Atelier without a separate commercial authorization.

## 2. Product decision

Product Atelier will replace the current Excalidraw-led Canvas surface with a node-oriented visual creation workbench that follows the reference product's proven creation rhythm:

`material on canvas → explicit reference → composer → model/parameters → governed execution → result beside source → continue from result → Fabric/detail work → return to canvas`

This is not a low-feature imitation. All reference behaviors that materially support PA's image-design workflow are in scope. Provider-specific, video, loop, ComfyUI, RunningHub, and workflow-market capabilities are excluded only because they are not part of the current PA product contract; their omission is not permission to simplify the core image workflow.

The current Excalidraw implementation remains a rollback/fallback path during the prototype and migration period. It is not the visual or interaction mother for the replacement.

## 3. Reuse stack

| Layer | Decision | Why |
| --- | --- | --- |
| Infinite node canvas | `@xyflow/react` 12.12.x, MIT | Provides maintained pan/zoom, box and multi-selection, node dragging, edges, grouping, `NodeToolbar`, `NodeResizer`, `MiniMap`, keyboard deletion, and viewport APIs. This replaces PA's hand-positioned Excalidraw shell overlays. |
| Accessible rich controls | `react-aria-components` 1.21.x, Apache-2.0 | Provides ComboBox/ListBox/Popover/Dialog behavior, keyboard navigation, focus restoration, dismissal, and accessible state without forcing Adobe's visual style. |
| Pixel/detail editing | Existing Fabric 7.4.0 integration | PA already owns the fine-edit contract, ROI/mask model, handoff, results, and recovery semantics. No second pixel editor is introduced. |
| Business execution | Existing PA adapters | Asset, Task, Result, lineage, execution-context, Governor, Catalog, Identity, Overlay, Admission, Router, Provider receipts, persistence, and recovery stay authoritative. |
| Reference product | Behavioral specification only | Its spatial composer, output placement, node actions, input thumbnails, minimap, lazy high-resolution media, and failure surfaces inform the new UI without importing restricted code. |

No new general Agent, Provider, memory, storage, or Task system is allowed.

## 4. Reference behavior map

### Directly supplied by the mature canvas foundation

- quiet full-window canvas, wheel/pinch zoom, space/middle-button pan, fit view, viewport restore;
- click, box, additive and multi-selection;
- node movement, constrained image resize, group/sub-flow movement, copy, paste, duplicate, delete;
- explicit connections, edge selection and deletion;
- stable node-relative toolbar and resize controls that do not depend on DOM-order selectors;
- minimap, background grid, fixed panels, keyboard movement and accessible node focus;
- event callbacks for selection, viewport, node and edge changes.

### Product Atelier adapters

- `ImageNode` projects a durable PA Asset or Result; the image itself is not decorated, cropped, rounded, stretched, recolored, or re-typeset;
- `PromptNode`/Composer owns only transient Creative Draft UI and compiles through the existing Governor;
- explicit reference edges project exact `asset_id` / `result_id` input bindings;
- execution state projects the existing Task/attempt without creating a canvas-only task;
- output nodes project existing Results and retain Task, source Canvas, lineage, context hash, model identity, adapter version, and Provider receipt;
- Fabric opens from the exact current Asset/Result and returns a durable child Result to the originating Canvas position;
- Canvas save serializes presentation and PA identity references through one replacement scene adapter.

### Product Atelier-owned components

- node visual language and image/result/task states;
- Creative Composer and its draft continuity rules;
- model admission/recommendation presentation;
- Governor preview bridge;
- error classification and recovery actions;
- durable scene conversion, restart recovery, legacy import, and rollback;
- title bar/window drag regions and Windows window controls.

## 5. State-by-state surface contract

| State | Primary surface | Required behavior |
| --- | --- | --- |
| Empty Canvas | quiet canvas + compact create control | Import, paste/drop, or generate from zero are discoverable without an onboarding card occupying the work surface. |
| Content, no selection | canvas + viewport controls | No Composer text is destroyed. Create/import/assets remain available but visually secondary. |
| Single Asset | exact image node + node toolbar | Reference, local cutout/white-background when compatible, Fabric, export, and more appear next to the node. No page jump. |
| Single Result | exact Result node + node toolbar | Continue, variant/reference, Fabric, export, and provenance details are available. New lineage starts from this exact Result. |
| Multiple selection | group toolbar | Align/distribute/group/delete/duplicate apply to the selection. AI generation is absent unless the capability contract explicitly supports the whole selection. |
| Composer editing | node-adjacent or viewport-safe composer | Reference tray, Prompt, model and parameters form one surface. Canvas shortcuts do not capture IME/text input. Deselect and object switching preserve the draft. |
| Model selection | accessible searchable popover | Only admitted models are selectable; recommendation, compatibility and evidence are legible; unsupported states explain recovery. |
| Governor preview | modal/sheet over the same Canvas | Shows concise user intent, exact references, hard constraints, design method, model and output parameters. Cancel creates no Task and preserves the draft. |
| Running | source-linked progress state | Shows actual Task state, elapsed time/cancel policy and recovery—not a generic white placeholder node or an unrelated workflow label. |
| Success | Result node placed beside the source | Result is immediately selectable and can branch into another reference/variation while sibling alternatives remain present. |
| Failure/interruption | inline task state + recoverable notification | Cause, paid-attempt status, preserved state, and the valid next action are visible. No automatic paid retry. |
| Fabric round-trip | dedicated editor, exact return target | Exact Asset/Result opens; save/delete/retry work; returning inserts or updates the child Result without losing Canvas/Draft state. |
| Small window | compact controls and bounded overlays | Canvas remains the largest region; toolbar, picker, Composer, model list, parameter popover and Governor remain reachable and do not cover one another irrecoverably. |

## 6. Premium model picker contract

The existing native `<select>` in `src/js/canvas-model-selector.jsx` is explicitly rejected as the replacement UI.

The replacement is an unstyled React Aria ComboBox/ListBox using PA tokens and the existing admission data. It must provide:

- one compact trigger that shows the current model and recommendation state;
- text search and type-ahead when the eligible catalog grows;
- mouse, keyboard, screen-reader, Tab, Escape and focus-return behavior;
- a recommended section and an eligible section without claiming a quality ranking;
- model name plus concise capability facts: current task, reference support, ratio, resolution, evidence status;
- dynamic catalog price and telemetry latency as factual context, never as a one-canary quality score;
- exact selected `provider_model_id` while Provider/channel detail stays in expandable details;
- no silent fallback, model substitution, ratio change, resolution downgrade, or automatic catalog switch;
- disabled or empty states that name the blocking layer: connection, catalog, identity, adapter, capability evidence, or current parameter combination;
- a direct recovery action only when the backend contract supports it, such as read-only catalog refresh;
- switching the model or relevant output parameters invalidates the existing Governor preview;
- list virtualization or equivalent bounded rendering before the eligible set becomes large.

The picker is a reusable PA control, not a raw projection of the reference project's engine `<select>`.

## 7. Draft and execution invariants

- Creative Draft is scoped to Canvas plus exact source node and lives independently of transient selection.
- Ordinary deselect, blank-canvas click, object switching, preview cancel, admission failure, Provider failure, or Fabric entry/return must not clear it.
- Only explicit clear/cancel of the draft or a successfully accepted Task may clear it.
- Returning to the bound source restores Prompt, reference, model and parameters.
- A submitted Task freezes exact reference identities, provider model ID, catalog snapshot/hash, overlay/admission evidence, adapter version, context hash, Governor compilation and paid authorization.
- Result placement and Canvas recovery are downstream of the durable Task; a UI assertion failure cannot erase Provider receipt or Result assets.
- Explicit reference edges are presentation of input binding. Lineage alone does not automatically add permanent arrows to the user's artwork.

## 8. Current code disposition

### Reuse without rebuilding

- `src/js/spatial-canvas-items.js` identity projection and lineage fields;
- `src/js/infinite-canvas-adapter.js` durable Canvas list/open/rename/delete/save conflict semantics, behind a new scene codec;
- `src/js/canvas-model-admission.js` exact admission and no-fallback policy;
- current Catalog/Identity/Overlay/Router/Governor/Task/Result/Provider APIs;
- current Fabric contract and local edit model after the collection-boundary defect is closed;
- current recovery tests for async import, canvas switching, save conflict, Task/Result backfill, draft continuity and exact Result lineage.

### Replace in the new prototype

- `CanvasTransplantShell` as the visual surface;
- Excalidraw selection/viewport geometry projection;
- DOM-order dependent align/distribute projection;
- the raw `<select>` model picker;
- bottom-fixed Composer placement;
- Inspector/Composer/legacy action duplication;
- generic task placeholder elements presented as artwork.

### Keep only as fallback until cutover

- current Excalidraw island and legacy/transplant shell toggle;
- existing Excalidraw scene codec and formal released canvases.

No deletion occurs until a migration/read-back Gate proves that a real existing Canvas can be opened, saved, restarted, and rolled back.

## 9. Audit sources

- React Flow product and built-in capability documentation: <https://reactflow.dev/>
- React Flow component APIs (`NodeToolbar`, `NodeResizer`, `MiniMap`, `Panel`): <https://reactflow.dev/api-reference/components>
- React Flow interaction and performance guidance: <https://reactflow.dev/learn/concepts/adding-interactivity> and <https://reactflow.dev/learn/advanced-use/performance>
- React Flow upstream repository and MIT license: <https://github.com/xyflow/xyflow>
- React Aria Components behavior and composition documentation: <https://react-spectrum.adobe.com/react-aria/getting-started.html>
- React Spectrum/React Aria upstream repository and Apache-2.0 license: <https://github.com/adobe/react-spectrum>
- Reference Canvas source and restrictive license: local read-only audit of `hero8152/Infinite-Canvas` at the frozen commit above.

The package versions recorded in this contract were also checked through the public npm registry on 2026-09-29.

## 10. Stage gates

### Stage 2 — canvas foundation

- parallel prototype only; formal release unchanged;
- import/paste/drop; select/multi-select; move/resize; copy/delete; undo/redo; group; explicit edge; minimap; viewport restore;
- normal/small window and 100/125/150% DPI;
- user images retain original pixels, aspect and rectangular bounds;
- no Provider calls.

### Stage 3 — premium controls

- PA tokens, node toolbar, reference tray/picker, Composer, model picker, parameter popover, task states and Canvas-local notifications;
- screenshot review precedes packaged interaction review;
- keyboard/IME/focus/Escape and overlay boundaries are required, not polish.

### Stage 4 — creative workflow

- Asset → reference → Composer → Governor → offline Result → Result as next reference;
- multiple alternatives coexist;
- draft continuity and exact input/lineage assertions;
- no Provider calls.

### Stage 5 — PA business integration

- existing Task/Result/Provider/Fabric/save/restart systems only;
- close collection-boundary, model-admission, task-placeholder, exact-source and recovery failures;
- no second business store or execution path.

### Stage 6 — packaged product gate

- complete designer journey, failure injection, small-window/DPI, focus/IME, interrupted execution and restart recovery;
- performance evidence for representative multi-node scenes;
- no live Provider call unless the user separately authorizes an exact bounded canary.

### Stage 7 — cutover and release

- only after user visual approval and all offline/packaged gates;
- legacy data compatibility and rollback verified before Build → Test → Package → Smoke → Validated → Promote;
- formal package and desktop shortcut remain unchanged before Promote.

## 11. Non-negotiable rejection criteria

Any stage fails if it introduces one of the following:

- a low-feature canvas presented as complete;
- copied restricted source/assets or unclear third-party provenance;
- user artwork restyled by the software theme;
- an unexplained disabled action or dead end;
- draft loss caused by ordinary navigation or selection;
- raw Provider complexity in the primary Composer;
- hidden model fallback or automatic paid retry;
- a second Task/Result/Provider/persistence system;
- a UI overlay dependent on private DOM ordering;
- a source action that jumps to an unrelated page while its result still belongs to Canvas;
- a release or desktop shortcut change before the full replacement is validated.
