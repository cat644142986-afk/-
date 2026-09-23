# Canvas Visual Unification checkpoint — 2026-09-23

Status: passed on the `codex/excalidraw-infinite-canvas` prototype branch.

## Scope

- Baseline: `1242f7c2703746e66495d02fe360ac62dfaf237a`
- Product Atelier Light/Dark controls Canvas software UI and the PA-owned Canvas surface tokens.
- Excalidraw remains the document renderer in a stable light rendering mode with a transparent runtime background.
- The stable PA Canvas host establishes `color-scheme: light` so WebView2 does not recolor the composited document when the surrounding app switches to Dark.
- No Creative Workflow, reference, Governor, Task, Result, lineage, Provider, schema, or user-content semantics changed.

## Automated and build gates

- Targeted frontend tests: 20/20 passed.
- `tauri build --no-bundle --features custom-protocol`: passed.
- Packaged candidate SHA-256: `C7F02D729B41A08601F61EF8A5D3BC25E0B130FDDDE83019243D69020713A46A`.

## Packaged delta gate

Receipt:

`build/canvas-visual-unification-packaged-delta/evidence/theme-boundary-0ff6755a0d/theme-boundary-receipt.json`

Screenshots:

- `build/canvas-visual-unification-packaged-delta/evidence/theme-boundary-0ff6755a0d/01-light.png`
- `build/canvas-visual-unification-packaged-delta/evidence/theme-boundary-0ff6755a0d/02-dark.png`

Verified:

- Excalidraw static content Canvas pixel hash stayed identical across Light/Dark: `ede147fb5d57e1ad9cba69da83097d333a01bff2862bcb660156a56f63a5ee6d`.
- The offline Result screen crop was pixel-identical across Light/Dark.
- Text remained `#1e1e1e`; all serialized element style fields were unchanged.
- Canvas surface changed from `#e7e6e3` to `#222528`; the PA grid token changed with it.
- PA toolbar colors, borders, and shadows changed with the app theme.
- Scene revision remained `7`; scene SHA-256 remained `404d401450e97083f4cebac923039b320109ef98a2e5b851a07d5df206eb15c7`.
- Scene versions remained `7`, jobs `1`, task attempts `1`, Provider receipts `0`.

Evidence boundary: the copied fixture's source-asset proxy is unavailable in the isolated data copy and renders its existing placeholder. The durable offline Result and user text are the artwork subjects used for exact theme-boundary verification.

No Provider call was made. No Promote was performed.
