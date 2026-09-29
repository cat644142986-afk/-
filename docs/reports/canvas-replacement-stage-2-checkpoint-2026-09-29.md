# Canvas Replacement Stage 2 checkpoint

Date: 2026-09-29

Branch: `codex/excalidraw-infinite-canvas`

Foundation: `@xyflow/react` 12.12.0 (MIT)

## Outcome

The mature canvas-core prototype is isolated from Product Atelier production
data and the formal release. It proves the replacement foundation without
reusing restricted source or assets from `hero8152/Infinite-Canvas`.

Implemented and exercised:

- raster paste/import/drop adapters with source aspect-ratio preservation;
- viewport pan/zoom/fit, minimap, selection, additive/select-all and resize;
- duplicate, Delete model path, undo/redo, group/ungroup;
- explicit reference handles and edge persistence;
- compact selection actions projected through React Flow's public `Panel` API;
- isolated local persistence and restart restoration;
- normal and 720x520 compact layouts.

The prototype does not read PA business data, call a Provider, or write the
formal portable release.

## Automated gates

- `npm test`: 5/5 passed.
- `npm run build`: passed (190 modules).
- `npx tauri build --no-bundle --features custom-protocol --config src-tauri/tauri.canvas-foundation.conf.json`: passed.
- `git diff --check`: passed.
- Runtime dependency audit: 0 vulnerabilities on the frozen lockfile before
  the final source-only changes; a later repeat was unavailable because the npm
  audit endpoint failed TLS negotiation.

The model tests cover aspect preservation, connected-selection duplication,
group/ungroup coordinates, deletion of descendants and connected edges, and
restart serialization.

## Interaction evidence

The browser delta exercised bitmap paste, selection, duplication, Ctrl+A
multi-selection, group/ungroup, object movement, undo/redo, 720x520 layout, and
reload restoration. The runtime reported `devicePixelRatio=1.5`.

The final packaged executable launched from a randomized candidate-isolation
root, stayed responsive, loaded `embedded-custom-protocol`, and reported:

- window scale: 1.50;
- physical window: 2062x1346;
- logical window: 1374.7x897.3;
- isolated sidecar ready without touching formal data.

Package artifact:

- `build/canvas-replacement-stage2-20260929/Product Atelier Canvas Foundation Prototype.exe`
- SHA-256: `7AB6B57CF96D90541B851445255519D712B10FC00E075E98B0EF085FFD6177B1`

## Evidence boundary

The available in-app browser's pointer injector did not activate ordinary HTML
buttons, including React Flow's stock Zoom control, while keyboard activation
did. Therefore pointer-button activation is not claimed from that injector.
The executable is left as an isolated packaged checkpoint for direct manual
pointer sanity; this does not change the formal desktop shortcut or release.

Only 150% system DPI was exercised in the packaged process. The layout is not
DPI-conditional, but 100% and 125% packaged screenshots remain a later release
gate rather than being inferred here.
