# Canvas Replacement Stage 2B — node-interaction correction

Date: 2026-09-30

Branch: `codex/excalidraw-infinite-canvas`; source parent: `709566d`.

## Outcome and acceptance boundary

The original Stage 2 UI was rejected by the user. This checkpoint corrects the
isolated node surface rather than continuing to treat the raw foundation UI as
the visual master. Engineering delta checks passed; product presentation is
**pending user review**. It is not a complete Canvas replacement.

No PA business schema, Provider execution, formal release or desktop shortcut
was changed. No restricted upstream source, CSS or assets were copied.

## Actual reference mechanisms and reuse

Behavioral reference is `hero8152/Infinite-Canvas` at
`67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e`, specifically its smart-canvas surface,
not its separate workflow Canvas. The upstream custom license prevents importing
modified code into PA's commercial package without authorization. Its source was
inspected read-only; implementation uses public, permissively licensed primitives.

| Inspected upstream mechanism | PA implementation / actual reuse |
| --- | --- |
| `syncSelectionUi`, image-node selection/port chrome | `canvas-nodes.jsx`: React Flow `Handle` and `NodeResizeControl`; source pixels/aspect unchanged, single quiet border and separate resize control |
| Context toolbar next to active node(s) | `SelectionTools` in `prototype.jsx`: public `NodeToolbar`, `useViewport`, `getNodesBounds`; toolbar does not scale with artwork |
| `renderConnections`: cubic curve, wide hit region, selected disconnect | `reference-edge.jsx`: React Flow `getBezierPath`, `BaseEdge`, `EdgeLabelRenderer`; 22px hit region, selected-only disconnect |
| `handlePortDrop`: drop into blank space creates connected image node | Public `onConnectEnd` + `screenToFlowPosition`; graph validation in `reference-model.js`; no execution implied |
| Editable/reference edge endpoints | Public `onReconnect` / `reconnectEdge`, same validation; pointer reconnect not yet exercised |
| Hover/selected actions and ordinary desktop menus | Scoped context menus, native buttons, arrow/Home/End navigation, Escape and focus return; Lucide SVG icons rather than text glyphs |

This is actual reuse of React Flow primitives, not a claimed transplant of the
restricted target source. Composer positioning/draft and premium model controls
were inspected but are not implemented at this checkpoint.

## Changes

- Removed raw purple all-edge resize chrome, opaque multi-selection fill, fixed
  top-center selection toolbar, angular reference edges and large marketing card.
- Kept image data untouched: no rounding, cover crop, restyle or content overlay.
- Added distinct input/output ports; invalid self/duplicate/missing/group/cyclic
  connections are rejected. Reference links remain explicit user actions.
- Added empty image node import using the existing image reader. Filling a node
  retains its ID, position and grouping; no second document store was created.
- Added context menus and quiet consistent software controls. The isolated
  prototype still uses native Windows window decorations.
- Preserved the established model, image I/O, history and isolated restoration
  foundations; existing formal Canvas/Fabric/Task chains were not modified.

## Automated/build Gate

- Targeted tests: **7/7 passed** (5 foundation cases plus connection validation
  and connected-empty-node serialization/no-execution cases).
- Production frontend build: passed; normal and compact native packages: passed.
- Both packages use the same frontend assets:
  - `index-xgXR_QMN.css`: SHA-256 `853F14DB6A7F2979B1C394D4A4A76E0D58D74957FE973B1EEC7E9D8EE5557365`
  - `index-BSIvT_yG.js`: SHA-256 `A64BA3FDA8AC23D2CB6BD101554E0C856E5284E2A400C1C702C6169215A518BC`
- No redundant foundation-wide test run was added after docs-only changes.

## Native packaged evidence

Native Computer Use exercised the actual Windows executables, not just a Vite
preview. The original isolated `511.png` scene was reused. Temporary test nodes
were undone; both original image nodes and their edge remain available.

Observed passing:

1. Real pointer single selection and node-adjacent toolbar; Ctrl+A multi-selection
   toolbar without an opaque blue fill over the artwork.
2. Curve selection, selected-only disconnect, then Ctrl+Z restoration.
3. Output-port drag to blank space creates a connected empty image node; Undo
   restores the prior scene.
4. Empty-node file picker imports the existing `511.png`; source aspect remains
   unchanged. Undo first restores the empty node, then removes the test node.
5. Object right-click menu, arrow-key navigation, visible focus and Escape.
6. Actual compact **760x560 client** window: fit view, multi-selection toolbar,
   accessible controls, lower-right menu clamped to the window and Escape.
7. Close compact package, start normal **1360x860 client** package using the same
   isolated root: original two images and reference curve restore. Fit view and
   single selection remain operable after restart.

Compact testing uses a startup-window-size-only Tauri config with the same
frontend. Native border-resize injection was unreliable, so it is not claimed as
a verified resize interaction. Only the current system DPI was observed here;
100% and 125% DPI remain separate release checks.

Evidence directory: `build/canvas-replacement-stage2b-20260930/screenshots/`.

- `normal-review.png`: final normal-window selection/curve review.
- `single-selection.png`, `multi-selection.png`: selection states.
- `reference-connection.png`, `port-to-space.png`: edge and blank-drop states.
- `compact-multi-selection.png`, `compact-context-menu.png`: compact bounds.
- `compact-empty-node-import.png`: node-local import.
- `restart-restored.png`: restoration before refitting the viewport.

## Artifact identity and formal isolation

- Normal package:
  `build/canvas-replacement-stage2b-20260930/Product Atelier Canvas Node Prototype.exe`
  - SHA-256 `7E01458D5341B3B2DBE063947E0F60DC1ACB79A65FB2F8AFD85B9FDE451EE5D4`
- Compact package:
  `build/canvas-replacement-stage2b-20260930/Product Atelier Canvas Node Prototype Compact.exe`
  - SHA-256 `53BF3198D44E23ACE9DF3EB4EFCE94F37D5773E8E03180C269266B281036F643`
- Test data/WebView root:
  `C:/Users/64414/AppData/Local/Temp/ProductAtelier-app-test-cbd4df6a283e4986aa77a6764d23fe37`.
  Candidate isolation, separate data/WebView, nonexistent legacy-config/vault
  paths were applied on both launches. The frontend does not call the PA sidecar.
- Formal EXE before/after SHA-256 remains
  `A53FE608001FC785D6715799CB1AA19F519904374F6C6BDA7C9FD37AE78E6E95`.
- Desktop `Product Atelier.lnk` still targets
  `D:/ProductAtelier-Desktop/release/ProductAtelier-Portable/Product Atelier.exe`.
- Provider calls: **0**. Merge: **no**. Promote: **no**.

## Remaining boundaries

User product review is required. Public edge reconnect has model validation but
not native pointer evidence. Large/offscreen selection-toolbar collision,
100%/125% DPI, full accessibility and performance remain uncovered. Composer,
premium model picker and PA business adapters remain subsequent phases; this
prototype cannot yet generate images, edit via Fabric or operate real PA Tasks.

Do not call the replacement complete or improve these boundaries by silently
expanding this checkpoint. The normal package is left open for user review.
