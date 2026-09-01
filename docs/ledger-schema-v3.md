# Product Atelier ledger schema v3 -> v4

Schema v3 added logical asset domains, four independently recoverable workflow
drafts, immutable job snapshots, result reviews and execution traces. Schema v4
extends that same ledger with immutable canvas-document versions and a canonical
command identity shared by quick workflows and the canvas. Physical source images
remain content-addressed in `asset_blobs`; neither migration duplicates image files
or embeds image bytes in canvas JSON.

## Upgrade contract

- `SCHEMA_VERSION = 4` in `python/atelier_ledger.py` is the only supported ledger
  version for this application build.
- A v1 database upgrades through v2, v3 and v4 in one `BEGIN IMMEDIATE`
  transaction. A v2 database upgrades through v3 and v4. A v3 database upgrades
  directly to v4.
- Existing databases are backed up with SQLite's online backup API before any
  migration statement runs. New empty databases do not create a backup.
- All required tables, columns, indexes, triggers, default collections and default
  drafts must exist before the schema marker advances to 4.
- If every object for the next schema exists but the marker remains stale, startup
  validates the complete contract, `integrity_check` and foreign keys, then repairs
  only the marker. This recovery applies independently to v2, v3 and v4.
- If only part of the next schema exists, startup refuses without changing the database and
  points to the automatic pre-startup backup.
- Reopening v4 is idempotent and creates no backup.
- A newer schema is rejected before journal mode or database bytes are changed.

## Stable identifiers and domain mapping

| Logical domain | Collection ID | Workflows |
|---|---|---|
| `product` | `col_product` | `single`, `multi-file` |
| `group` | `col_group` | `group-split` |
| `cutout` | `col_cutout` | `cutout-batch` |

The four stable draft IDs are `draft_single`, `draft_multi_file`,
`draft_group_split` and `draft_cutout_batch`. Single-product and multi-file
workflows share collection membership but never share draft rows.

## v3 tables

### `asset_collections`

The three logical domains. These rows classify assets without copying the
underlying file.

### `asset_collection_members`

One asset can belong to more than one collection. Membership records stable
ordering and the recycle-bin state:

- `active`: visible in the domain;
- `trashed`: removed from the domain but retained for undo, task lineage and
  historical evidence.

Removing a membership never deletes `assets` or `asset_blobs`. The Phase 3 purge
API defaults to a 30-day recycle retention period, requires an exact asset-ID
confirmation and proceeds only when no active membership, draft, task item,
snapshot, generation result, review, feedback, knowledge evidence or execution
trace references the asset.

### `workflow_drafts`

One durable draft exists for each workflow. It records:

- logical collection;
- optimistic `revision`;
- Creative Brief and structured intent;
- workflow parameters;
- active job, current generation and current result;
- compare state, canvas/UI restore state and mask/object-selection state.

Draft writes are atomic full replacements. `expected_revision` must match the
stored revision; otherwise `DraftRevisionConflictError` prevents a late async
response from overwriting newer user work.

### `draft_asset_selections`

Ordered asset selection for one draft. The ledger only accepts assets that are
active members of that draft's collection. Selection state therefore cannot
leak from group or cutout into product workflows.

### `job_snapshots`

Every newly created job receives exactly one immutable snapshot containing:

- mode and draft identity/revision;
- ordered source asset IDs;
- submitted brief and intent;
- submitted parameters and knowledge references;
- UI context required to return to the originating work area.

Later draft edits never update this row. Job retries continue from the original
snapshot instead of silently adopting changed settings.

### `result_reviews`

Stores one result-level review with the three product decisions:

- `adopt` — directly usable;
- `adjust` — direction is correct but needs changes;
- `reject` — overall direction is wrong.

Reason codes and notes are separate from `learning_action`. Recording feedback,
regenerating the current result and creating a knowledge suggestion are distinct
actions; the UI must not imply that every comment automatically changes future
behavior. API idempotency keys map to deterministic row IDs, so reconnects cannot
create duplicate review decisions.

