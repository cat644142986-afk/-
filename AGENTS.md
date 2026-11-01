# Product Atelier — development handoff rules

Read `START_HERE.md` first. It is the current new-computer handoff; older README
sections and phase reports are historical, not proof that the current work is done.

- The active branch is `codex/excalidraw-infinite-canvas`. Preserve current work.
  Do not reset to `main`, merge, Promote, or replace a desktop shortcut by default.
- Current work is the node-based multimedia Canvas replacement in
  `prototypes/canvas-replacement-v1`, with adapters into the existing PA backend.
  The complete product acceptance gate remains OPEN. Do not call it complete
  based on unit tests or individual screenshots.
- The user wants a mature, continuous design workbench, including video and
  workflow behavior. Do not silently reduce it to an image-only mockup, redesign
  the App Shell, resume Home work, or treat the old Excalidraw shell as the target.
- Reuse existing PA Asset / Canvas ledger / Governor / Command / Task / Result /
  lineage / Catalog / Identity / Admission / Router / Fabric. Do not introduce
  another persistent task, media, provider or memory system.
- Reuse mature permissively licensed components and public APIs. Preserve notices.
  The referenced hero8152/Infinite-Canvas code is a behavior reference, not a
  blanket license to copy its source, CSS or assets. See the prototype notices.
- User artwork is not software chrome. Preserve image pixels, aspect ratios,
  alpha, user typography and colors unless the user explicitly edits the work.
  Prompt/reference/parameters survive ordinary deselection and focus changes.
- Test packaged candidates only with isolated temporary data and WebView roots.
  Never seed fixtures into formal user data. Do not delete old recovery data.
- No paid Provider call without a new explicit authorization for that call/budget.
  Never automatically retry paid calls. Offline video fixtures are not live AI
  evidence; never extrapolate admission across models or task kinds.
- Use delta-based validation. Do not repeat unchanged, already evidenced Canvas
  baseline tests, builds or paid canaries. Report exactly what was and was not run.
- Prefer targeted searches/reads. Do not inspect `.env`, credentials, auth caches
  or private knowledge vaults unless needed and authorized. Never commit secrets,
  user databases, original user media, build output or downloaded model weights.
- Keep progress updates concise. Computer-use should be bounded to the current
  interaction; release its screen-control/highlight state when no longer needed.
- Continue routine implementation without repeatedly asking the user to confirm
  details; pause for real destructive data changes, paid calls or release decisions.
  The present handoff itself authorizes no new product work or formal release.
