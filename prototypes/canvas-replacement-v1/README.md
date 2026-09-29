# Product Atelier Canvas Replacement — Foundation Prototype

This is the isolated Stage 2 prototype defined by `docs/contracts/canvas-replacement-acceptance-v1.json`.

It uses React Flow as the mature infinite-node-canvas foundation and intentionally does **not** read Product Atelier user data, call the PA sidecar, execute a Provider, or write into the formal portable release.

## Included in Stage 2

- PNG/JPEG/WebP/GIF file import, Explorer drop, and bitmap clipboard paste;
- exact first-file drop position and preserved source aspect ratio;
- pan, zoom, fit view, minimap, box/additive/multi-selection;
- node drag and aspect-preserving image resize;
- explicit reference connections;
- duplicate, Delete, undo/redo, group/ungroup;
- isolated localStorage restart restoration;
- compact-window CSS.

## Deliberately deferred

Stage 3 owns premium PA controls and visual polish. Stage 4 owns reference Composer and offline creative workflow. Stage 5 owns PA business adapters. No Provider call is permitted in this prototype.

## Run

```powershell
npm install
npm test
npm run build
npm run dev
```

Open `http://127.0.0.1:4187/`.