### `execution_traces`

Records the execution stage, user input, compiled prompt, applied knowledge,
ignored fields, model, parameters, output and failure stage. This makes it
possible to explain whether a description influenced the result. For example,
the current quick background-removal path can explicitly trace a natural-language
selection request as ignored instead of silently accepting it.
Trace writes use the same deterministic-row idempotency contract.

## v4 canvas and command additions

### `canvas_documents`

One durable canvas document is bound to one workflow draft. The row only points to
the current immutable version and records the optimistic `current_revision`.
Canvas state is not stored in `workflow_drafts.ui_state`, so saving a newer canvas
cannot overwrite historical versions.

### `canvas_document_versions`

Each successful save appends a full canonical `CanvasDocument` JSON version with a
parent pointer, revision and `client_request_id`. Database triggers reject updates
and deletes. The save contract provides:

- optimistic concurrency through `expected_revision`;
- idempotent replay for an identical `client_request_id` and payload;
- an explicit conflict if an idempotency key is reused with different content;
- historical version reads without reconstructing state from mutable UI data.

The frozen coordinate system is `canvas-pixel`, top-left origin, X increasing to
the right and Y increasing downward. Every layer references an existing `assets.id`
or result asset. A `proxy_ref` is a rebuildable preview hint only: Base64 data and
absolute filesystem paths are rejected, and the returned proxy manifest identifies
`assets.id` as the authoritative source.

### `canvas_version_sources`

This immutable join table records every source asset or result referenced by one
canvas version. It protects lineage and makes permanent asset deletion explain why
a historical canvas version still blocks the operation.

### Canonical command bindings

`python/command_registry.py` is the single registry for four existing durable quick
workflows and three local canvas mutations. Schema v4 adds `command_id`,
`canvas_document_version_id` and `canvas_operation_id` to `job_snapshots` and
`execution_traces`. A canvas-triggered durable job therefore freezes the exact
command, canvas version and originating operation while the existing quick entry
continues through the same `jobs` and trace chain.

The production API contract is:

- `GET /api/workspaces/{mode}/canvas`;
- `PUT /api/workspaces/{mode}/canvas`;
- `GET /api/commands`;
- `POST /api/commands/{command_id}/execute`.

Workspace recovery also returns the mode's current `canvas`. These endpoints are
the data and execution foundation for G1B; they do not by themselves claim that the
Fabric canvas UI or pixel-accurate export is already released.

## Compatibility behavior

- Every pre-v3 `workspace_source` asset is assigned to `product` during upgrade.
  Nothing is assigned to `group` or `cutout` without a user or API decision.
- Existing v2 jobs, attempts, generations and results remain unchanged. They do
  not receive invented snapshots; only jobs created by v3 code do.
- The legacy global asset-listing method remains available while the Phase 4 UI
  migrates to the Phase 3 scoped workspace API.
- New imports default to the product collection for compatibility. Callers may
  explicitly choose `group` or `cutout`.

## Verification

`python -m unittest -v tests.test_ledger_migrations tests.test_canvas_ledger tests.test_command_registry tests.test_asset_api` verifies:

- v1 → v4, v2 → v4 and v3 → v4 upgrades with queryable pre-migration backups;
- complete stale-marker repair and incomplete-contract refusal through v4;
- repeated, concurrent-thread and cross-process initialization;
- default collections and four draft mappings;
- collection isolation, soft removal, restoration and multi-domain membership;
- optimistic draft revision conflicts and cross-domain selection rejection;
- immutable job snapshots after subsequent draft edits;
- immutable, replayable canvas versions with optimistic revision conflicts;
- coordinate, asset/result reference and rebuildable-proxy validation;
- canonical quick/canvas command identity in snapshots and traces;
- foreign keys, uniqueness, ranges and frozen job state transitions.

All tests use temporary databases and do not open the user's application data.
