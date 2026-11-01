# Product Atelier Canvas Replacement — Foundation Prototype

New-computer setup, exact checkpoint status, portable evidence and remaining work:
[START_HERE.md](../../START_HERE.md). The complete replacement Gate remains OPEN.

## Current implementation supersedes the historical Stage 2 notes below

Implementation follows `docs/canvas-smart-workbench-implementation-plan-v2-2026-09-30.md`.
This is NOT a formally accepted replacement. The historical localStorage-only
and no-sidecar descriptions below apply to aafb778, not the current working tree.

React Flow supplies public node primitives, Retake/BeatDesign reference/composer
behavior, React Aria focus-safe controls, Fabric pixel editing, and fflate ZIP
transfer. PA's existing Asset/Canvas ledger/Governor/Command/Task/Result/lineage
and admission remain authoritative. Database schema remains 10.

NodeScene v2 persists metadata in the existing Canvas ledger. localStorage holds
only a small pointer; old recovery data is not deleted. Run only against separate
temporary app-test data and WebView directories, never formal user data.

Current implementation: original image/video/audio import, exact input links,
Prompt nodes and persistent per-node drafts; node-adjacent Governor; local
crop/mask/brush/text/transparent padding/grid with exact lineage; independent
multi-Task projections; personal Prompt template CRUD in Canvas metadata; bounded
JSON/ZIP workflow transfer. ZIP import registers sources, not forged historical
Tasks, and import never executes a graph.

Live image admission remains single reference / 1:1 / 2K / one attempt for the
existing three models. Video execution is the existing **offline fixture**
contract, not AI synthesis of the supplied image. From-zero execution, LLM,
bounded loops, clip timeline, richer compare/join and full multimedia/performance
journey still require implementation/validation. The complete Gate remains open.

No paid Provider calls, Promote, formal package or shortcut changes are authorized
in this implementation run. Native candidate packaging uses
`tauri.canvas-foundation.conf.json` and `--features custom-protocol`.

## Historical Stage 2 record (not current claims)

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
