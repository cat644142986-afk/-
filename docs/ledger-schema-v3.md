# Product Atelier ledger schema v3

Schema v3 extends the existing `atelier.sqlite3`. It adds logical asset domains,
four independently recoverable workflow drafts, immutable job snapshots, result
reviews and execution traces. Physical source images remain content-addressed in
`asset_blobs`; the migration does not duplicate image files.

## Upgrade contract

- `SCHEMA_VERSION = 3` in `python/atelier_ledger.py` is the only supported ledger
  version for this application build.
- A v1 database upgrades through v2 and v3 in one `BEGIN IMMEDIATE`
  transaction. A v2 database upgrades directly to v3.
- Existing databases are backed up with SQLite's online backup API before any
  migration statement runs. New empty databases do not create a backup.
- All new tables, indexes, default collections and default drafts must exist
  before the schema marker advances to 3.
- If every v3 object exists but the marker remains 2, startup validates the full
  contract, `integrity_check` and foreign keys, then repairs only the marker.
- If only part of v3 exists, startup refuses without changing the database and
  points to the automatic pre-startup backup.
- Reopening v3 is idempotent and creates no backup.
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

Removing a membership never deletes `assets` or `asset_blobs`. Permanent purge
will be a guarded API operation in Phase 3 and may proceed only when no active
membership, task item, generation result, review, feedback or knowledge evidence
references the asset.

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
behavior.

### `execution_traces`

Records the execution stage, user input, compiled prompt, applied knowledge,
ignored fields, model, parameters, output and failure stage. This makes it
possible to explain whether a description influenced the result. For example,
the current quick background-removal path can explicitly trace a natural-language
selection request as ignored instead of silently accepting it.

## Compatibility behavior

- Every pre-v3 `workspace_source` asset is assigned to `product` during upgrade.
  Nothing is assigned to `group` or `cutout` without a user or API decision.
- Existing v2 jobs, attempts, generations and results remain unchanged. They do
  not receive invented snapshots; only jobs created by v3 code do.
- The legacy global asset-listing method remains available while the Phase 3 API
  and Phase 4 UI migrate to scoped queries.
- New imports default to the product collection for compatibility. Callers may
  explicitly choose `group` or `cutout`.

## Verification

`python -m unittest -v tests.test_ledger_migrations` verifies:

- v1 → v3 and v2 → v3 lossless upgrades with queryable backups;
- complete stale-marker repair and incomplete-contract refusal for v2 and v3;
- repeated, concurrent-thread and cross-process initialization;
- default collections and four draft mappings;
- collection isolation, soft removal, restoration and multi-domain membership;
- optimistic draft revision conflicts and cross-domain selection rejection;
- immutable job snapshots after subsequent draft edits;
- foreign keys, uniqueness, ranges and frozen job state transitions.

All tests use temporary databases and do not open the user's application data.
