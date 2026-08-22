# Product Atelier reliable workspace v2

This release turns the four existing image workflows into one local-first,
recoverable workspace. SQLite remains the only database; imported source pixels
live in a content-addressed asset directory and generated outputs retain their
source, generation, prompt, and job-item lineage.

## Runtime architecture

1. `AssetStore` validates and atomically imports JPG, PNG, or WEBP content to
   `assets/<sha256-prefix>/<sha256>.<extension>`.
2. `AtelierLedger.create_job()` persists a job, one ordered item per source
   asset, one generation record per item, and a client idempotency key.
3. `JobEngine` fairly claims one item per job round from SQLite. A bounded
   executor enforces global and per-job concurrency; processor admission is
   checked before claim so tasks waiting for a cloud gate cannot occupy every
   worker. Named gates independently limit VLM, cloud image generation, and
   local cutout stages. Local cutout stays at one concurrent BiRefNet session
   until a dedicated memory/stability stress suite proves a higher safe value.
4. A processor reads the source again by `source_asset_id`, writes outputs to an
   attempt-private staging directory, and returns a commit/cleanup pair. Result
   rows, attempt completion, item completion, and parent counters publish in
   one SQLite transaction, preventing restart from recharging an item whose
   results were already committed. Cancellation and publication are serialized.
5. A cross-process advisory lock elects one scheduler leader per ledger. A
   second live sidecar remains passive instead of recovering work owned by the
   leader, then automatically takes over after the leader process exits.
6. Leader startup records stale attempts as `interrupted`, requeues only
   attempts with budget remaining, and leaves never-started `queued` items
   intact.
7. The front end reloads `/api/assets` and `/api/jobs`; browser memory is never
   the authority for asset or task state.

`multi-file` and `cutout-batch` use one item per source. Variations and products
detected inside a group shot remain outputs inside that source item, preserving
the original four workflow semantics.

## Durable APIs

- `GET /api/assets`
- `POST /api/assets/import` and `/api/assets/import-batch`
- `GET /api/assets/{id}` plus `/content` and `/thumbnail`
- `POST /api/jobs` with `mode`, ordered `source_asset_ids`, `parameters`, and
  optional `client_request_id`
- `GET /api/jobs` and `/api/jobs/{id}`
- `POST /api/jobs/{id}/pause` and `/resume`; pause stops new claims while
  allowing an already-running item to settle safely
- `POST /api/jobs/{id}/cancel`
- `POST /api/jobs/{id}/retry` with optional failed `item_ids`
- `GET /api/progress/{id}` as a database-backed compatibility view

Legacy upload routes persist uploads through `AssetStore` before submitting the
same durable jobs. `/api/batch-folder` is retired with HTTP 410; it no longer
runs a fifth, untracked task system.

## Failure and recovery rules

- Duplicate non-empty idempotency keys replay the original job only when the
  mode, ordered sources, parameters, engine, concurrency, and attempt policy
  match. A different request returns a conflict.
- Queued cancellation creates no attempt. Running cancellation becomes
  `canceling` until a cooperative checkpoint; results returned by an
  uninterruptible external call are discarded.
- Pause is durable job state, does not suspend a thread, and does not create or
  consume an attempt. Resume exposes the same queued items to fair scheduling.
- One failed item does not erase siblings. Mixed terminal outcomes become
  `partial`; retry adds an attempt only to failed/interrupted items.
- Item progress means queue completion: completed, failed, and canceled items
  persist `1.0`; outcome quality remains visible in the separate status and
  success/failure counters.
- The database is written before progress observers are notified.
- Normal HTTP sessions are thread-local, and the rembg session is initialized
  under a lock and used behind its named resource gate.
- Group-shot detection validates the full VLM response before any paid image
  call, rejects more than 12 products, and fails explicitly when recognition is
  unavailable instead of silently treating the whole image as one product.
- Settings use a cross-process lock and atomic replacement; worker sidecars
  refresh the shared API key and knowledge path before executing work.
- Browser requests are accepted only from the packaged Tauri origins and the
  checked-in loopback Vite origin. Explicit foreign origins are rejected before
  even simple multipart mutations execute; origin-less native health probes
  remain supported.

## Offline verification

```bash
python -m unittest discover -s tests -v
npm run test:frontend
npm run build
```

The Python suite uses temporary databases and assets, mocked AI/VLM/cutout
processors, blocked HTTP requests, deterministic synchronization primitives,
two simultaneously live sidecars, automatic leader takeover, and a real
terminated child process for restart recovery. It never reads the production
ledger or spends cloud quota.
