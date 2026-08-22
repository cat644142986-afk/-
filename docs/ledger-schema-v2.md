# Product Atelier ledger schema v2

Schema v2 is the single local source of truth for persistent source assets and
durable background jobs. It extends the existing `atelier.sqlite3`; it does not
create a second database.

## Upgrade contract

- The supported application schema is declared by `SCHEMA_VERSION` in
  `python/atelier_ledger.py`.
- A new database is created transactionally at the latest schema.
- An existing v1 database is backed up with SQLite's online backup API before
  any migration runs. The backup stays beside the source database and includes
  the source version and UTC timestamp in its name.
- All pending migration steps run in one `BEGIN IMMEDIATE` transaction. Any
  error rolls back schema changes, metadata changes, and data changes together.
- Reopening an up-to-date database is idempotent and creates no backup.
- A database newer than this application is rejected before journal mode or
  any schema metadata is changed.
- Existing v1 rows remain unchanged. Old `assets` rows receive a nullable
  `blob_id`; they are not assigned invented paths or blob records.

## v2 tables

### `asset_blobs`

One row represents one physical, content-addressed source file.

- `sha256` and `storage_path` are independently unique.
- `size_bytes`, `width`, and `height` must be valid non-negative/positive
  values.
- A logical `assets` row may reference a blob through `assets.blob_id`.
- Blob deletion is restricted while any logical asset references it.

### `jobs`

One row represents one user submission. It owns mode, immutable submitted
parameters, idempotency key, requested concurrency, aggregate counts, and
timestamps. A partial unique index prevents duplicate non-empty idempotency
keys.

### `job_items`

One row represents one ordered source unit within a job. It references a
logical source asset and optionally the generation that records prompt/model
provenance. `(job_id, position)` is unique. Progress is normalized to `0..1`.

### `task_attempts`

One row records one execution attempt for a job item. Retries add attempts; they
do not overwrite earlier model, error, latency, or execution metadata.

## Frozen state machines

Job states:

```text
queued -> running | paused | canceled
running -> paused | completed | partial | failed | canceling | interrupted
paused -> queued | running | completed | partial | failed | canceling | canceled | interrupted
canceling -> canceled | partial | failed
interrupted -> queued | running | partial | failed | canceled
partial -> running | completed | failed | canceled
failed -> queued | running
```

Job-item states:

```text
queued -> running | canceled
running -> completed | failed | canceling | interrupted
canceling -> canceled
interrupted -> queued | failed | canceled
failed -> queued
```

`completed` and `canceled` are terminal item states. Repeating the current state
is idempotent. `validate_status_transition()` rejects unknown states and illegal
edges before a caller mutates the database.

Parent status is derived from child rows; persisted counters match the same
transaction that changes child status. Scheduler/API methods use these
transitions and persist rejected mutations as `job.transition_rejected` events.

## Verification

`python -m unittest -v tests.test_ledger_migrations` proves:

- new database creation and repeated initialization;
- complete v1 row preservation;
- queryable pre-migration backup;
- transactional rollback after injected DDL failure;
- future-version rejection without changing bytes, journal mode, or sidecars;
- concurrent in-process initialization without duplicate migration; and
- legal retry/cancel edges plus rejection of illegal transitions.

Every test uses `TemporaryDirectory`; no test opens the user's application data.
