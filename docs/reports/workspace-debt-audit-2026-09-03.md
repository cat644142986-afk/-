# Product Atelier Workspace Debt Audit

Date: 2026-09-03
Audit target: `D:\ProductAtelier-Desktop`
Mode: read-only; no files deleted or moved

## Current Footprint

| Area | Files | Lines | Size |
| --- | ---: | ---: | ---: |
| `src/css` total | 86 | 45,415 | 1.56 MiB |
| tracked active CSS | 1 | 1,636 | 0.17 MiB |
| non-tracked historical CSS | 85 | 43,779 | 1.39 MiB |
| `src/js` total | 98 | 42,690 | 1.72 MiB |
| tracked active JS | 31 | 15,706 | 0.63 MiB |
| non-tracked historical JS | 67 | 26,984 | 1.09 MiB |

The historical source does not enlarge the Git checkout or installer, but it pollutes local search, code discovery, and AI context. The CSS count has increased since the previous audit, so the root cause is still active.

| Directory | Files | Size |
| --- | ---: | ---: |
| `build` | 31,610 | 17,565.71 MiB |
| `backups` | 127,524 | 31,483.90 MiB |
| `backup` | 10,799 | 807.99 MiB |
| `release` | 5,504 | 818.94 MiB |
| `node_modules` | 24,552 | 393.22 MiB |
| `dist` | 2,188 | 10.17 MiB |

`release` is protected. It is not a cleanup target.

## Largest Generated Areas

| Area | Size |
| --- | ---: |
| `build/grounding-runtime-failed-343bfe2` | 4,031.07 MiB |
| `build/grounding-runtime-candidate` | 3,840.59 MiB |
| `build/grounding-runtime-failed-af22c9a-httpx` | 3,840.46 MiB |
| `build/grounding-runtime-failed-f865f5a` | 3,822.35 MiB |
| `build/sidecar-current` | 416.55 MiB |
| `build/portable-candidate-current` | 361.52 MiB |

The four Grounding directories alone occupy about 15.17 GiB. The candidate was previously rejected for insufficient recall and must not enter the main package.

## Backup Multiplication

Five historical snapshots are approximately 6.11 GiB each:

- `backups/v56-20260819-093814-pre-v57`
- `backups/v64-fix9-20260819-142244`
- `backups/v64-light-ui-ok-20260819-144531`
- `backups/v65-disabled-cards-fixed-20260819-152556`
- `backups/v70-ui-wip-20260819-165818`

They contain repeated Rust `src-tauri/target` output and compiled Python runtime trees. These are build products embedded inside source snapshots, not five independent source histories.

## Recommended Cleanup Order

1. Protect: hash and exclude `release`, the active worktree, formal portable artifacts, and current candidate evidence.
2. Generated failures: remove the three failed Grounding builds after explicit approval.
3. Rejected candidate: archive its evaluation report, then remove its compiled runtime after explicit approval.
4. Backup normalization: retain source and metadata, remove embedded `target`, compiled sidecars, and dependency caches from historical snapshots after an inventory manifest is approved.
5. Historical UI source: archive the 85 CSS and 67 JS files once their unique content is checked against Git history.
6. Prevention: add one backup script that excludes `target`, `node_modules`, build outputs, model caches, and portable binaries.

## Active-Code Refactoring

Disk cleanup and source refactoring are separate operations. After the UX contracts stabilize, split the largest active files by tested ownership boundaries rather than line count alone:

- `python/atelier_ledger.py`: schema, assets, tasks, evidence, and migration services.
- `python/server.py`: route groups and application lifecycle.
- `src/js/app.js`: appearance/task presence, studio orchestration, and drawer coordination.
- `src/js/studio-canvas.js`: local edit state, rendering, and generation lifecycle.
- `src-tauri/src/main.rs`: sidecar lifecycle, window lifecycle, and release identity.

No deletion or monolith split is authorized by this audit alone.
