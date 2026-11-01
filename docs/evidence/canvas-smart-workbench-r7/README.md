# Canvas smart workbench — portable r6/r7 evidence

Saved into Git for the 2026-11-01 new-computer handoff. Original capture/build
directories are dated 20260930. These are previously captured isolated packaged
checks, **not a new full acceptance run**. The complete replacement Gate is OPEN.

## Candidate and source

- Candidate: `build/canvas-smart-v2-internal-r7-20260930/Product Atelier Canvas Prototype.exe`
- EXE SHA-256: `C4AFD2C4AD3D5F47918C3E69DEEEEBB83480F534B83A86D395ACFCE06628BBD1`
- Source at capture: `aafb778c2363a41a1aaa72dd71e6299341a3c927` plus the
  implementation changes subsequently preserved by the handoff tag
  `handoff/canvas-smart-workbench-2026-11-01`. Do not claim the earlier EXE was
  built from that later commit identity. A new machine must rebuild.
- Runtime: isolated Windows/Tauri/WebView2; schema 10; no formal-data writes.
- Build: r7 frontend and Rust build succeeded (4.42s / 35.18s in existing log).
- Paid Provider calls: 0. No Promote or desktop shortcut replacement.

## What the files prove

| File | Observed behavior | Boundary |
| --- | --- | --- |
| `outpaint-applied-and-revealed.png` (r6) | Apply transparent padding directly from current controls; reveal/select derived result | Local edit, not AI outpaint. 2458×1638 source → 2658×1838 output; original RGBA preserved at (100,100), padding alpha 0. |
| `video-tail-frame-and-reference.png` | Extract tail frame at 4.95s from 5s video, return 320×320 image and use as image reference | Exact video parent; no Provider call. |
| `result-restored-and-selected.png` | Restore an existing offline video Result from logs; close dialog, reveal/select Result | Existing Task/attempt/receipt unchanged. Earlier deletion of only its Canvas view did not auto-resurrect on restart. |
| `workflow-zip-import.png` | Native export then real UI import of ZIP, appended media/source node | Same resource hash; new asset/scene identity, not copied Task provenance. No automatic generation. |
| `video-workflow-r7.zip` | Small exported workflow containing PA artificial offline video fixture | Portable manual test input; not the full original ledger or user media. Import only into isolated test Canvas. |
| `audio-import-play.png` | Native file import of durable WAV; playback control changes to pause | The fixture is two seconds of silence. Not evidence of audible quality or complete playback/error handling. |

Further full restart after the r7 restore/audio/ZIP group, full mixed-object
performance, DPI matrix, and the full v2 workflow are **not yet covered**.
Screenshots alone do not prove backend identities; the durable checks below
were observed in the old isolated ledger, which is not uploaded.

## Recorded durable identity (offline only)

- Canvas: `spatial_5e6d507dc1c1b2cfabeb20d73d800361`
- Input asset: `ast_fb06a8aa8234463dac16130cb8d2ea84`
- Task: `job_cf192f5010864ff19d2cef3ad6cf2c77`
- Attempt: `attempt_4c73d2d951734324a6f9fb251b3081c1` (one attempt, max 1)
- Video Result: `ast_e383349e1df84212ac73e336acede02d`
- Restored node: `result_muo4gca6_1`
- Imported workspace source: `ast_e7b4446faac248c9916103073e949095`
- Imported node: `import_muo4x62q_3`
- Frozen Task digest before/after downstream recovery:
  `ea079356f377137fdd32ac0a8892214b2c25224b55852075e16cfd6c9208a798`
- Digest input: JSON sorted keys, UTF-8, `ensure_ascii=False`; fields `id`,
  `status`, `snapshot`, `items`, `provider_call_receipts`, `paid_call_authorization`.
- Live Provider receipts: 0. Offline fixture success does not admit a live video
  model, establish output quality, or authorize a new paid call.

## File integrity

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| audio-import-play.png | 72713 | 12367F6068B5F5766714FD052FB4ACE15DF35A619B5D2EADF6594575EC37C66A |
| outpaint-applied-and-revealed.png | 93404 | 12F0EFAB4AAE9E42DA46F518BDD5AF5E69637CD648A93180E319EDD8CD00F2BF |
| result-restored-and-selected.png | 71503 | AD86FF50A7923CA69CC5387F8BF2D9BB451C3E803562274A52EA7494B7F1D6F2 |
| video-tail-frame-and-reference.png | 77243 | AD9477A8EAE928C60C3EA4580F8047B6AAF56C7317DB0E33110CD6AD69B1356C |
| video-workflow-r7.zip | 15656 | ED2584C7ECFCC94D10E9827F9085A8D0B4C16374C740F36F20309D87E63E50C3 |
| workflow-zip-import.png | 62802 | 4689DA1AA1A0BAE3061580EF845585C9BB8DFF74CE9E31754AF3C83D610156FD |
