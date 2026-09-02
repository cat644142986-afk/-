# Excalidraw spatial workspace PoC

This isolated prototype validates the spatial layer selected in ADR-0001. It does not call Product Atelier APIs, read the production ledger, modify `src/`, or write to the formal portable directory.

## Scope

- Excalidraw 0.18.1 with exact React 18 peer runtime
- 200 synthetic image proxies with 4096x4096 original metadata references
- 20 `renderEmbeddable` video covers with no autoplay or media download
- five Frames, comparison groups, result branches and lineage arrows
- debounced scene persistence without files, Base64, originals or absolute paths
- content-fingerprint deduplication so transient selection and playback do not create save loops
- locally served Excalidraw fonts during Vite development and production builds

## Commands

```powershell
npm test
npm run build
npm run dev
```

The browser test harness is exposed at `window.__PA_POC__` for deterministic metrics, save, reset and video-selection checks.

The checked-in evidence is `../../docs/excalidraw-spatial-poc-checkpoint-2026-09-02.md`. The isolated tests are 7/7. `npm audit` retains three upstream Mermaid-chain findings (two moderate and one high); they are documented rather than hidden or force-fixed across a major version.
