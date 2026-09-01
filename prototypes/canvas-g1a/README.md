# G1A Canvas Prototype

This directory is an isolated Fabric.js feasibility prototype for the Product Atelier G1 canvas gate. It does not modify production `src/`, read the production ledger, execute paid image generation, or write into the formal portable release.

## Scope

- Infinite pan and zoom workspace with one finite 1920 x 1080 export artboard.
- 200 real Fabric proxy objects referencing synthetic 4096 x 4096 originals by metadata only.
- Layer selection, transform, visibility, lock, undo, redo, and restart recovery.
- Preview-only compilation of the existing `command:existing-remove-background` request shape.
- Runtime metrics for object construction, serialization, document size, heap use, and 4K references.

## Run

From this directory:

```powershell
npm test
npm run build
npm run dev -- --port 4178
```

Open `http://127.0.0.1:4178/`. The prototype persists only its synthetic document in browser storage under `product-atelier:g1a-canvas:v1`.

## Acceptance boundary

G1A proves technical feasibility only. Passing it does not authorize direct production integration. G1B must still freeze the production document/version contract, SQLite migration, command registry adapter, proxy lifecycle, and Studio integration before Fabric enters `src/`.

Pinned packages and license texts are recorded in `THIRD_PARTY_NOTICES.md`.
