# Product Atelier Canvas Replacement — Foundation Prototype

This is the isolated Stage 2/2B prototype defined by `docs/contracts/canvas-replacement-acceptance-v1.json`.

It uses React Flow as the mature infinite-node-canvas foundation and intentionally does **not** read Product Atelier user data, call the PA sidecar, execute a Provider, or write into the formal portable release.

## Included in Stage 2

- PNG/JPEG/WebP/GIF file import, Explorer drop, and bitmap clipboard paste;
- exact first-file drop position and preserved source aspect ratio;
- pan, zoom, fit view, minimap, box/additive/multi-selection;
- node drag and aspect-preserving image resize;
- explicit reference connections;
- duplicate, Delete, undo/redo, group/ungroup;
- isolated localStorage restart restoration;
- compact-window CSS;
- node-adjacent single/multi-selection toolbars, quiet image chrome and independent ports;
- Bezier reference links with selection/disconnect, public edge reconnect, and port-to-space empty image nodes;
- object/canvas context menus with keyboard navigation and Escape/focus return.

The original `709566d` foundation UI was rejected by the user. Stage 2B corrects
node interactions and presentation; its packaged product-review Gate remains
pending user review. Passing model tests/builds is not complete product acceptance.
See `docs/reports/canvas-replacement-stage-2b-node-interaction-2026-09-30.md` for
actual native packaged evidence and untested boundaries.

## Deliberately deferred

Premium creative controls/model picker, reference Composer/offline creative
workflow, and PA business adapters remain subsequent stages. This checkpoint
does not claim target-product parity or a usable replacement for the formal PA
Canvas. No Provider call is permitted in this prototype.

## Run

```powershell
npm install
npm test
npm run build
npm run dev
```

Open `http://127.0.0.1:4187/`.
