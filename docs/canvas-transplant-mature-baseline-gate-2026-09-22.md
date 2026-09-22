# Canvas transplant — Mature Infinite Canvas Baseline Gate

Status: **closed for the isolated prototype**, not merged or promoted. Source remains on `codex/excalidraw-infinite-canvas`; formal desktop release remains unchanged. No Provider call was made.

The v8 packaged prototype (`build/canvas-mature-baseline-v8-20260922/Product Atelier.exe`, SHA256 `B43E38522E444E5057887E1C1BB5B50C1C9F7FB9BDC02FB7DEDE10E0146017CF`) passed the earlier packaged viewport, selection, transform, copy/undo, group/layer, native align/distribute, Fabric return, Conversation/Governor, restart, and compact-window checks. The targeted frontend suite passed 50/50 after this final sanity check.

Final sanity evidence:

- Bitmap Ctrl+V was verified in the v8 packaged isolation Canvas; its imported image was visible after reopening the Canvas.
- Delete removed the selected temporary pasted bitmap from that packaged Canvas; the transplanted Shell did not intercept the native key.
- A real Explorer-to-Canvas cross-window drop was **not rerun in v8**: the available desktop automation cannot reliably cross its window boundary. This is an accepted evidence boundary, not a claim of fresh v8 live coverage. The existing formal packaged workbench receipt (`build/canvas-workbench-closure-packaged-e4ff684-final/packaged-canvas-workbench-closure.json`) records `explorer_drop_respects_two_real_release_points: true`; current `tests/frontend/infinite-canvas-race.test.mjs` exercises FileList ingress, destination binding, and async import in the shared host.

Upgrade regression gate: the transplant projects Excalidraw 0.18.1's native align/distribute fieldset using CSS `fieldset:not(:nth-last-of-type(2))`. Any Excalidraw upgrade must recheck that DOM ordering and the packaged multi-selection align/distribute interaction before updating the pinned-version assertion in `tests/frontend/canvas-transplant.test.mjs`. Do not treat a passing source-only test as sufficient for that upgrade.
