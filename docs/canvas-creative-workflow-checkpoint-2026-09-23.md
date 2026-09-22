# Canvas Creative Workflow — packaged checkpoint

Status: **packaged prototype checkpoint**, not merged or promoted. Branch: `codex/excalidraw-infinite-canvas`. No real Provider call was made.

Scope: Retake-derived reference picker/tray/controls and the existing BeatDesign-derived composer feed PA's existing Asset/Result binding, Governor preview, Task and lineage semantics. The Excalidraw canvas preserves user image and text content; no permanent reference or lineage arrows are drawn.

Targeted verification:

- Targeted frontend tests: **79/79 passed**. Production frontend build and Tauri packaged build (`--no-bundle --features custom-protocol`) passed after the null-selection reference guard was added.
- Packaged EXE: `build/canvas-creative-reference-packaged-633ceb1-r2/Product Atelier.exe`, SHA256 `A560539AF3C79C1D30838EE0CAF757F4469FC16F1A0FBDB444D1E33512753655`; isolated data under `build/canvas-creative-reference-packaged-633ceb1/isolated-data`.
- Imported `tests/fixtures/semantic_grounding_photos/coffee-powder.jpg` through the packaged file dialog. The durable asset is 960 × 1280 JPEG; its canvas image is 240 × 320 with `roundness: null` and `crop: null`, preserving the 3:4 ratio and rectangular visual.
- Created a native two-line Excalidraw text element in the packaged canvas. Saved and reopened with the same text and layout (`fontFamily: 2`, `fontSize: 20`, 140 × 46); PA did not substitute text styling or line breaks.
- On the selected source, picker → confirmation → reference tray → composer → remove → reselect worked. Ratio `1:1` and quality `4K` reached Governor preview with the written packaging-text/Logo constraint. Cancelling the preview left jobs, job items, attempts and Provider receipts at zero.
- A **test-only offline Result** was committed to the isolated ledger from `tests/fixtures/generation_quality/generated/packaging-text-brand.png` using the ledger's normal atomic result publication, with mock engine metadata and **zero Provider receipts**. It was bound to the same canvas as a Result with exact parent source asset. After packaged restart, both images loaded. The reference picker listed both source and Result; selecting Result populated the tray and Governor preview with that Result. Cancelling left the ledger at its single completed offline fixture job and zero Provider receipts.
- In a 960 × 600 packaged window, picker, composer and image-parameter popover were reachable without hiding the selected source image. Escape closed the picker and parameter popover; Escape exited composer editing. Chinese text and shortcut letters entered the focused composer without changing canvas tools. Ctrl+Enter opened Governor preview only; cancellation created no new Task. The targeted automated test covers Escape while IME composition is active; the packaged input check used Unicode entry, not a live OS IME composition session.
- After all reference/preview interactions, the saved scene had the source image, one text element and the offline Result image, with **zero arrow elements**. The Result's `lineage_parent_id` remained the source asset ID.

Evidence boundary: this is an isolated, unpaid prototype Gate. The offline Result is a fixture, not a Provider-generated output; no Provider execution, formal Validated receipt, merge or Promote is claimed. Mature Infinite Canvas Baseline operations were not rerun here.
