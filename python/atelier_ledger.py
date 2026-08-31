# -*- coding: utf-8 -*-
"""Local-first creation ledger for Product Atelier.

The ledger stores design decisions and asset provenance, not image pixels or raw
interaction telemetry.  It is intentionally dependency-free so the packaged
sidecar can keep using the Python standard library.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 3
WORKSPACE_SESSION_ID = "ses_workspace"

COLLECTION_IDS = {
    "product": "col_product",
    "group": "col_group",
    "cutout": "col_cutout",
}
WORKFLOW_COLLECTIONS = {
    "single": "product",
    "multi-file": "product",
    "group-split": "group",
    "cutout-batch": "cutout",
}
WORKFLOW_DRAFT_IDS = {
    "single": "draft_single",
    "multi-file": "draft_multi_file",
    "group-split": "draft_group_split",
    "cutout-batch": "draft_cutout_batch",
}

JOB_STATUSES = frozenset({
    "queued", "running", "paused", "completed", "partial", "failed",
    "canceling", "canceled", "interrupted",
})
JOB_ITEM_STATUSES = frozenset({
    "queued", "running", "completed", "failed",
    "canceling", "canceled", "interrupted",
})

JOB_STATUS_TRANSITIONS = {
    "queued": frozenset({"running", "paused", "canceled"}),
    "running": frozenset({"paused", "completed", "partial", "failed", "canceling", "interrupted"}),
    "paused": frozenset({
        "queued", "running", "completed", "partial", "failed",
        "canceling", "canceled", "interrupted",
    }),
    "canceling": frozenset({"canceled", "partial", "failed"}),
    "interrupted": frozenset({"queued", "running", "partial", "failed", "canceled"}),
    "partial": frozenset({"running", "completed", "failed", "canceled"}),
    "failed": frozenset({"queued", "running"}),
    "completed": frozenset(),
    "canceled": frozenset(),
}
JOB_ITEM_STATUS_TRANSITIONS = {
    "queued": frozenset({"running", "canceled"}),
    "running": frozenset({"completed", "failed", "canceling", "interrupted"}),
    "canceling": frozenset({"canceled"}),
    "interrupted": frozenset({"queued", "failed", "canceled"}),
    "failed": frozenset({"queued"}),
    "completed": frozenset(),
    "canceled": frozenset(),
}


class LedgerSchemaError(RuntimeError):
    """Raised when a ledger cannot be safely opened or migrated."""


class UnsupportedSchemaVersionError(LedgerSchemaError):
    """Raised when the database was created by a newer application version."""


class PartialSchemaError(LedgerSchemaError):
    """Raised when migration objects exist but do not form a valid schema."""


class InvalidStatusTransitionError(ValueError):
    """Raised when a job or item attempts an illegal state transition."""


class IdempotencyConflictError(ValueError):
    """Raised when a request key is reused for a different durable job."""


class DraftRevisionConflictError(ValueError):
    """Raised when a stale client attempts to overwrite a newer draft."""


class MemorySuggestionRevisionConflictError(ValueError):
    """Raised when a stale client attempts to govern a newer suggestion."""

    def __init__(self, message: str, current: Mapping[str, Any]):
        super().__init__(message)
        self.current = dict(current)


class AssetPurgeBlockedError(ValueError):
    """Raised when a workspace asset still has protected references."""

    def __init__(self, message: str, summary: Mapping[str, Any]):
        super().__init__(message)
        self.summary = dict(summary)


V1_TABLES = frozenset({
    "ledger_meta", "sessions", "assets", "generations", "events",
    "feedback", "memory_suggestions",
})

V2_TABLE_COLUMNS = {
    "asset_blobs": frozenset({
        "id", "sha256", "storage_path", "mime", "size_bytes",
        "width", "height", "created_at",
    }),
    "jobs": frozenset({
        "id", "session_id", "mode", "status", "priority", "total_items",
        "completed_items", "failed_items", "canceled_items",
        "requested_concurrency", "idempotency_key", "parameters_json",
        "created_at", "queued_at", "started_at", "updated_at", "completed_at",
    }),
    "job_items": frozenset({
        "id", "job_id", "position", "source_asset_id", "generation_id",
        "engine_key", "status", "progress", "attempt_count", "max_attempts",
        "error_code", "error_message", "queued_at", "started_at",
        "updated_at", "completed_at",
    }),
    "task_attempts": frozenset({
        "id", "job_item_id", "attempt_number", "engine_key", "model",
        "status", "error_code", "error_message", "latency_ms",
        "metadata_json", "started_at", "completed_at",
    }),
}

V2_REQUIRED_INDEXES = frozenset({
    "idx_asset_blobs_sha256", "idx_assets_blob", "idx_assets_workspace_blob",
    "idx_jobs_status_queue", "idx_jobs_idempotency", "idx_jobs_session",
    "idx_job_items_job", "idx_job_items_status", "idx_job_items_source",
    "idx_task_attempts_item",
})

V3_TABLE_COLUMNS = {
    "asset_collections": frozenset({
        "id", "key", "name", "created_at", "updated_at",
    }),
    "asset_collection_members": frozenset({
        "id", "collection_id", "asset_id", "position", "status",
        "added_at", "updated_at", "removed_at",
    }),
    "workflow_drafts": frozenset({
        "id", "mode", "collection_id", "revision", "brief_json",
        "intent_json", "parameters_json", "active_job_id",
        "current_generation_id", "current_result_asset_id",
        "compare_state_json", "ui_state_json", "mask_state_json",
        "created_at", "updated_at",
    }),
    "draft_asset_selections": frozenset({
        "draft_id", "asset_id", "position", "selected_at",
    }),
    "job_snapshots": frozenset({
        "job_id", "draft_id", "draft_revision", "mode",
        "source_asset_ids_json", "brief_json", "intent_json",
        "parameters_json", "knowledge_refs_json", "ui_context_json",
        "created_at",
    }),
    "result_reviews": frozenset({
        "id", "job_id", "generation_id", "result_asset_id", "decision",
        "reason_codes_json", "note", "learning_action", "status",
        "created_at", "updated_at",
    }),
    "execution_traces": frozenset({
        "id", "job_id", "job_item_id", "generation_id", "stage", "status",
        "user_input_json", "compiled_prompt", "applied_knowledge_json",
        "ignored_fields_json", "model", "parameters_json", "output_json",
        "error_code", "error_message", "created_at",
    }),
}

V3_REQUIRED_INDEXES = frozenset({
    "idx_collection_members_collection",
    "idx_collection_members_asset",
    "idx_draft_selections_draft",
    "idx_drafts_active_job",
    "idx_job_snapshots_draft",
    "idx_reviews_job",
    "idx_reviews_result",
    "idx_traces_job",
    "idx_traces_item",
})

V1_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS ledger_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        mode TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        title TEXT NOT NULL DEFAULT '',
        project_name TEXT NOT NULL DEFAULT '',
        designer_profile TEXT NOT NULL DEFAULT 'default',
        brand_profile TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT 'general',
        brief_json TEXT NOT NULL DEFAULT '{}',
        intent_locks_json TEXT NOT NULL DEFAULT '{}',
        started_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assets (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        parent_asset_id TEXT,
        role TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'image',
        path TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL DEFAULT '',
        mime TEXT NOT NULL DEFAULT '',
        width INTEGER,
        height INTEGER,
        sha256 TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
        FOREIGN KEY(parent_asset_id) REFERENCES assets(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS generations (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        task_id TEXT NOT NULL DEFAULT '',
        parent_generation_id TEXT,
        model TEXT NOT NULL DEFAULT '',
        prompt TEXT NOT NULL DEFAULT '',
        negative_prompt TEXT NOT NULL DEFAULT '',
        parameters_json TEXT NOT NULL DEFAULT '{}',
        knowledge_refs_json TEXT NOT NULL DEFAULT '[]',
        prompt_version TEXT NOT NULL DEFAULT 'v1',
        status TEXT NOT NULL DEFAULT 'queued',
        result_asset_ids_json TEXT NOT NULL DEFAULT '[]',
        error TEXT NOT NULL DEFAULT '',
        latency_ms INTEGER,
        estimated_cost REAL,
        created_at TEXT NOT NULL,
        completed_at TEXT,
        FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
        FOREIGN KEY(parent_generation_id) REFERENCES generations(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        generation_id TEXT,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
        FOREIGN KEY(generation_id) REFERENCES generations(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS feedback (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        generation_id TEXT,
        asset_id TEXT,
        signal TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        structured_json TEXT NOT NULL DEFAULT '{}',
        scope TEXT NOT NULL DEFAULT 'session',
        created_at TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
        FOREIGN KEY(generation_id) REFERENCES generations(id) ON DELETE SET NULL,
        FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_suggestions (
        id TEXT PRIMARY KEY,
        scope_type TEXT NOT NULL,
        scope_id TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT 'general',
        rule_key TEXT NOT NULL,
        current_value_json TEXT NOT NULL DEFAULT 'null',
        proposed_value_json TEXT NOT NULL DEFAULT 'null',
        evidence_json TEXT NOT NULL DEFAULT '[]',
        confidence REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        reviewed_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_assets_session ON assets(session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_generations_session ON generations(session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_generations_task ON generations(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_session ON feedback(session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_memory_pending ON memory_suggestions(status, created_at)",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def idempotent_id(prefix: str, request_id: str) -> str:
    request_id = str(request_id).strip()
    if not request_id:
        raise ValueError("client_request_id is required")
    if len(request_id) > 200:
        raise ValueError("client_request_id is too long")
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}_{digest}"


def validate_status_transition(current: str, target: str, *, item: bool = False) -> None:
    """Validate the frozen v2 job state machine without mutating the database."""
    statuses = JOB_ITEM_STATUSES if item else JOB_STATUSES
    transitions = JOB_ITEM_STATUS_TRANSITIONS if item else JOB_STATUS_TRANSITIONS
    entity = "job item" if item else "job"
    if current not in statuses:
        raise InvalidStatusTransitionError(f"unknown {entity} status: {current}")
    if target not in statuses:
        raise InvalidStatusTransitionError(f"unknown {entity} status: {target}")
    if current == target:
        return
    if target not in transitions[current]:
        raise InvalidStatusTransitionError(
            f"illegal {entity} transition: {current} -> {target}"
        )


def encode_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))


def decode_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def json_contains_value(value: Any, target: str) -> bool:
    """Return whether a decoded JSON value contains one exact string value."""
    if isinstance(value, str):
        return value == target
    if isinstance(value, Mapping):
        return any(json_contains_value(item, target) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(json_contains_value(item, target) for item in value)
    return False


class AtelierLedger:
    """Thread-safe SQLite facade for sessions, generations and learning signals."""

    _schema_locks_guard = threading.Lock()
    _schema_locks: dict[str, threading.Lock] = {}

    @classmethod
    def _schema_lock_for(cls, db_path: Path) -> threading.Lock:
        key = str(db_path.resolve())
        with cls._schema_locks_guard:
            return cls._schema_locks.setdefault(key, threading.Lock())

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_lock = self._schema_lock_for(self.db_path)
        self.last_migration_backup: Path | None = None
        self.last_schema_repair: str | None = None
        self._ensure_schema()

    def _connect(self, *, configure_storage: bool = True) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=20, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 20000")
        if configure_storage:
            connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _configure_storage(self) -> None:
        """Persist WAL mode once schema work is complete.

        SQLite's ``PRAGMA journal_mode`` does not consistently honor
        ``busy_timeout`` while another process is opening or migrating the same
        database. Retrying only this idempotent pragma avoids making every
        ordinary connection race to reconfigure the database.
        """
        deadline = time.monotonic() + 20.0
        delay = 0.01
        while True:
            connection = self._connect(configure_storage=False)
            try:
                row = connection.execute("PRAGMA journal_mode = WAL").fetchone()
                journal_mode = str(row[0]).lower() if row is not None else ""
                if journal_mode != "wal":
                    raise LedgerSchemaError(
                        f"SQLite refused WAL journal mode (reported {journal_mode!r})"
                    )
                connection.execute("PRAGMA synchronous = NORMAL")
                return
            except sqlite3.OperationalError as exc:
                locked = "locked" in str(exc).lower() or "busy" in str(exc).lower()
                if not locked or time.monotonic() >= deadline:
                    raise LedgerSchemaError("Failed to configure SQLite WAL mode") from exc
            finally:
                connection.close()
            time.sleep(delay)
            delay = min(delay * 2, 0.25)

    @contextmanager
    def _connection(self):
        """Commit or roll back, then always release the Windows file handle."""
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def _immediate_connection(self):
        """Serialize read-modify-write operations across threads and processes."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _table_names(connection: sqlite3.Connection) -> set[str]:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    @classmethod
    def _read_schema_version(cls, connection: sqlite3.Connection) -> int:
        tables = cls._table_names(connection)
        if not tables:
            return 0
        if "ledger_meta" not in tables:
            raise LedgerSchemaError("Existing database has no ledger_meta table; refusing to guess its schema")
        row = connection.execute(
            "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            if V1_TABLES.issubset(tables):
                return 1
            raise LedgerSchemaError("Existing database has no schema_version metadata")
        try:
            version = int(row[0])
        except (TypeError, ValueError) as exc:
            raise LedgerSchemaError(f"Invalid ledger schema version: {row[0]!r}") from exc
        if version < 1:
            raise LedgerSchemaError(f"Invalid ledger schema version: {version}")
        return version

    @staticmethod
    def _write_schema_version(connection: sqlite3.Connection, version: int) -> None:
        connection.execute(
            "INSERT INTO ledger_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(version),),
        )

    @staticmethod
    def _create_v1_schema(connection: sqlite3.Connection) -> None:
        for statement in V1_SCHEMA_STATEMENTS:
            connection.execute(statement)

    @classmethod
    def _v2_objects_present(cls, connection: sqlite3.Connection) -> bool:
        """Return whether any durable-workspace v2 object already exists."""
        tables = cls._table_names(connection)
        if tables.intersection(V2_TABLE_COLUMNS):
            return True
        if "assets" in tables:
            asset_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(assets)")
            }
            if "blob_id" in asset_columns:
                return True
        return False

    @classmethod
    def _v2_contract_issues(cls, connection: sqlite3.Connection) -> list[str]:
        """Describe why an existing v2-shaped database is unsafe to mark v2.

        Older builds could commit all v2 objects but fail to advance the schema
        marker.  That state is recoverable only when the complete frozen v2
        contract is present and SQLite reports both structural and referential
        integrity.  Anything else is left untouched for explicit recovery.
        """
        issues: list[str] = []
        tables = cls._table_names(connection)
        for table, required_columns in V2_TABLE_COLUMNS.items():
            if table not in tables:
                issues.append(f"missing table {table}")
                continue
            actual_columns = {
                str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
            }
            missing_columns = sorted(required_columns - actual_columns)
            if missing_columns:
                issues.append(f"{table} missing columns: {', '.join(missing_columns)}")

        if "assets" not in tables:
            issues.append("missing table assets")
        else:
            asset_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(assets)")
            }
            if "blob_id" not in asset_columns:
                issues.append("assets missing column blob_id")

        indexes = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
            )
        }
        missing_indexes = sorted(V2_REQUIRED_INDEXES - indexes)
        if missing_indexes:
            issues.append(f"missing indexes: {', '.join(missing_indexes)}")

        integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        if integrity_rows != ["ok"]:
            issues.append(f"integrity_check failed: {'; '.join(integrity_rows[:3])}")

        foreign_key_rows = list(connection.execute("PRAGMA foreign_key_check"))
        if foreign_key_rows:
            issues.append(f"foreign_key_check found {len(foreign_key_rows)} violation(s)")
        return issues

    @classmethod
    def _v3_objects_present(cls, connection: sqlite3.Connection) -> bool:
        """Return whether any scoped-workspace v3 object already exists."""
        return bool(cls._table_names(connection).intersection(V3_TABLE_COLUMNS))

    @classmethod
    def _v3_contract_issues(cls, connection: sqlite3.Connection) -> list[str]:
        """Describe an incomplete v3 workspace contract without mutating it."""
        issues: list[str] = []
        tables = cls._table_names(connection)
        for table, required_columns in V3_TABLE_COLUMNS.items():
            if table not in tables:
                issues.append(f"missing table {table}")
                continue
            actual_columns = {
                str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
            }
            missing_columns = sorted(required_columns - actual_columns)
            if missing_columns:
                issues.append(f"{table} missing columns: {', '.join(missing_columns)}")

        indexes = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
            )
        }
        missing_indexes = sorted(V3_REQUIRED_INDEXES - indexes)
        if missing_indexes:
            issues.append(f"missing indexes: {', '.join(missing_indexes)}")

        if "asset_collections" in tables:
            collection_rows = connection.execute(
                "SELECT id, key FROM asset_collections"
            ).fetchall()
            actual_collections = {str(row["key"]): str(row["id"]) for row in collection_rows}
            if actual_collections != COLLECTION_IDS:
                issues.append("default asset collections are missing or inconsistent")

        if "workflow_drafts" in tables:
            draft_rows = connection.execute(
                "SELECT id, mode, collection_id FROM workflow_drafts"
            ).fetchall()
            actual_drafts = {
                str(row["mode"]): (str(row["id"]), str(row["collection_id"]))
                for row in draft_rows
            }
            expected_drafts = {
                mode: (draft_id, COLLECTION_IDS[WORKFLOW_COLLECTIONS[mode]])
                for mode, draft_id in WORKFLOW_DRAFT_IDS.items()
            }
            if actual_drafts != expected_drafts:
                issues.append("default workflow drafts are missing or inconsistent")

        integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        if integrity_rows != ["ok"]:
            issues.append(f"integrity_check failed: {'; '.join(integrity_rows[:3])}")
        foreign_key_rows = list(connection.execute("PRAGMA foreign_key_check"))
        if foreign_key_rows:
            issues.append(f"foreign_key_check found {len(foreign_key_rows)} violation(s)")
        return issues

    def _migration_backup_path(self, version: int) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        return self.db_path.with_name(
            f"{self.db_path.name}.backup-v{version}-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
        )

    def _backup_database(self, version: int) -> Path:
        backup_path = self._migration_backup_path(version)
        source_connection = sqlite3.connect(self.db_path, timeout=20)
        backup_connection = sqlite3.connect(backup_path)
        try:
            source_connection.backup(backup_connection)
            backup_connection.commit()
        except Exception:
            backup_connection.close()
            source_connection.close()
            backup_path.unlink(missing_ok=True)
            raise
        else:
            backup_connection.close()
            source_connection.close()
        return backup_path

    @staticmethod
    def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE asset_blobs (
                id TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL UNIQUE,
                storage_path TEXT NOT NULL UNIQUE,
                mime TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
                width INTEGER NOT NULL CHECK(width > 0),
                height INTEGER NOT NULL CHECK(height > 0),
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "ALTER TABLE assets ADD COLUMN blob_id TEXT REFERENCES asset_blobs(id) ON DELETE RESTRICT"
        )
        connection.execute(
            """
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued'
                    CHECK(status IN ('queued','running','paused','completed','partial','failed','canceling','canceled','interrupted')),
                priority INTEGER NOT NULL DEFAULT 0,
                total_items INTEGER NOT NULL DEFAULT 0 CHECK(total_items >= 0),
                completed_items INTEGER NOT NULL DEFAULT 0 CHECK(completed_items >= 0),
                failed_items INTEGER NOT NULL DEFAULT 0 CHECK(failed_items >= 0),
                canceled_items INTEGER NOT NULL DEFAULT 0 CHECK(canceled_items >= 0),
                requested_concurrency INTEGER NOT NULL DEFAULT 1 CHECK(requested_concurrency >= 1),
                idempotency_key TEXT NOT NULL DEFAULT '',
                parameters_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                queued_at TEXT NOT NULL,
                started_at TEXT,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE job_items (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                position INTEGER NOT NULL CHECK(position >= 0),
                source_asset_id TEXT NOT NULL,
                generation_id TEXT,
                engine_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued'
                    CHECK(status IN ('queued','running','completed','failed','canceling','canceled','interrupted')),
                progress REAL NOT NULL DEFAULT 0 CHECK(progress >= 0 AND progress <= 1),
                attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
                max_attempts INTEGER NOT NULL DEFAULT 1 CHECK(max_attempts >= 1),
                error_code TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                queued_at TEXT NOT NULL,
                started_at TEXT,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE,
                FOREIGN KEY(source_asset_id) REFERENCES assets(id) ON DELETE RESTRICT,
                FOREIGN KEY(generation_id) REFERENCES generations(id) ON DELETE SET NULL,
                UNIQUE(job_id, position)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE task_attempts (
                id TEXT PRIMARY KEY,
                job_item_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL CHECK(attempt_number >= 1),
                engine_key TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL
                    CHECK(status IN ('running','completed','failed','canceled','interrupted')),
                error_code TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                latency_ms INTEGER CHECK(latency_ms IS NULL OR latency_ms >= 0),
                metadata_json TEXT NOT NULL DEFAULT '{}',
                started_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(job_item_id) REFERENCES job_items(id) ON DELETE CASCADE,
                UNIQUE(job_item_id, attempt_number)
            )
            """
        )
        connection.execute("CREATE INDEX idx_asset_blobs_sha256 ON asset_blobs(sha256)")
        connection.execute("CREATE INDEX idx_assets_blob ON assets(blob_id)")
        connection.execute(
            "CREATE UNIQUE INDEX idx_assets_workspace_blob ON assets(blob_id, role) "
            "WHERE blob_id IS NOT NULL AND role = 'workspace_source'"
        )
        connection.execute("CREATE INDEX idx_jobs_status_queue ON jobs(status, priority DESC, queued_at)")
        connection.execute(
            "CREATE UNIQUE INDEX idx_jobs_idempotency ON jobs(idempotency_key) WHERE idempotency_key <> ''"
        )
        connection.execute("CREATE INDEX idx_jobs_session ON jobs(session_id, created_at)")
        connection.execute("CREATE INDEX idx_job_items_job ON job_items(job_id, position)")
        connection.execute("CREATE INDEX idx_job_items_status ON job_items(status, queued_at)")
        connection.execute("CREATE INDEX idx_job_items_source ON job_items(source_asset_id)")
        connection.execute("CREATE INDEX idx_task_attempts_item ON task_attempts(job_item_id, attempt_number)")

    @staticmethod
    def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
        """Create scoped workspaces, durable drafts and immutable task evidence."""
        connection.execute(
            """
            CREATE TABLE asset_collections (
                id TEXT PRIMARY KEY,
                key TEXT NOT NULL UNIQUE
                    CHECK(key IN ('product','group','cutout')),
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE asset_collection_members (
                id TEXT PRIMARY KEY,
                collection_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                position INTEGER NOT NULL CHECK(position >= 0),
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active','trashed')),
                added_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                removed_at TEXT,
                FOREIGN KEY(collection_id) REFERENCES asset_collections(id) ON DELETE CASCADE,
                FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE RESTRICT,
                UNIQUE(collection_id, asset_id),
                UNIQUE(collection_id, position)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE workflow_drafts (
                id TEXT PRIMARY KEY,
                mode TEXT NOT NULL UNIQUE
                    CHECK(mode IN ('single','multi-file','group-split','cutout-batch')),
                collection_id TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
                brief_json TEXT NOT NULL DEFAULT '{}',
                intent_json TEXT NOT NULL DEFAULT '{}',
                parameters_json TEXT NOT NULL DEFAULT '{}',
                active_job_id TEXT,
                current_generation_id TEXT,
                current_result_asset_id TEXT,
                compare_state_json TEXT NOT NULL DEFAULT '{}',
                ui_state_json TEXT NOT NULL DEFAULT '{}',
                mask_state_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(collection_id) REFERENCES asset_collections(id) ON DELETE RESTRICT,
                FOREIGN KEY(active_job_id) REFERENCES jobs(id) ON DELETE SET NULL,
                FOREIGN KEY(current_generation_id) REFERENCES generations(id) ON DELETE SET NULL,
                FOREIGN KEY(current_result_asset_id) REFERENCES assets(id) ON DELETE SET NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE draft_asset_selections (
                draft_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                position INTEGER NOT NULL CHECK(position >= 0),
                selected_at TEXT NOT NULL,
                PRIMARY KEY(draft_id, asset_id),
                UNIQUE(draft_id, position),
                FOREIGN KEY(draft_id) REFERENCES workflow_drafts(id) ON DELETE CASCADE,
                FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE RESTRICT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE job_snapshots (
                job_id TEXT PRIMARY KEY,
                draft_id TEXT,
                draft_revision INTEGER NOT NULL DEFAULT 0 CHECK(draft_revision >= 0),
                mode TEXT NOT NULL
                    CHECK(mode IN ('single','multi-file','group-split','cutout-batch')),
                source_asset_ids_json TEXT NOT NULL DEFAULT '[]',
                brief_json TEXT NOT NULL DEFAULT '{}',
                intent_json TEXT NOT NULL DEFAULT '{}',
                parameters_json TEXT NOT NULL DEFAULT '{}',
                knowledge_refs_json TEXT NOT NULL DEFAULT '[]',
                ui_context_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE,
                FOREIGN KEY(draft_id) REFERENCES workflow_drafts(id) ON DELETE SET NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE result_reviews (
                id TEXT PRIMARY KEY,
                job_id TEXT,
                generation_id TEXT,
                result_asset_id TEXT NOT NULL,
                decision TEXT NOT NULL DEFAULT 'pending'
                    CHECK(decision IN ('pending','adopt','adjust','reject')),
                reason_codes_json TEXT NOT NULL DEFAULT '[]',
                note TEXT NOT NULL DEFAULT '',
                learning_action TEXT NOT NULL DEFAULT 'none'
                    CHECK(learning_action IN ('none','record','regenerate','suggest')),
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','submitted','retracted')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE SET NULL,
                FOREIGN KEY(generation_id) REFERENCES generations(id) ON DELETE SET NULL,
                FOREIGN KEY(result_asset_id) REFERENCES assets(id) ON DELETE RESTRICT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE execution_traces (
                id TEXT PRIMARY KEY,
                job_id TEXT,
                job_item_id TEXT,
                generation_id TEXT,
                stage TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK(status IN ('started','completed','failed','skipped')),
                user_input_json TEXT NOT NULL DEFAULT '{}',
                compiled_prompt TEXT NOT NULL DEFAULT '',
                applied_knowledge_json TEXT NOT NULL DEFAULT '[]',
                ignored_fields_json TEXT NOT NULL DEFAULT '[]',
                model TEXT NOT NULL DEFAULT '',
                parameters_json TEXT NOT NULL DEFAULT '{}',
                output_json TEXT NOT NULL DEFAULT '{}',
                error_code TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE SET NULL,
                FOREIGN KEY(job_item_id) REFERENCES job_items(id) ON DELETE SET NULL,
                FOREIGN KEY(generation_id) REFERENCES generations(id) ON DELETE SET NULL
            )
            """
        )

        connection.execute(
            "CREATE INDEX idx_collection_members_collection "
            "ON asset_collection_members(collection_id, status, position)"
        )
        connection.execute(
            "CREATE INDEX idx_collection_members_asset ON asset_collection_members(asset_id)"
        )
        connection.execute(
            "CREATE INDEX idx_draft_selections_draft ON draft_asset_selections(draft_id, position)"
        )
        connection.execute(
            "CREATE INDEX idx_drafts_active_job ON workflow_drafts(active_job_id)"
        )
        connection.execute(
            "CREATE INDEX idx_job_snapshots_draft ON job_snapshots(draft_id, created_at)"
        )
        connection.execute("CREATE INDEX idx_reviews_job ON result_reviews(job_id, created_at)")
        connection.execute(
            "CREATE INDEX idx_reviews_result ON result_reviews(result_asset_id, created_at)"
        )
        connection.execute("CREATE INDEX idx_traces_job ON execution_traces(job_id, created_at)")
        connection.execute(
            "CREATE INDEX idx_traces_item ON execution_traces(job_item_id, created_at)"
        )

        now = utc_now()
        collection_names = {
            "product": "产品素材",
            "group": "合照素材",
            "cutout": "抠图素材",
        }
        for key, collection_id in COLLECTION_IDS.items():
            connection.execute(
                "INSERT INTO asset_collections(id, key, name, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (collection_id, key, collection_names[key], now, now),
            )
        for mode, draft_id in WORKFLOW_DRAFT_IDS.items():
            connection.execute(
                """
                INSERT INTO workflow_drafts(
                    id, mode, collection_id, revision, created_at, updated_at
                ) VALUES(?, ?, ?, 1, ?, ?)
                """,
                (draft_id, mode, COLLECTION_IDS[WORKFLOW_COLLECTIONS[mode]], now, now),
            )

        legacy_assets = connection.execute(
            "SELECT id, created_at FROM assets WHERE role = 'workspace_source' "
            "ORDER BY created_at ASC, id ASC"
        ).fetchall()
        for position, asset in enumerate(legacy_assets):
            connection.execute(
                """
                INSERT INTO asset_collection_members(
                    id, collection_id, asset_id, position, status,
                    added_at, updated_at
                ) VALUES(?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    new_id("member"), COLLECTION_IDS["product"], str(asset["id"]),
                    position, str(asset["created_at"]), now,
                ),
            )

    def _ensure_schema(self) -> None:
        with self._schema_lock:
            # Probe the version without changing journal mode. An older app must
            # leave a future-version database byte-for-byte untouched.
            connection = self._connect(configure_storage=False)
            try:
                current_version = self._read_schema_version(connection)
                if current_version > SCHEMA_VERSION:
                    raise UnsupportedSchemaVersionError(
                        f"Ledger schema v{current_version} is newer than supported v{SCHEMA_VERSION}; "
                        "upgrade Product Atelier before opening this database"
                    )
                if current_version != SCHEMA_VERSION:
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        # Another process may have completed the migration while this
                        # initializer waited for SQLite's write lock.
                        current_version = self._read_schema_version(connection)
                        if current_version > SCHEMA_VERSION:
                            raise UnsupportedSchemaVersionError(
                                f"Ledger schema v{current_version} is newer than supported v{SCHEMA_VERSION}; "
                                "upgrade Product Atelier before opening this database"
                            )
                        if current_version != SCHEMA_VERSION:
                            is_new_database = current_version == 0
                            if is_new_database:
                                self._create_v1_schema(connection)
                                self._write_schema_version(connection, 1)
                                current_version = 1
                            else:
                                # The SQLite write lock is already held. A separate read
                                # connection can now take a consistent online backup
                                # without another process racing the schema version.
                                self.last_migration_backup = self._backup_database(current_version)

                            while current_version < SCHEMA_VERSION:
                                if current_version == 1:
                                    if self._v2_objects_present(connection):
                                        issues = self._v2_contract_issues(connection)
                                        if issues:
                                            raise PartialSchemaError(
                                                "Detected an incomplete v2 ledger while schema metadata says v1; "
                                                "the database was not changed. Restore the automatic backup or "
                                                f"repair these objects first: {' | '.join(issues)}"
                                            )
                                        repair = "recovered complete v2 schema with stale v1 metadata"
                                        self.last_schema_repair = (
                                            f"{self.last_schema_repair}; {repair}"
                                            if self.last_schema_repair else repair
                                        )
                                    else:
                                        self._migrate_v1_to_v2(connection)
                                    current_version = 2
                                elif current_version == 2:
                                    if self._v3_objects_present(connection):
                                        issues = self._v3_contract_issues(connection)
                                        if issues:
                                            raise PartialSchemaError(
                                                "Detected an incomplete v3 ledger while schema metadata says v2; "
                                                "the database was not changed. Restore the automatic backup or "
                                                f"repair these objects first: {' | '.join(issues)}"
                                            )
                                        repair = "recovered complete v3 schema with stale v2 metadata"
                                        self.last_schema_repair = (
                                            f"{self.last_schema_repair}; {repair}"
                                            if self.last_schema_repair else repair
                                        )
                                    else:
                                        self._migrate_v2_to_v3(connection)
                                    current_version = 3
                                else:
                                    raise LedgerSchemaError(
                                        f"No migration path from schema v{current_version}"
                                    )
                                self._write_schema_version(connection, current_version)
                        connection.commit()
                    except Exception as exc:
                        connection.rollback()
                        if isinstance(exc, (UnsupportedSchemaVersionError, PartialSchemaError)):
                            raise
                        raise LedgerSchemaError(
                            f"Failed to migrate ledger to schema v{SCHEMA_VERSION}"
                        ) from exc
            finally:
                connection.close()
            # Configure storage after schema work. If multiple processes raced
            # above, none holds the migration transaction while setting WAL.
            self._configure_storage()

    def create_session(
        self,
        mode: str,
        *,
        title: str = "",
        project_name: str = "",
        designer_profile: str = "default",
        brand_profile: str = "",
        category: str = "general",
        brief: dict[str, Any] | None = None,
        intent_locks: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id = new_id("ses")
        now = utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO sessions(
                    id, mode, title, project_name, designer_profile, brand_profile,
                    category, brief_json, intent_locks_json, started_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id, mode, title, project_name, designer_profile,
                    brand_profile, category, encode_json(brief), encode_json(intent_locks),
                    now, now,
                ),
            )
        self.add_event(session_id, "session.created", {"mode": mode, "title": title})
        return self.get_session(session_id, include_timeline=False)

    def update_session(self, session_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {
            "mode", "status", "title", "project_name", "designer_profile",
            "brand_profile", "category", "completed_at",
        }
        json_fields = {"brief": "brief_json", "intent_locks": "intent_locks_json"}
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            if key in allowed:
                assignments.append(f"{key} = ?")
                values.append(value)
            elif key in json_fields:
                assignments.append(f"{json_fields[key]} = ?")
                values.append(encode_json(value))
        if not assignments:
            return self.get_session(session_id, include_timeline=False)
        assignments.append("updated_at = ?")
        values.append(utc_now())
        values.append(session_id)
        with self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE sessions SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            if cursor.rowcount == 0:
                raise KeyError(f"unknown session: {session_id}")
        return self.get_session(session_id, include_timeline=False)

    def add_asset(
        self,
        session_id: str,
        role: str,
        *,
        path: str = "",
        name: str = "",
        mime: str = "",
        width: int | None = None,
        height: int | None = None,
        sha256: str = "",
        data: bytes | None = None,
        kind: str = "image",
        parent_asset_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        asset_id = new_id("ast")
        if data is not None and not sha256:
            sha256 = hashlib.sha256(data).hexdigest()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO assets(
                    id, session_id, parent_asset_id, role, kind, path, name, mime,
                    width, height, sha256, metadata_json, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id, session_id, parent_asset_id, role, kind, path, name,
                    mime, width, height, sha256, encode_json(metadata), utc_now(),
                ),
            )
        self.add_event(session_id, "asset.added", {"asset_id": asset_id, "role": role, "name": name})
        return self.get_asset(asset_id)

    def register_workspace_asset(
        self,
        *,
        sha256: str,
        storage_path: str,
        mime: str,
        size_bytes: int,
        width: int,
        height: int,
        name: str,
        metadata: dict[str, Any] | None = None,
        collection_key: str = "product",
    ) -> dict[str, Any]:
        """Register one content-addressed source and return its stable logical asset."""
        if collection_key not in COLLECTION_IDS:
            raise ValueError(f"unsupported asset collection: {collection_key}")
        blob_id = new_id("blob")
        asset_id = new_id("ast")
        now = utc_now()
        with self._immediate_connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO sessions(
                    id, mode, status, title, project_name, designer_profile,
                    brand_profile, category, brief_json, intent_locks_json,
                    started_at, updated_at
                ) VALUES(?, 'workspace', 'active', '素材工作区', '', 'default', '',
                         'general', '{}', '{}', ?, ?)
                """,
                (WORKSPACE_SESSION_ID, now, now),
            )
            connection.execute(
                """
                INSERT INTO asset_blobs(
                    id, sha256, storage_path, mime, size_bytes, width, height, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sha256) DO NOTHING
                """,
                (blob_id, sha256, storage_path, mime, size_bytes, width, height, now),
            )
            blob = connection.execute(
                "SELECT * FROM asset_blobs WHERE sha256 = ?", (sha256,)
            ).fetchone()
            if blob is None:
                raise LedgerSchemaError(f"failed to register asset blob: {sha256}")
            if (
                str(blob["storage_path"]) != storage_path
                or str(blob["mime"]) != mime
                or int(blob["size_bytes"]) != int(size_bytes)
                or int(blob["width"]) != int(width)
                or int(blob["height"]) != int(height)
            ):
                raise LedgerSchemaError(
                    f"content hash metadata conflict for asset blob: {sha256}"
                )
            existing = connection.execute(
                "SELECT id FROM assets WHERE blob_id = ? AND role = 'workspace_source'",
                (blob["id"],),
            ).fetchone()
            if existing is None:
                try:
                    connection.execute(
                        """
                        INSERT INTO assets(
                            id, session_id, parent_asset_id, role, kind, path, name,
                            mime, width, height, sha256, metadata_json, created_at, blob_id
                        ) VALUES(?, ?, NULL, 'workspace_source', 'image', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            asset_id, WORKSPACE_SESSION_ID, storage_path, name, mime,
                            width, height, sha256, encode_json(metadata), now, blob["id"],
                        ),
                    )
                except sqlite3.IntegrityError:
                    existing = connection.execute(
                        "SELECT id FROM assets WHERE blob_id = ? AND role = 'workspace_source'",
                        (blob["id"],),
                    ).fetchone()
                    if existing is None:
                        raise
            if existing is not None:
                asset_id = str(existing["id"])
            membership = connection.execute(
                "SELECT id, status FROM asset_collection_members "
                "WHERE collection_id = ? AND asset_id = ?",
                (COLLECTION_IDS[collection_key], asset_id),
            ).fetchone()
            if membership is None:
                next_position = int(connection.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM asset_collection_members "
                    "WHERE collection_id = ?",
                    (COLLECTION_IDS[collection_key],),
                ).fetchone()[0])
                connection.execute(
                    """
                    INSERT INTO asset_collection_members(
                        id, collection_id, asset_id, position, status,
                        added_at, updated_at
                    ) VALUES(?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        new_id("member"), COLLECTION_IDS[collection_key], asset_id,
                        next_position, now, now,
                    ),
                )
            elif str(membership["status"]) == "trashed":
                connection.execute(
                    "UPDATE asset_collection_members SET status = 'active', "
                    "removed_at = NULL, updated_at = ? WHERE id = ?",
                    (now, membership["id"]),
                )
        return self.get_workspace_asset(asset_id)

    def get_workspace_asset(self, asset_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT a.*,
                       b.id AS blob_record_id,
                       b.sha256 AS blob_sha256,
                       b.storage_path AS blob_storage_path,
                       b.mime AS blob_mime,
                       b.size_bytes AS blob_size_bytes,
                       b.width AS blob_width,
                       b.height AS blob_height,
                       b.created_at AS blob_created_at
                FROM assets a
                JOIN asset_blobs b ON b.id = a.blob_id
                WHERE a.id = ? AND a.role = 'workspace_source'
                """,
                (asset_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown workspace asset: {asset_id}")
        return self._workspace_asset_row(row)

    def find_workspace_asset_by_sha256(self, sha256: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT a.*,
                       b.id AS blob_record_id,
                       b.sha256 AS blob_sha256,
                       b.storage_path AS blob_storage_path,
                       b.mime AS blob_mime,
                       b.size_bytes AS blob_size_bytes,
                       b.width AS blob_width,
                       b.height AS blob_height,
                       b.created_at AS blob_created_at
                FROM assets a
                JOIN asset_blobs b ON b.id = a.blob_id
                WHERE b.sha256 = ? AND a.role = 'workspace_source'
                LIMIT 1
                """,
                (sha256,),
            ).fetchone()
        return self._workspace_asset_row(row) if row is not None else None

    def list_workspace_assets(self, limit: int = 500) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 2000))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT a.*,
                       b.id AS blob_record_id,
                       b.sha256 AS blob_sha256,
                       b.storage_path AS blob_storage_path,
                       b.mime AS blob_mime,
                       b.size_bytes AS blob_size_bytes,
                       b.width AS blob_width,
                       b.height AS blob_height,
                       b.created_at AS blob_created_at
                FROM assets a
                JOIN asset_blobs b ON b.id = a.blob_id
                WHERE a.role = 'workspace_source'
                ORDER BY a.created_at ASC, a.id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._workspace_asset_row(row) for row in rows]

    def list_collection_assets(
        self,
        collection_key: str,
        *,
        include_trashed: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List one logical asset domain without duplicating physical files."""
        if collection_key not in COLLECTION_IDS:
            raise ValueError(f"unsupported asset collection: {collection_key}")
        limit = max(1, min(int(limit), 2000))
        offset = max(0, int(offset))
        statuses = ("active", "trashed") if include_trashed else ("active",)
        placeholders = ",".join("?" for _ in statuses)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT a.*,
                       b.id AS blob_record_id,
                       b.sha256 AS blob_sha256,
                       b.storage_path AS blob_storage_path,
                       b.mime AS blob_mime,
                       b.size_bytes AS blob_size_bytes,
                       b.width AS blob_width,
                       b.height AS blob_height,
                       b.created_at AS blob_created_at,
                       m.id AS membership_id,
                       m.position AS membership_position,
                       m.status AS membership_status,
                       m.added_at AS membership_added_at,
                       m.updated_at AS membership_updated_at,
                       m.removed_at AS membership_removed_at
                FROM asset_collection_members m
                JOIN assets a ON a.id = m.asset_id
                JOIN asset_blobs b ON b.id = a.blob_id
                WHERE m.collection_id = ? AND m.status IN ({placeholders})
                ORDER BY m.position ASC, m.added_at ASC, m.id ASC
                LIMIT ? OFFSET ?
                """,
                (COLLECTION_IDS[collection_key], *statuses, limit, offset),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = self._workspace_asset_row(row)
            item["membership"] = {
                "id": item.pop("membership_id"),
                "collection": collection_key,
                "position": item.pop("membership_position"),
                "status": item.pop("membership_status"),
                "added_at": item.pop("membership_added_at"),
                "updated_at": item.pop("membership_updated_at"),
                "removed_at": item.pop("membership_removed_at"),
            }
            items.append(item)
        return items

    def count_collection_assets(self, collection_key: str, *, include_trashed: bool = False) -> int:
        if collection_key not in COLLECTION_IDS:
            raise ValueError(f"unsupported asset collection: {collection_key}")
        statuses = ("active", "trashed") if include_trashed else ("active",)
        placeholders = ",".join("?" for _ in statuses)
        with self._connection() as connection:
            return int(connection.execute(
                f"SELECT COUNT(*) FROM asset_collection_members "
                f"WHERE collection_id = ? AND status IN ({placeholders})",
                (COLLECTION_IDS[collection_key], *statuses),
            ).fetchone()[0])

    def add_asset_to_collection(self, asset_id: str, collection_key: str) -> dict[str, Any]:
        """Add or restore an existing physical asset in one logical domain."""
        if collection_key not in COLLECTION_IDS:
            raise ValueError(f"unsupported asset collection: {collection_key}")
        now = utc_now()
        with self._immediate_connection() as connection:
            asset = connection.execute(
                "SELECT id FROM assets WHERE id = ? AND role = 'workspace_source'",
                (asset_id,),
            ).fetchone()
            if asset is None:
                raise KeyError(f"unknown workspace asset: {asset_id}")
            member = connection.execute(
                "SELECT * FROM asset_collection_members WHERE collection_id = ? AND asset_id = ?",
                (COLLECTION_IDS[collection_key], asset_id),
            ).fetchone()
            if member is None:
                position = int(connection.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM asset_collection_members "
                    "WHERE collection_id = ?",
                    (COLLECTION_IDS[collection_key],),
                ).fetchone()[0])
                connection.execute(
                    """
                    INSERT INTO asset_collection_members(
                        id, collection_id, asset_id, position, status,
                        added_at, updated_at
                    ) VALUES(?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        new_id("member"), COLLECTION_IDS[collection_key], asset_id,
                        position, now, now,
                    ),
                )
            elif str(member["status"]) != "active":
                connection.execute(
                    "UPDATE asset_collection_members SET status = 'active', "
                    "removed_at = NULL, updated_at = ? WHERE id = ?",
                    (now, member["id"]),
                )
        return next(
            item for item in self.list_collection_assets(collection_key)
            if item["id"] == asset_id
        )

    def remove_asset_from_collection(self, asset_id: str, collection_key: str) -> dict[str, Any]:
        """Soft-remove an asset from a logical domain while preserving lineage."""
        if collection_key not in COLLECTION_IDS:
            raise ValueError(f"unsupported asset collection: {collection_key}")
        now = utc_now()
        with self._immediate_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE asset_collection_members
                SET status = 'trashed', removed_at = ?, updated_at = ?
                WHERE collection_id = ? AND asset_id = ? AND status = 'active'
                """,
                (now, now, COLLECTION_IDS[collection_key], asset_id),
            )
            if cursor.rowcount == 0:
                row = connection.execute(
                    "SELECT status FROM asset_collection_members "
                    "WHERE collection_id = ? AND asset_id = ?",
                    (COLLECTION_IDS[collection_key], asset_id),
                ).fetchone()
                if row is None:
                    raise KeyError(f"asset is not in {collection_key}: {asset_id}")
            affected_drafts = connection.execute(
                """
                SELECT d.id
                FROM workflow_drafts d
                JOIN draft_asset_selections s ON s.draft_id = d.id
                WHERE d.collection_id = ? AND s.asset_id = ?
                ORDER BY d.id
                """,
                (COLLECTION_IDS[collection_key], asset_id),
            ).fetchall()
            for draft_row in affected_drafts:
                draft_id = str(draft_row["id"])
                connection.execute(
                    "DELETE FROM draft_asset_selections WHERE draft_id = ? AND asset_id = ?",
                    (draft_id, asset_id),
                )
                remaining = connection.execute(
                    "SELECT asset_id FROM draft_asset_selections "
                    "WHERE draft_id = ? ORDER BY position, selected_at, asset_id",
                    (draft_id,),
                ).fetchall()
                connection.execute(
                    "UPDATE draft_asset_selections SET position = position + 1000000 "
                    "WHERE draft_id = ?",
                    (draft_id,),
                )
                for position, selection in enumerate(remaining):
                    connection.execute(
                        "UPDATE draft_asset_selections SET position = ? "
                        "WHERE draft_id = ? AND asset_id = ?",
                        (position, draft_id, str(selection["asset_id"])),
                    )
                connection.execute(
                    "UPDATE workflow_drafts "
                    "SET revision = revision + 1, updated_at = ? WHERE id = ?",
                    (now, draft_id),
                )
        return next(
            item for item in self.list_collection_assets(
                collection_key, include_trashed=True
            ) if item["id"] == asset_id
        )

    def reorder_collection_assets(
        self, collection_key: str, ordered_asset_ids: Iterable[str]
    ) -> list[dict[str, Any]]:
        """Replace the complete active order while retaining trashed members."""
        if collection_key not in COLLECTION_IDS:
            raise ValueError(f"unsupported asset collection: {collection_key}")
        requested = [str(asset_id) for asset_id in ordered_asset_ids]
        if len(requested) != len(set(requested)):
            raise ValueError("collection order contains duplicate asset IDs")
        collection_id = COLLECTION_IDS[collection_key]
        now = utc_now()
        with self._immediate_connection() as connection:
            rows = connection.execute(
                "SELECT asset_id, status FROM asset_collection_members "
                "WHERE collection_id = ? ORDER BY position",
                (collection_id,),
            ).fetchall()
            active = [str(row["asset_id"]) for row in rows if row["status"] == "active"]
            if set(requested) != set(active) or len(requested) != len(active):
                raise ValueError("collection order must contain every active asset exactly once")
            trashed = [str(row["asset_id"]) for row in rows if row["status"] == "trashed"]
            connection.execute(
                "UPDATE asset_collection_members SET position = position + 1000000 "
                "WHERE collection_id = ?",
                (collection_id,),
            )
            for position, member_asset_id in enumerate([*requested, *trashed]):
                connection.execute(
                    "UPDATE asset_collection_members SET position = ?, updated_at = ? "
                    "WHERE collection_id = ? AND asset_id = ?",
                    (position, now, collection_id, member_asset_id),
                )
        return self.list_collection_assets(collection_key)

    @staticmethod
    def _asset_reference_summary(
        connection: sqlite3.Connection,
        asset_id: str,
        *,
        retention_days: int,
    ) -> dict[str, Any]:
        asset = connection.execute(
            """
            SELECT a.id, a.role, a.created_at, a.blob_id,
                   b.storage_path, b.sha256
            FROM assets a
            LEFT JOIN asset_blobs b ON b.id = a.blob_id
            WHERE a.id = ?
            """,
            (asset_id,),
        ).fetchone()
        if asset is None or str(asset["role"]) != "workspace_source":
            raise KeyError(f"unknown workspace asset: {asset_id}")

        memberships = [
            {
                "collection": str(row["collection_key"]),
                "status": str(row["status"]),
                "removed_at": row["removed_at"],
            }
            for row in connection.execute(
                """
                SELECT c.key AS collection_key, m.status, m.removed_at
                FROM asset_collection_members m
                JOIN asset_collections c ON c.id = m.collection_id
                WHERE m.asset_id = ? ORDER BY c.key
                """,
                (asset_id,),
            )
        ]

        references: dict[str, list[str]] = {
            "drafts": [
                str(row["draft_id"])
                for row in connection.execute(
                    "SELECT draft_id FROM draft_asset_selections WHERE asset_id = ?",
                    (asset_id,),
                )
            ],
            "jobs": [
                str(row["id"])
                for row in connection.execute(
                    "SELECT DISTINCT job_id AS id FROM job_items WHERE source_asset_id = ?",
                    (asset_id,),
                )
            ],
            "child_assets": [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM assets WHERE parent_asset_id = ?",
                    (asset_id,),
                )
            ],
            "feedback": [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM feedback WHERE asset_id = ?",
                    (asset_id,),
                )
            ],
            "reviews": [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM result_reviews WHERE result_asset_id = ?",
                    (asset_id,),
                )
            ],
            "workspace_previews": [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM workflow_drafts WHERE current_result_asset_id = ?",
                    (asset_id,),
                )
            ],
            "job_snapshots": [],
            "generation_results": [],
            "knowledge_evidence": [],
            "execution_traces": [],
        }
        for row in connection.execute("SELECT job_id, source_asset_ids_json FROM job_snapshots"):
            if json_contains_value(decode_json(row["source_asset_ids_json"], []), asset_id):
                references["job_snapshots"].append(str(row["job_id"]))
        for row in connection.execute("SELECT id, result_asset_ids_json FROM generations"):
            if json_contains_value(decode_json(row["result_asset_ids_json"], []), asset_id):
                references["generation_results"].append(str(row["id"]))
        for row in connection.execute(
            "SELECT id, current_value_json, proposed_value_json, evidence_json FROM memory_suggestions"
        ):
            values = (
                decode_json(row["current_value_json"], None),
                decode_json(row["proposed_value_json"], None),
                decode_json(row["evidence_json"], []),
            )
            if any(json_contains_value(value, asset_id) for value in values):
                references["knowledge_evidence"].append(str(row["id"]))
        for row in connection.execute(
            "SELECT id, user_input_json, parameters_json, output_json FROM execution_traces"
        ):
            values = (
                decode_json(row["user_input_json"], {}),
                decode_json(row["parameters_json"], {}),
                decode_json(row["output_json"], {}),
            )
            if any(json_contains_value(value, asset_id) for value in values):
                references["execution_traces"].append(str(row["id"]))

        active_memberships = [
            item["collection"] for item in memberships if item["status"] == "active"
        ]
        retention_days = max(0, int(retention_days))
        anchors = [
            str(item["removed_at"])
            for item in memberships
            if item["status"] == "trashed" and item["removed_at"]
        ]
        anchor_text = max(anchors) if anchors else str(asset["created_at"])
        try:
            anchor = datetime.fromisoformat(anchor_text)
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
        except ValueError:
            anchor = datetime.now(timezone.utc)
        eligible_at = anchor + timedelta(days=retention_days)
        now_utc = datetime.now(timezone.utc)
        retention_pending = now_utc < eligible_at
        retention_remaining_days = max(
            0,
            int(math.ceil((eligible_at - now_utc).total_seconds() / 86400)),
        )
        reference_count = sum(len(ids) for ids in references.values())
        blockers = []
        if active_memberships:
            blockers.append("active_membership")
        blockers.extend(key for key, ids in references.items() if ids)
        if retention_pending:
            blockers.append("retention_period")
        return {
            "asset_id": asset_id,
            "blob_sha256": str(asset["sha256"] or ""),
            "storage_path": str(asset["storage_path"] or ""),
            "memberships": memberships,
            "active_memberships": active_memberships,
            "references": references,
            "reference_count": reference_count,
            "retention_days": retention_days,
            "retention_remaining_days": retention_remaining_days,
            "purge_eligible_at": eligible_at.isoformat(timespec="milliseconds"),
            "retention_pending": retention_pending,
            "blockers": blockers,
            "purge_allowed": not blockers,
        }

    def asset_reference_summary(
        self, asset_id: str, *, retention_days: int = 30
    ) -> dict[str, Any]:
        with self._connection() as connection:
            return self._asset_reference_summary(
                connection, asset_id, retention_days=retention_days
            )

    def purge_workspace_asset(
        self, asset_id: str, *, retention_days: int = 30
    ) -> dict[str, Any]:
        """Permanently remove unreferenced metadata after the recycle retention period."""
        with self._immediate_connection() as connection:
            summary = self._asset_reference_summary(
                connection, asset_id, retention_days=retention_days
            )
            if not summary["purge_allowed"]:
                raise AssetPurgeBlockedError(
                    "workspace asset is still protected and cannot be purged", summary
                )
            blob_id = connection.execute(
                "SELECT blob_id FROM assets WHERE id = ?", (asset_id,)
            ).fetchone()["blob_id"]
            connection.execute(
                "DELETE FROM asset_collection_members WHERE asset_id = ?", (asset_id,)
            )
            connection.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
            blob_deleted = False
            if blob_id and connection.execute(
                "SELECT 1 FROM assets WHERE blob_id = ? LIMIT 1", (blob_id,)
            ).fetchone() is None:
                connection.execute("DELETE FROM asset_blobs WHERE id = ?", (blob_id,))
                blob_deleted = True
        return {**summary, "purged": True, "blob_deleted": blob_deleted}

    def get_workflow_draft(self, mode: str) -> dict[str, Any]:
        if mode not in WORKFLOW_DRAFT_IDS:
            raise ValueError(f"unsupported workflow mode: {mode}")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT d.*, c.key AS collection_key
                FROM workflow_drafts d
                JOIN asset_collections c ON c.id = d.collection_id
                WHERE d.mode = ?
                """,
                (mode,),
            ).fetchone()
            if row is None:
                raise LedgerSchemaError(f"missing workflow draft: {mode}")
            selected = connection.execute(
                "SELECT asset_id FROM draft_asset_selections "
                "WHERE draft_id = ? ORDER BY position",
                (row["id"],),
            ).fetchall()
        item = dict(row)
        item["brief"] = decode_json(item.pop("brief_json"), {})
        item["intent"] = decode_json(item.pop("intent_json"), {})
        item["parameters"] = decode_json(item.pop("parameters_json"), {})
        item["compare_state"] = decode_json(item.pop("compare_state_json"), {})
        item["ui_state"] = decode_json(item.pop("ui_state_json"), {})
        item["mask_state"] = decode_json(item.pop("mask_state_json"), {})
        item["selected_asset_ids"] = [str(selected_row["asset_id"]) for selected_row in selected]
        return item

    def save_workflow_draft(
        self,
        mode: str,
        *,
        expected_revision: int,
        selected_asset_ids: Iterable[str],
        brief: Mapping[str, Any] | None = None,
        intent: Mapping[str, Any] | None = None,
        parameters: Mapping[str, Any] | None = None,
        active_job_id: str | None = None,
        current_generation_id: str | None = None,
        current_result_asset_id: str | None = None,
        compare_state: Mapping[str, Any] | None = None,
        ui_state: Mapping[str, Any] | None = None,
        mask_state: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically replace one workflow draft using optimistic concurrency."""
        if mode not in WORKFLOW_DRAFT_IDS:
            raise ValueError(f"unsupported workflow mode: {mode}")
        selected_asset_ids = [str(asset_id) for asset_id in selected_asset_ids]
        if len(selected_asset_ids) != len(set(selected_asset_ids)):
            raise ValueError("draft asset selections must be unique")
        now = utc_now()
        with self._immediate_connection() as connection:
            draft = connection.execute(
                "SELECT * FROM workflow_drafts WHERE mode = ?", (mode,)
            ).fetchone()
            if draft is None:
                raise LedgerSchemaError(f"missing workflow draft: {mode}")
            if int(draft["revision"]) != int(expected_revision):
                raise DraftRevisionConflictError(
                    f"draft {mode} is revision {draft['revision']}, not {expected_revision}"
                )
            if selected_asset_ids:
                placeholders = ",".join("?" for _ in selected_asset_ids)
                found_rows = connection.execute(
                    f"""
                    SELECT asset_id FROM asset_collection_members
                    WHERE collection_id = ? AND status = 'active'
                      AND asset_id IN ({placeholders})
                    """,
                    (draft["collection_id"], *selected_asset_ids),
                ).fetchall()
                found = {str(row["asset_id"]) for row in found_rows}
                missing = [asset_id for asset_id in selected_asset_ids if asset_id not in found]
                if missing:
                    raise ValueError(
                        "draft selections are outside its active collection: "
                        + ", ".join(missing)
                    )
            if active_job_id:
                job = connection.execute(
                    "SELECT mode FROM jobs WHERE id = ?", (active_job_id,)
                ).fetchone()
                if job is None or str(job["mode"]) != mode:
                    raise ValueError("active job does not belong to this workflow")
            next_revision = int(draft["revision"]) + 1
            connection.execute(
                """
                UPDATE workflow_drafts
                SET revision = ?, brief_json = ?, intent_json = ?, parameters_json = ?,
                    active_job_id = ?, current_generation_id = ?,
                    current_result_asset_id = ?, compare_state_json = ?,
                    ui_state_json = ?, mask_state_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    next_revision, encode_json(dict(brief or {})),
                    encode_json(dict(intent or {})), encode_json(dict(parameters or {})),
                    active_job_id, current_generation_id, current_result_asset_id,
                    encode_json(dict(compare_state or {})),
                    encode_json(dict(ui_state or {})), encode_json(dict(mask_state or {})),
                    now, draft["id"],
                ),
            )
            connection.execute(
                "DELETE FROM draft_asset_selections WHERE draft_id = ?", (draft["id"],)
            )
            for position, asset_id in enumerate(selected_asset_ids):
                connection.execute(
                    "INSERT INTO draft_asset_selections(draft_id, asset_id, position, selected_at) "
                    "VALUES(?, ?, ?, ?)",
                    (draft["id"], asset_id, position, now),
                )
        return self.get_workflow_draft(mode)

    def complete_workflow(
        self,
        mode: str,
        *,
        expected_revision: int,
        client_request_id: str,
        job_id: str,
        result_asset_id: str,
    ) -> dict[str, Any]:
        """Atomically close one task scene while preserving its durable history."""
        if mode not in WORKFLOW_DRAFT_IDS:
            raise ValueError(f"unsupported workflow mode: {mode}")
        request_id = str(client_request_id).strip()
        idempotent_id("completion", request_id)
        job_id = str(job_id).strip()
        result_asset_id = str(result_asset_id).strip()
        if not job_id or not result_asset_id:
            raise ValueError("job_id and result_asset_id are required")
        candidate = {
            "client_request_id": request_id,
            "mode": mode,
            "job_id": job_id,
            "result_asset_id": result_asset_id,
        }
        event_id: int | None = None
        replayed = False
        now = utc_now()
        with self._immediate_connection() as connection:
            for event in connection.execute(
                "SELECT * FROM events WHERE event_type = 'workspace.completed' ORDER BY id DESC"
            ).fetchall():
                payload = decode_json(event["payload_json"], {})
                if str(payload.get("client_request_id") or "") != request_id:
                    continue
                comparable = {key: payload.get(key) for key in candidate}
                if comparable != candidate:
                    raise IdempotencyConflictError(
                        "client_request_id already belongs to a different workspace completion"
                    )
                event_id = int(event["id"])
                replayed = True
                break

            if not replayed:
                draft = connection.execute(
                    "SELECT * FROM workflow_drafts WHERE mode = ?", (mode,)
                ).fetchone()
                if draft is None:
                    raise LedgerSchemaError(f"missing workflow draft: {mode}")
                if int(draft["revision"]) != int(expected_revision):
                    raise DraftRevisionConflictError(
                        f"draft {mode} is revision {draft['revision']}, not {expected_revision}"
                    )
                job = connection.execute(
                    "SELECT id, session_id, mode FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
                if job is None:
                    raise KeyError(f"unknown job: {job_id}")
                if str(job["mode"]) != mode:
                    raise ValueError("completed job does not belong to this workflow")
                if str(draft["active_job_id"] or "") != job_id:
                    raise DraftRevisionConflictError(
                        "the workflow cursor no longer points to the completed job"
                    )
                generations = connection.execute(
                    """
                    SELECT g.id, g.result_asset_ids_json
                    FROM generations g
                    JOIN job_items ji ON ji.generation_id = g.id
                    WHERE ji.job_id = ?
                    """,
                    (job_id,),
                ).fetchall()
                matching_generation = next((
                    row for row in generations
                    if result_asset_id in decode_json(row["result_asset_ids_json"], [])
                ), None)
                if matching_generation is None:
                    raise ValueError("completed result does not belong to its job")

                next_revision = int(draft["revision"]) + 1
                connection.execute(
                    """
                    UPDATE workflow_drafts
                    SET revision = ?, brief_json = '{}', intent_json = '{}',
                        active_job_id = NULL, current_generation_id = NULL,
                        current_result_asset_id = NULL, compare_state_json = '{}',
                        ui_state_json = '{}', mask_state_json = '{}', updated_at = ?
                    WHERE id = ?
                    """,
                    (next_revision, now, draft["id"]),
                )
                connection.execute(
                    "DELETE FROM draft_asset_selections WHERE draft_id = ?", (draft["id"],)
                )
                payload = {
                    **candidate,
                    "generation_id": str(matching_generation["id"]),
                    "completed_revision": next_revision,
                    "completed_at": now,
                }
                cursor = connection.execute(
                    """
                    INSERT INTO events(
                        session_id, generation_id, event_type, payload_json, created_at
                    ) VALUES(?, ?, 'workspace.completed', ?, ?)
                    """,
                    (
                        str(job["session_id"]), str(matching_generation["id"]),
                        encode_json(payload), now,
                    ),
                )
                event_id = int(cursor.lastrowid)
                connection.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (now, str(job["session_id"])),
                )
        return {
            "event_id": event_id,
            "replayed": replayed,
            "draft": self.get_workflow_draft(mode),
        }

    def has_asset_blob(self, sha256: str) -> bool:
        with self._connection() as connection:
            return connection.execute(
                "SELECT 1 FROM asset_blobs WHERE sha256 = ?", (sha256,)
            ).fetchone() is not None

    def create_job(
        self,
        mode: str,
        source_asset_ids: Iterable[str],
        *,
        engine_key: str,
        parameters: dict[str, Any] | None = None,
        idempotency_key: str = "",
        requested_concurrency: int = 1,
        max_attempts: int = 2,
        title: str = "",
    ) -> tuple[dict[str, Any], bool]:
        source_asset_ids = [str(asset_id) for asset_id in source_asset_ids]
        if mode not in {"single", "multi-file", "group-split", "cutout-batch"}:
            raise ValueError(f"unsupported job mode: {mode}")
        if not source_asset_ids:
            raise ValueError("at least one source asset is required")
        if len(source_asset_ids) != len(set(source_asset_ids)):
            raise ValueError("source asset IDs must be unique within a job")
        engine_key = str(engine_key).strip()
        if not engine_key:
            raise ValueError("engine_key is required")
        idempotency_key = str(idempotency_key).strip()
        if len(idempotency_key) > 200:
            raise ValueError("idempotency key is too long")
        requested_concurrency = max(1, min(int(requested_concurrency), 24))
        max_attempts = max(1, min(int(max_attempts), 10))
        parameters = dict(parameters or {})
        job_id = new_id("job")
        session_id = new_id("ses")
        now = utc_now()
        created = False

        with self._immediate_connection() as connection:
            if idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if existing is not None:
                    job_id = str(existing["id"])
                    existing_items = connection.execute(
                        "SELECT source_asset_id, engine_key, max_attempts FROM job_items "
                        "WHERE job_id = ? ORDER BY position",
                        (job_id,),
                    ).fetchall()
                    same_request = (
                        str(existing["mode"]) == mode
                        and decode_json(existing["parameters_json"], {}) == parameters
                        and int(existing["requested_concurrency"]) == requested_concurrency
                        and [str(row["source_asset_id"]) for row in existing_items]
                        == source_asset_ids
                        and all(str(row["engine_key"]) == engine_key for row in existing_items)
                        and all(int(row["max_attempts"]) == max_attempts for row in existing_items)
                    )
                    if not same_request:
                        raise IdempotencyConflictError(
                            "idempotency key already belongs to a different job request"
                        )
                else:
                    created = True
            else:
                created = True

            if created:
                placeholders = ",".join("?" for _ in source_asset_ids)
                rows = connection.execute(
                    f"""
                    SELECT a.id FROM assets a
                    JOIN asset_blobs b ON b.id = a.blob_id
                    WHERE a.role = 'workspace_source' AND a.id IN ({placeholders})
                    """,
                    source_asset_ids,
                ).fetchall()
                found = {str(row["id"]) for row in rows}
                missing = [asset_id for asset_id in source_asset_ids if asset_id not in found]
                if missing:
                    raise KeyError(f"unknown workspace assets: {', '.join(missing)}")

                brief = parameters.get("brief") if isinstance(parameters.get("brief"), dict) else {}
                intent_locks = (
                    parameters.get("intent_locks")
                    if isinstance(parameters.get("intent_locks"), dict)
                    else {}
                )
                connection.execute(
                    """
                    INSERT INTO sessions(
                        id, mode, status, title, project_name, designer_profile,
                        brand_profile, category, brief_json, intent_locks_json,
                        started_at, updated_at
                    ) VALUES(?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id, mode, title or mode,
                        str(parameters.get("project_name", "")),
                        str(parameters.get("designer_profile", "default")),
                        str(parameters.get("brand_profile", "")),
                        str(parameters.get("category", "general")),
                        encode_json(brief), encode_json(intent_locks), now, now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO jobs(
                        id, session_id, mode, status, priority, total_items,
                        requested_concurrency, idempotency_key, parameters_json,
                        created_at, queued_at, updated_at
                    ) VALUES(?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id, session_id, mode, int(parameters.get("priority", 0)),
                        len(source_asset_ids), requested_concurrency, idempotency_key,
                        encode_json(parameters), now, now, now,
                    ),
                )
                draft = connection.execute(
                    "SELECT id, revision, ui_state_json FROM workflow_drafts WHERE mode = ?",
                    (mode,),
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO job_snapshots(
                        job_id, draft_id, draft_revision, mode,
                        source_asset_ids_json, brief_json, intent_json,
                        parameters_json, knowledge_refs_json, ui_context_json,
                        created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        draft["id"] if draft is not None else None,
                        int(draft["revision"]) if draft is not None else 0,
                        mode,
                        encode_json(source_asset_ids),
                        encode_json(brief),
                        encode_json(intent_locks),
                        encode_json(parameters),
                        encode_json(parameters.get("knowledge_refs") or []),
                        encode_json(
                            parameters.get("ui_context")
                            if isinstance(parameters.get("ui_context"), dict)
                            else decode_json(draft["ui_state_json"], {})
                            if draft is not None else {}
                        ),
                        now,
                    ),
                )
                for position, source_asset_id in enumerate(source_asset_ids):
                    item_id = new_id("item")
                    generation_id = new_id("gen")
                    adjustment = (
                        parameters.get("adjustment")
                        if isinstance(parameters.get("adjustment"), dict)
                        else {}
                    )
                    parent_generation_id = (
                        str(adjustment.get("parent_generation_id") or "").strip()
                        or None
                    )
                    connection.execute(
                        """
                        INSERT INTO generations(
                            id, session_id, task_id, parent_generation_id, model,
                            parameters_json, knowledge_refs_json, status, created_at
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, 'queued', ?)
                        """,
                        (
                            generation_id, session_id, item_id, parent_generation_id,
                            str(parameters.get("model", "")), encode_json(parameters),
                            encode_json(parameters.get("knowledge_refs") or []), now,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO job_items(
                            id, job_id, position, source_asset_id, generation_id,
                            engine_key, status, progress, attempt_count, max_attempts,
                            queued_at, updated_at
                        ) VALUES(?, ?, ?, ?, ?, ?, 'queued', 0, 0, ?, ?, ?)
                        """,
                        (
                            item_id, job_id, position, source_asset_id, generation_id,
                            engine_key, max_attempts, now, now,
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO events(session_id, event_type, payload_json, created_at)
                    VALUES(?, 'job.created', ?, ?)
                    """,
                    (
                        session_id,
                        encode_json({
                            "job_id": job_id,
                            "mode": mode,
                            "total_items": len(source_asset_ids),
                            "idempotency_key": idempotency_key,
                        }),
                        now,
                    ),
                )
        return self.get_job(job_id), created

    def get_job(self, job_id: str, *, include_attempts: bool = True) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT j.*, s.title AS session_title
                FROM jobs j JOIN sessions s ON s.id = j.session_id
                WHERE j.id = ?
                """,
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown job: {job_id}")
            job = self._job_row(row)
            item_rows = connection.execute(
                """
                SELECT ji.*, g.result_asset_ids_json, g.model AS generation_model
                FROM job_items ji
                LEFT JOIN generations g ON g.id = ji.generation_id
                WHERE ji.job_id = ? ORDER BY ji.position
                """,
                (job_id,),
            ).fetchall()
            items = [self._job_item_row(item_row) for item_row in item_rows]
            if include_attempts and items:
                attempt_rows = connection.execute(
                    """
                    SELECT ta.* FROM task_attempts ta
                    JOIN job_items ji ON ji.id = ta.job_item_id
                    WHERE ji.job_id = ?
                    ORDER BY ji.position, ta.attempt_number
                    """,
                    (job_id,),
                ).fetchall()
                attempts_by_item: dict[str, list[dict[str, Any]]] = {}
                for attempt_row in attempt_rows:
                    attempt = self._task_attempt_row(attempt_row)
                    attempts_by_item.setdefault(attempt["job_item_id"], []).append(attempt)
                for item in items:
                    item["attempts"] = attempts_by_item.get(item["id"], [])
            job["items"] = items
            snapshot_row = connection.execute(
                "SELECT * FROM job_snapshots WHERE job_id = ?", (job_id,)
            ).fetchone()
            job["snapshot"] = self._job_snapshot_row(snapshot_row) if snapshot_row else None
            job["progress"] = (
                sum(float(item["progress"]) for item in items) / len(items)
                if items else 0.0
            )
            return job

    def get_job_item(self, item_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT ji.*, g.result_asset_ids_json, g.model AS generation_model
                FROM job_items ji
                LEFT JOIN generations g ON g.id = ji.generation_id
                WHERE ji.id = ?
                """,
                (item_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown job item: {item_id}")
        return self._job_item_row(row)

    def list_jobs(self, limit: int = 100, *, mode: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        if mode is not None and mode not in WORKFLOW_DRAFT_IDS:
            raise ValueError(f"unsupported workflow mode: {mode}")
        with self._connection() as connection:
            if mode is None:
                rows = connection.execute(
                    "SELECT id FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT id FROM jobs WHERE mode = ? ORDER BY created_at DESC LIMIT ?",
                    (mode, limit),
                ).fetchall()
        return [self.get_job(str(row["id"]), include_attempts=False) for row in rows]

    def list_runnable_job_heads(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT ji.*, j.priority AS job_priority, j.queued_at AS job_queued_at,
                       j.requested_concurrency, j.status AS job_status
                FROM job_items ji
                JOIN jobs j ON j.id = ji.job_id
                WHERE ji.status = 'queued'
                  AND j.status IN ('queued', 'running')
                  AND ji.position = (
                      SELECT MIN(head.position) FROM job_items head
                      WHERE head.job_id = ji.job_id AND head.status = 'queued'
                  )
                ORDER BY j.priority DESC, j.queued_at ASC, j.id ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_job_item(self, item_id: str) -> dict[str, Any] | None:
        now = utc_now()
        with self._immediate_connection() as connection:
            row = connection.execute(
                """
                SELECT ji.*, j.session_id, j.status AS job_status,
                       j.parameters_json AS job_parameters_json
                FROM job_items ji JOIN jobs j ON j.id = ji.job_id
                WHERE ji.id = ?
                """,
                (item_id,),
            ).fetchone()
            if row is None or row["status"] != "queued" or row["job_status"] not in {"queued", "running"}:
                return None
            active_count = connection.execute(
                "SELECT COUNT(*) FROM job_items WHERE job_id = ? "
                "AND status IN ('running', 'canceling')",
                (row["job_id"],),
            ).fetchone()[0]
            requested_concurrency = connection.execute(
                "SELECT requested_concurrency FROM jobs WHERE id = ?",
                (row["job_id"],),
            ).fetchone()[0]
            if int(active_count) >= int(requested_concurrency):
                return None
            if int(row["attempt_count"]) >= int(row["max_attempts"]):
                return None
            validate_status_transition("queued", "running", item=True)
            attempt_number = int(row["attempt_count"]) + 1
            connection.execute(
                """
                UPDATE job_items
                SET status = 'running', progress = 0, attempt_count = ?,
                    started_at = ?, updated_at = ?, completed_at = NULL,
                    error_code = '', error_message = ''
                WHERE id = ?
                """,
                (attempt_number, now, now, item_id),
            )
            attempt_id = new_id("attempt")
            job_parameters = decode_json(row["job_parameters_json"], {})
            attempt_model = str(job_parameters.get("model", ""))
            connection.execute(
                """
                INSERT INTO task_attempts(
                    id, job_item_id, attempt_number, engine_key, model, status,
                    started_at
                ) VALUES(?, ?, ?, ?, ?, 'running', ?)
                """,
                (
                    attempt_id, item_id, attempt_number, str(row["engine_key"]),
                    attempt_model, now,
                ),
            )
            connection.execute(
                """
                UPDATE jobs SET status = 'running', started_at = COALESCE(started_at, ?),
                                updated_at = ? WHERE id = ?
                """,
                (now, now, row["job_id"]),
            )
            connection.execute(
                "UPDATE sessions SET status = 'processing', updated_at = ? WHERE id = ?",
                (now, row["session_id"]),
            )
            if row["generation_id"]:
                connection.execute(
                    "UPDATE generations SET status = 'running', error = '' WHERE id = ?",
                    (row["generation_id"],),
                )
            connection.execute(
                """
                INSERT INTO events(session_id, generation_id, event_type, payload_json, created_at)
                VALUES(?, ?, 'job.item.started', ?, ?)
                """,
                (
                    row["session_id"], row["generation_id"],
                    encode_json({"job_id": row["job_id"], "item_id": item_id, "attempt": attempt_number}),
                    now,
                ),
            )
        return {
            "job": self.get_job(str(row["job_id"]), include_attempts=False),
            "item": self.get_job_item(item_id),
            "attempt_id": attempt_id,
        }

    def update_job_item_progress(self, item_id: str, progress: float) -> dict[str, Any]:
        progress = max(0.0, min(float(progress), 0.999))
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE job_items SET progress = ?, updated_at = ?
                WHERE id = ? AND status IN ('running', 'canceling')
                """,
                (progress, utc_now(), item_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"job item is not active: {item_id}")
        return self.get_job_item(item_id)

    def update_task_attempt_metadata(
        self,
        item_id: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist recoverable attempt metadata while an item is active."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT attempt_count, status FROM job_items WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None or row["status"] not in {"running", "canceling"}:
                raise KeyError(f"job item is not active: {item_id}")
            cursor = connection.execute(
                "UPDATE task_attempts SET metadata_json = ? "
                "WHERE job_item_id = ? AND attempt_number = ? AND status = 'running'",
                (encode_json(metadata), item_id, row["attempt_count"]),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"active attempt is unavailable: {item_id}")
        return self.get_job_item(item_id)

    def commit_generation_results(
        self,
        generation_id: str,
        source_asset_id: str,
        outputs: Iterable[dict[str, Any]],
        *,
        job_item_id: str = "",
        attempt_metadata: Mapping[str, Any] | None = None,
    ) -> list[str]:
        """Atomically register outputs and, when supplied, finish their job item.

        Files are published before this transaction.  Keeping result rows,
        generation state, attempt history, item completion, and parent counters
        in one commit removes the restart window where durable results existed
        but the item could be requeued and charged a second time.
        """
        normalized = [dict(output) for output in outputs]
        if not normalized:
            raise ValueError("at least one generation output is required")
        now = utc_now()
        asset_ids = [new_id("ast") for _ in normalized]
        with self._immediate_connection() as connection:
            generation = connection.execute(
                "SELECT session_id, result_asset_ids_json FROM generations WHERE id = ?",
                (generation_id,),
            ).fetchone()
            if generation is None:
                raise KeyError(f"unknown generation: {generation_id}")
            source = connection.execute(
                "SELECT id FROM assets WHERE id = ?", (source_asset_id,)
            ).fetchone()
            if source is None:
                raise KeyError(f"unknown source asset: {source_asset_id}")
            lineage_parent_ids = [
                str(output.get("parent_asset_id") or source_asset_id)
                for output in normalized
            ]
            placeholders = ",".join("?" for _ in set(lineage_parent_ids))
            lineage_rows = connection.execute(
                f"SELECT id FROM assets WHERE id IN ({placeholders})",
                tuple(set(lineage_parent_ids)),
            ).fetchall()
            known_lineage_parents = {str(row["id"]) for row in lineage_rows}
            missing_lineage = [
                asset_id for asset_id in lineage_parent_ids
                if asset_id not in known_lineage_parents
            ]
            if missing_lineage:
                raise KeyError(f"unknown result lineage parent: {missing_lineage[0]}")
            job_item = None
            if job_item_id:
                job_item = connection.execute(
                    """
                    SELECT ji.*, j.session_id FROM job_items ji
                    JOIN jobs j ON j.id = ji.job_id
                    WHERE ji.id = ?
                    """,
                    (job_item_id,),
                ).fetchone()
                if job_item is None:
                    raise KeyError(f"unknown job item: {job_item_id}")
                if str(job_item["generation_id"] or "") != generation_id:
                    raise LedgerSchemaError("job item does not own the target generation")
                if str(job_item["source_asset_id"]) != source_asset_id:
                    raise LedgerSchemaError("job item source does not match result lineage")
                validate_status_transition(str(job_item["status"]), "completed", item=True)
            previous = decode_json(generation["result_asset_ids_json"], [])
            if previous:
                raise LedgerSchemaError(
                    f"generation already has committed results: {generation_id}"
                )
            for asset_id, output, lineage_parent_id in zip(
                asset_ids, normalized, lineage_parent_ids
            ):
                role = str(output.get("role", "result_main"))
                if role not in {"result_main", "result_cutout"}:
                    raise ValueError(f"unsupported generation result role: {role}")
                path = str(output.get("path", ""))
                if not path:
                    raise ValueError("generation output path is required")
                connection.execute(
                    """
                    INSERT INTO assets(
                        id, session_id, parent_asset_id, role, kind, path, name,
                        mime, width, height, sha256, metadata_json, created_at
                    ) VALUES(?, ?, ?, ?, 'image', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        asset_id,
                        generation["session_id"],
                        lineage_parent_id,
                        role,
                        path,
                        str(output.get("name", Path(path).name)),
                        str(output.get("mime", "image/png" if role == "result_cutout" else "image/jpeg")),
                        output.get("width"),
                        output.get("height"),
                        str(output.get("sha256", "")),
                        encode_json({
                            **(output.get("metadata") if isinstance(output.get("metadata"), dict) else {}),
                            "generation_id": generation_id,
                            "job_item_id": job_item_id,
                        }),
                        now,
                    ),
                )
            connection.execute(
                "UPDATE generations SET result_asset_ids_json = ? WHERE id = ?",
                (encode_json(asset_ids), generation_id),
            )
            if job_item is not None:
                started = connection.execute(
                    """
                    SELECT started_at FROM task_attempts
                    WHERE job_item_id = ? AND attempt_number = ?
                    """,
                    (job_item_id, job_item["attempt_count"]),
                ).fetchone()
                latency_ms = None
                if started is not None:
                    try:
                        start_dt = datetime.fromisoformat(str(started["started_at"]))
                        latency_ms = max(
                            0,
                            int(
                                (datetime.now(timezone.utc) - start_dt).total_seconds()
                                * 1000
                            ),
                        )
                    except (TypeError, ValueError):
                        latency_ms = None
                final_metadata = {
                    **dict(attempt_metadata or {}),
                    "result_asset_ids": list(asset_ids),
                    "output_count": len(asset_ids),
                }
                connection.execute(
                    """
                    UPDATE task_attempts
                    SET status = 'completed', error_code = '', error_message = '',
                        latency_ms = ?, metadata_json = ?, completed_at = ?
                    WHERE job_item_id = ? AND attempt_number = ? AND status = 'running'
                    """,
                    (
                        latency_ms,
                        encode_json(final_metadata),
                        now,
                        job_item_id,
                        job_item["attempt_count"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE job_items
                    SET status = 'completed', progress = 1, error_code = '',
                        error_message = '', updated_at = ?, completed_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (now, now, job_item_id),
                )
                connection.execute(
                    """
                    UPDATE generations
                    SET status = 'completed', error = '', completed_at = ?
                    WHERE id = ?
                    """,
                    (now, generation_id),
                )
                parent_status = self._refresh_job_aggregate(
                    connection, str(job_item["job_id"])
                )
                connection.execute(
                    """
                    INSERT INTO events(
                        session_id, generation_id, event_type, payload_json, created_at
                    ) VALUES(?, ?, 'job.item.finished', ?, ?)
                    """,
                    (
                        job_item["session_id"],
                        generation_id,
                        encode_json({
                            "job_id": job_item["job_id"],
                            "item_id": job_item_id,
                            "status": "completed",
                            "parent_status": parent_status,
                            "error_code": "",
                        }),
                        now,
                    ),
                )
            connection.execute(
                """
                INSERT INTO events(session_id, generation_id, event_type, payload_json, created_at)
                VALUES(?, ?, 'generation.results.committed', ?, ?)
                """,
                (
                    generation["session_id"], generation_id,
                    encode_json({"asset_ids": asset_ids, "job_item_id": job_item_id}), now,
                ),
            )
        return asset_ids

    def discard_generation_results(
        self,
        generation_id: str,
        asset_ids: Iterable[str],
        *,
        reason: str = "attempt_discarded",
    ) -> int:
        """Remove only the named attempt outputs after cancel/commit rollback."""
        requested = [str(asset_id) for asset_id in asset_ids]
        if not requested:
            return 0
        now = utc_now()
        with self._immediate_connection() as connection:
            generation = connection.execute(
                "SELECT session_id, result_asset_ids_json FROM generations WHERE id = ?",
                (generation_id,),
            ).fetchone()
            if generation is None:
                return 0
            current = decode_json(generation["result_asset_ids_json"], [])
            removable = [asset_id for asset_id in requested if asset_id in current]
            if not removable:
                return 0
            placeholders = ",".join("?" for _ in removable)
            cursor = connection.execute(
                f"DELETE FROM assets WHERE session_id = ? AND id IN ({placeholders})",
                (generation["session_id"], *removable),
            )
            remaining = [asset_id for asset_id in current if asset_id not in set(removable)]
            connection.execute(
                "UPDATE generations SET result_asset_ids_json = ? WHERE id = ?",
                (encode_json(remaining), generation_id),
            )
            connection.execute(
                """
                INSERT INTO events(session_id, generation_id, event_type, payload_json, created_at)
                VALUES(?, ?, 'generation.results.discarded', ?, ?)
                """,
                (
                    generation["session_id"], generation_id,
                    encode_json({"asset_ids": removable, "reason": reason}), now,
                ),
            )
            return int(cursor.rowcount)

    @staticmethod
    def _refresh_job_aggregate(connection: sqlite3.Connection, job_id: str) -> str:
        job = connection.execute(
            "SELECT session_id, status FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if job is None:
            raise KeyError(f"unknown job: {job_id}")
        rows = connection.execute(
            "SELECT status, COUNT(*) AS count FROM job_items WHERE job_id = ? GROUP BY status",
            (job_id,),
        ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        total = sum(counts.values())
        completed = counts.get("completed", 0)
        failed = counts.get("failed", 0)
        canceled = counts.get("canceled", 0)
        queued = counts.get("queued", 0)
        running = counts.get("running", 0)
        canceling = counts.get("canceling", 0)
        interrupted = counts.get("interrupted", 0)

        if canceling:
            status = "canceling"
        elif str(job["status"]) == "paused" and (running or queued or interrupted):
            # Pausing stops new claims; an already-running item may still reach
            # a safe terminal point. Preserve the pause while work remains.
            status = "paused"
        elif running:
            status = "running"
        elif queued:
            status = "running" if completed or failed or canceled else "queued"
        elif interrupted:
            status = "interrupted"
        elif total and completed == total:
            status = "completed"
        elif total and canceled == total:
            status = "canceled"
        elif total and failed == total:
            status = "failed"
        elif total:
            status = "partial"
        else:
            status = "failed"

        now = utc_now()
        validate_status_transition(str(job["status"]), status)
        terminal = status in {"completed", "partial", "failed", "canceled"}
        connection.execute(
            """
            UPDATE jobs
            SET status = ?, total_items = ?, completed_items = ?, failed_items = ?,
                canceled_items = ?, updated_at = ?,
                completed_at = CASE WHEN ? THEN ? ELSE NULL END
            WHERE id = ?
            """,
            (
                status, total, completed, failed, canceled, now,
                1 if terminal else 0, now, job_id,
            ),
        )
        session_status = {
            "queued": "queued",
            "running": "processing",
            "paused": "paused",
            "canceling": "processing",
            "interrupted": "interrupted",
            "completed": "completed",
            "partial": "partial",
            "failed": "error",
            "canceled": "canceled",
        }[status]
        connection.execute(
            """
            UPDATE sessions SET status = ?, updated_at = ?,
                completed_at = CASE WHEN ? THEN ? ELSE NULL END
            WHERE id = ?
            """,
            (session_status, now, 1 if terminal else 0, now, job["session_id"]),
        )
        return status

    def finish_job_item(
        self,
        item_id: str,
        status: str,
        *,
        error_code: str = "",
        error_message: str = "",
        attempt_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in {"completed", "failed", "canceled", "interrupted"}:
            raise ValueError(f"unsupported terminal item status: {status}")
        now = utc_now()
        row = None
        try:
            with self._immediate_connection() as connection:
                row = connection.execute(
                    """
                    SELECT ji.*, j.session_id FROM job_items ji
                    JOIN jobs j ON j.id = ji.job_id WHERE ji.id = ?
                    """,
                    (item_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"unknown job item: {item_id}")
                validate_status_transition(str(row["status"]), status, item=True)
                # progress is queue completion, not success quality: every
                # terminal item has finished consuming its slot and is 100%
                # settled even when its outcome is failed/canceled.
                progress = (
                    1.0
                    if status in {"completed", "failed", "canceled"}
                    else float(row["progress"])
                )
                connection.execute(
                    """
                    UPDATE job_items
                    SET status = ?, progress = ?, error_code = ?, error_message = ?,
                        updated_at = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (status, progress, error_code, error_message, now, now, item_id),
                )
                started = connection.execute(
                    """
                    SELECT started_at FROM task_attempts
                    WHERE job_item_id = ? AND attempt_number = ?
                    """,
                    (item_id, row["attempt_count"]),
                ).fetchone()
                latency_ms = None
                if started is not None:
                    try:
                        start_dt = datetime.fromisoformat(str(started["started_at"]))
                        latency_ms = max(0, int((datetime.now(timezone.utc) - start_dt).total_seconds() * 1000))
                    except (TypeError, ValueError):
                        latency_ms = None
                connection.execute(
                    """
                    UPDATE task_attempts
                    SET status = ?, error_code = ?, error_message = ?, latency_ms = ?,
                        metadata_json = ?, completed_at = ?
                    WHERE job_item_id = ? AND attempt_number = ?
                    """,
                    (
                        status, error_code, error_message, latency_ms,
                        encode_json(attempt_metadata), now, item_id, row["attempt_count"],
                    ),
                )
                if row["generation_id"]:
                    generation_status = "error" if status == "failed" else status
                    connection.execute(
                        """
                        UPDATE generations SET status = ?, error = ?, completed_at = ?
                        WHERE id = ?
                        """,
                        (generation_status, error_message, now, row["generation_id"]),
                    )
                parent_status = self._refresh_job_aggregate(connection, str(row["job_id"]))
                connection.execute(
                    """
                    INSERT INTO events(session_id, generation_id, event_type, payload_json, created_at)
                    VALUES(?, ?, 'job.item.finished', ?, ?)
                    """,
                    (
                        row["session_id"], row["generation_id"],
                        encode_json({
                            "job_id": row["job_id"], "item_id": item_id,
                            "status": status, "parent_status": parent_status,
                            "error_code": error_code,
                        }),
                        now,
                    ),
                )
        except InvalidStatusTransitionError:
            if row is not None:
                self.add_event(
                    str(row["session_id"]),
                    "job.transition_rejected",
                    {
                        "entity": "job_item",
                        "job_id": str(row["job_id"]),
                        "item_id": item_id,
                        "from": str(row["status"]),
                        "to": status,
                    },
                    generation_id=row["generation_id"],
                )
            raise
        return self.get_job(str(row["job_id"]))

    def pause_job(self, job_id: str) -> dict[str, Any]:
        """Stop future claims while allowing already-running items to settle."""
        now = utc_now()
        with self._immediate_connection() as connection:
            job = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if job is None:
                raise KeyError(f"unknown job: {job_id}")
            current = str(job["status"])
            if current in {"completed", "partial", "failed", "canceled", "paused"}:
                pass
            elif current not in {"queued", "running"}:
                raise InvalidStatusTransitionError(
                    f"job cannot be paused while {current}"
                )
            else:
                validate_status_transition(current, "paused")
                connection.execute(
                    "UPDATE jobs SET status = 'paused', updated_at = ? WHERE id = ?",
                    (now, job_id),
                )
                connection.execute(
                    "UPDATE sessions SET status = 'paused', updated_at = ? WHERE id = ?",
                    (now, job["session_id"]),
                )
                connection.execute(
                    """
                    INSERT INTO events(session_id, event_type, payload_json, created_at)
                    VALUES(?, 'job.paused', ?, ?)
                    """,
                    (job["session_id"], encode_json({"job_id": job_id}), now),
                )
        return self.get_job(job_id)

    def resume_job(self, job_id: str) -> dict[str, Any]:
        """Resume claims for a paused job without changing any item attempt."""
        now = utc_now()
        with self._immediate_connection() as connection:
            job = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if job is None:
                raise KeyError(f"unknown job: {job_id}")
            current = str(job["status"])
            if current != "paused":
                if current in {"completed", "partial", "failed", "canceled"}:
                    pass
                else:
                    raise InvalidStatusTransitionError(
                        f"job cannot be resumed while {current}"
                    )
            else:
                rows = connection.execute(
                    "SELECT status, COUNT(*) AS count FROM job_items "
                    "WHERE job_id = ? GROUP BY status",
                    (job_id,),
                ).fetchall()
                counts = {str(row["status"]): int(row["count"]) for row in rows}
                settled = sum(
                    counts.get(status, 0)
                    for status in ("completed", "failed", "canceled")
                )
                if counts.get("running", 0):
                    target = "running"
                elif counts.get("queued", 0):
                    target = "running" if settled else "queued"
                elif counts.get("interrupted", 0):
                    target = "interrupted"
                else:
                    self._refresh_job_aggregate(connection, job_id)
                    target = None
                if target is not None:
                    validate_status_transition("paused", target)
                    connection.execute(
                        "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                        (target, now, job_id),
                    )
                    connection.execute(
                        "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
                        (
                            "processing" if target == "running" else target,
                            now,
                            job["session_id"],
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO events(session_id, event_type, payload_json, created_at)
                        VALUES(?, 'job.resumed', ?, ?)
                        """,
                        (
                            job["session_id"],
                            encode_json({"job_id": job_id, "status": target}),
                            now,
                        ),
                    )
        return self.get_job(job_id)

    def request_job_cancel(self, job_id: str) -> dict[str, Any]:
        now = utc_now()
        canceling_item_ids: list[str] = []
        already_terminal = False
        with self._immediate_connection() as connection:
            job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job is None:
                raise KeyError(f"unknown job: {job_id}")
            if job["status"] in {"completed", "partial", "failed", "canceled"}:
                already_terminal = True
            else:
                items = connection.execute(
                    "SELECT id, status, generation_id FROM job_items WHERE job_id = ?",
                    (job_id,),
                ).fetchall()
                for item in items:
                    current = str(item["status"])
                    if current == "queued":
                        validate_status_transition(current, "canceled", item=True)
                        connection.execute(
                            """
                            UPDATE job_items SET status = 'canceled', progress = 1,
                                                 updated_at = ?, completed_at = ? WHERE id = ?
                            """,
                            (now, now, item["id"]),
                        )
                        if item["generation_id"]:
                            connection.execute(
                                "UPDATE generations SET status = 'canceled', completed_at = ? WHERE id = ?",
                                (now, item["generation_id"]),
                            )
                    elif current == "running":
                        validate_status_transition(current, "canceling", item=True)
                        connection.execute(
                            "UPDATE job_items SET status = 'canceling', updated_at = ? WHERE id = ?",
                            (now, item["id"]),
                        )
                        if item["generation_id"]:
                            connection.execute(
                                "UPDATE generations SET status = 'canceling' WHERE id = ?",
                                (item["generation_id"],),
                            )
                        canceling_item_ids.append(str(item["id"]))
                    elif current == "canceling":
                        canceling_item_ids.append(str(item["id"]))
                    elif current == "interrupted":
                        validate_status_transition(current, "canceled", item=True)
                        connection.execute(
                            """
                            UPDATE job_items SET status = 'canceled', progress = 1,
                                                 updated_at = ?, completed_at = ? WHERE id = ?
                            """,
                            (now, now, item["id"]),
                        )
                        if item["generation_id"]:
                            connection.execute(
                                "UPDATE generations SET status = 'canceled', completed_at = ? WHERE id = ?",
                                (now, item["generation_id"]),
                            )
                self._refresh_job_aggregate(connection, job_id)
                connection.execute(
                    """
                    INSERT INTO events(session_id, event_type, payload_json, created_at)
                    VALUES(?, 'job.cancel.requested', ?, ?)
                    """,
                    (job["session_id"], encode_json({"job_id": job_id}), now),
                )
        result = self.get_job(job_id)
        if not already_terminal:
            result["canceling_item_ids"] = canceling_item_ids
        return result

    def retry_job_items(self, job_id: str, item_ids: Iterable[str] | None = None) -> dict[str, Any]:
        requested = {str(item_id) for item_id in (item_ids or [])}
        now = utc_now()
        retried: list[str] = []
        with self._immediate_connection() as connection:
            job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job is None:
                raise KeyError(f"unknown job: {job_id}")
            rows = connection.execute(
                "SELECT * FROM job_items WHERE job_id = ? ORDER BY position", (job_id,)
            ).fetchall()
            known = {str(row["id"]) for row in rows}
            if requested - known:
                raise KeyError(f"unknown job items: {', '.join(sorted(requested - known))}")
            for row in rows:
                item_id = str(row["id"])
                if requested and item_id not in requested:
                    continue
                current = str(row["status"])
                if current not in {"failed", "interrupted"}:
                    continue
                validate_status_transition(current, "queued", item=True)
                connection.execute(
                    """
                    UPDATE job_items
                    SET status = 'queued', progress = 0, error_code = '', error_message = '',
                        max_attempts = MAX(max_attempts, attempt_count + 1), queued_at = ?,
                        started_at = NULL, updated_at = ?, completed_at = NULL
                    WHERE id = ?
                    """,
                    (now, now, item_id),
                )
                if row["generation_id"]:
                    connection.execute(
                        """
                        UPDATE generations SET status = 'queued', error = '', completed_at = NULL
                        WHERE id = ?
                        """,
                        (row["generation_id"],),
                    )
                retried.append(item_id)
            if not retried:
                raise ValueError("no failed or interrupted items are eligible for retry")
            self._refresh_job_aggregate(connection, job_id)
            connection.execute(
                """
                INSERT INTO events(session_id, event_type, payload_json, created_at)
                VALUES(?, 'job.retry.requested', ?, ?)
                """,
                (job["session_id"], encode_json({"job_id": job_id, "item_ids": retried}), now),
            )
        result = self.get_job(job_id)
        result["retried_item_ids"] = retried
        return result

    def recover_orphaned_job_item(
        self,
        item_id: str,
        *,
        error_code: str = "WORKER_INFRASTRUCTURE_FAILURE",
        error_message: str = "Worker stopped before persisting a terminal state",
    ) -> dict[str, Any]:
        """Reconcile one finished worker without disturbing other live owners.

        This is the in-process counterpart to startup recovery.  It is targeted
        deliberately: calling ``recover_interrupted_jobs`` while sibling workers
        are still alive would steal their durable ``running`` claims.
        """
        now = utc_now()
        with self._immediate_connection() as connection:
            row = connection.execute(
                """
                SELECT ji.*, j.session_id, j.status AS job_status FROM job_items ji
                JOIN jobs j ON j.id = ji.job_id
                WHERE ji.id = ?
                """,
                (item_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown job item: {item_id}")
            current = str(row["status"])
            if current not in {"running", "canceling"}:
                return {
                    "item_id": item_id,
                    "status": current,
                    "recovered": False,
                }

            was_canceling = current == "canceling"
            can_retry = (
                not was_canceling
                and int(row["attempt_count"]) < int(row["max_attempts"])
            )
            next_status = "canceled" if was_canceling else ("queued" if can_retry else "failed")
            final_code = "USER_CANCELED" if was_canceling else str(error_code)
            final_message = "Canceled by user" if was_canceling else str(error_message)

            if was_canceling:
                validate_status_transition("canceling", "canceled", item=True)
            else:
                validate_status_transition("running", "interrupted", item=True)
                validate_status_transition("interrupted", next_status, item=True)
                if str(row["job_status"]) == "running":
                    validate_status_transition("running", "interrupted")
                    connection.execute(
                        "UPDATE jobs SET status = 'interrupted', updated_at = ? WHERE id = ?",
                        (now, row["job_id"]),
                    )

            connection.execute(
                """
                UPDATE task_attempts
                SET status = 'interrupted', error_code = ?, error_message = ?, completed_at = ?
                WHERE job_item_id = ? AND attempt_number = ? AND status = 'running'
                """,
                (final_code, final_message, now, item_id, row["attempt_count"]),
            )
            if not was_canceling:
                connection.execute(
                    """
                    UPDATE job_items
                    SET status = 'interrupted', error_code = ?, error_message = ?,
                        started_at = NULL, updated_at = ?, completed_at = NULL
                    WHERE id = ? AND status = 'running'
                    """,
                    (final_code, final_message, now, item_id),
                )
                if row["generation_id"]:
                    connection.execute(
                        "UPDATE generations SET status = 'interrupted', error = ? WHERE id = ?",
                        (final_message, row["generation_id"]),
                    )
            connection.execute(
                """
                UPDATE job_items
                SET status = ?, progress = CASE
                        WHEN ? = 'queued' THEN 0
                        WHEN ? IN ('failed','canceled') THEN 1
                        ELSE progress
                    END,
                    error_code = ?, error_message = ?,
                    queued_at = CASE WHEN ? = 'queued' THEN ? ELSE queued_at END,
                    started_at = NULL, updated_at = ?,
                    completed_at = CASE WHEN ? IN ('failed','canceled') THEN ? ELSE NULL END
                WHERE id = ? AND status IN ('interrupted','canceling')
                """,
                (
                    next_status, next_status, next_status, final_code, final_message,
                    next_status, now, now, next_status, now, item_id,
                ),
            )
            if row["generation_id"]:
                generation_status = "error" if next_status == "failed" else next_status
                connection.execute(
                    "UPDATE generations SET status = ?, error = ? WHERE id = ?",
                    (generation_status, final_message, row["generation_id"]),
                )
            parent_status = self._refresh_job_aggregate(connection, str(row["job_id"]))
            connection.execute(
                """
                INSERT INTO events(session_id, generation_id, event_type, payload_json, created_at)
                VALUES(?, ?, 'job.item.interrupted', ?, ?)
                """,
                (
                    row["session_id"], row["generation_id"],
                    encode_json({
                        "job_id": row["job_id"],
                        "item_id": item_id,
                        "next_status": next_status,
                        "error_code": final_code,
                        "live_reconciliation": True,
                    }),
                    now,
                ),
            )
        return {
            "item_id": item_id,
            "status": next_status,
            "parent_status": parent_status,
            "recovered": True,
        }

    def recover_interrupted_jobs(self) -> dict[str, int]:
        now = utc_now()
        interrupted_count = 0
        requeued_count = 0
        failed_count = 0
        affected_jobs: set[str] = set()
        with self._immediate_connection() as connection:
            rows = connection.execute(
                """
                SELECT ji.*, j.session_id, j.status AS job_status FROM job_items ji
                JOIN jobs j ON j.id = ji.job_id
                WHERE ji.status IN ('running', 'canceling')
                """
            ).fetchall()
            for row in rows:
                interrupted_count += 1
                affected_jobs.add(str(row["job_id"]))
                current_item_status = str(row["status"])
                if current_item_status == "running":
                    validate_status_transition("running", "interrupted", item=True)
                else:
                    validate_status_transition("canceling", "canceled", item=True)
                if str(row["job_status"]) == "running":
                    validate_status_transition("running", "interrupted")
                    connection.execute(
                        "UPDATE jobs SET status = 'interrupted', updated_at = ? WHERE id = ?",
                        (now, row["job_id"]),
                    )
                connection.execute(
                    """
                    UPDATE task_attempts
                    SET status = 'interrupted', error_code = 'PROCESS_RESTARTED',
                        error_message = 'Worker process stopped before completion', completed_at = ?
                    WHERE job_item_id = ? AND attempt_number = ? AND status = 'running'
                    """,
                    (now, row["id"], row["attempt_count"]),
                )
                was_canceling = str(row["status"]) == "canceling"
                can_retry = (
                    not was_canceling
                    and int(row["attempt_count"]) < int(row["max_attempts"])
                )
                next_status = "canceled" if was_canceling else ("queued" if can_retry else "failed")
                if not was_canceling:
                    validate_status_transition("interrupted", next_status, item=True)
                if next_status == "queued":
                    requeued_count += 1
                elif next_status == "canceled":
                    pass
                else:
                    failed_count += 1
                connection.execute(
                    """
                    UPDATE job_items
                    SET status = ?, progress = CASE
                            WHEN ? = 'queued' THEN 0
                            WHEN ? IN ('failed','canceled') THEN 1
                            ELSE progress
                        END,
                        error_code = 'PROCESS_RESTARTED',
                        error_message = 'Previous attempt was interrupted by a process restart',
                        queued_at = CASE WHEN ? = 'queued' THEN ? ELSE queued_at END,
                        started_at = NULL, updated_at = ?,
                        completed_at = CASE WHEN ? IN ('failed','canceled') THEN ? ELSE NULL END
                    WHERE id = ?
                    """,
                    (
                        next_status,
                        next_status,
                        next_status,
                        next_status,
                        now,
                        now,
                        next_status,
                        now,
                        row["id"],
                    ),
                )
                if row["generation_id"]:
                    generation_status = "error" if next_status == "failed" else next_status
                    connection.execute(
                        "UPDATE generations SET status = ?, error = ? WHERE id = ?",
                        (generation_status, "Previous attempt was interrupted by a process restart", row["generation_id"]),
                    )
                connection.execute(
                    """
                    INSERT INTO events(session_id, generation_id, event_type, payload_json, created_at)
                    VALUES(?, ?, 'job.item.interrupted', ?, ?)
                    """,
                    (
                        row["session_id"], row["generation_id"],
                        encode_json({"job_id": row["job_id"], "item_id": row["id"], "next_status": next_status}),
                        now,
                    ),
                )
            for job_id in affected_jobs:
                self._refresh_job_aggregate(connection, job_id)
        return {
            "interrupted": interrupted_count,
            "requeued": requeued_count,
            "failed": failed_count,
        }

    def add_generation(
        self,
        session_id: str,
        *,
        task_id: str = "",
        parent_generation_id: str | None = None,
        model: str = "",
        prompt: str = "",
        negative_prompt: str = "",
        parameters: dict[str, Any] | None = None,
        knowledge_refs: Iterable[dict[str, Any] | str] | None = None,
        prompt_version: str = "v1",
        status: str = "queued",
    ) -> dict[str, Any]:
        generation_id = new_id("gen")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO generations(
                    id, session_id, task_id, parent_generation_id, model, prompt,
                    negative_prompt, parameters_json, knowledge_refs_json,
                    prompt_version, status, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    generation_id, session_id, task_id, parent_generation_id, model,
                    prompt, negative_prompt, encode_json(parameters),
                    encode_json(list(knowledge_refs or [])), prompt_version, status, utc_now(),
                ),
            )
        self.add_event(
            session_id,
            "generation.created",
            {"generation_id": generation_id, "task_id": task_id, "model": model},
            generation_id=generation_id,
        )
        return self.get_generation(generation_id)

    def update_generation(self, generation_id: str, **changes: Any) -> dict[str, Any]:
        generation = self.get_generation(generation_id)
        allowed = {
            "task_id", "model", "prompt", "negative_prompt", "prompt_version",
            "status", "error", "latency_ms", "estimated_cost", "completed_at",
        }
        json_fields = {
            "parameters": "parameters_json",
            "knowledge_refs": "knowledge_refs_json",
            "result_asset_ids": "result_asset_ids_json",
        }
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            if key in allowed:
                assignments.append(f"{key} = ?")
                values.append(value)
            elif key in json_fields:
                assignments.append(f"{json_fields[key]} = ?")
                values.append(encode_json(value))
        if not assignments:
            return generation
        values.append(generation_id)
        with self._connection() as connection:
            connection.execute(
                f"UPDATE generations SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
        self.add_event(
            generation["session_id"],
            "generation.updated",
            {"generation_id": generation_id, "changes": sorted(changes)},
            generation_id=generation_id,
        )
        return self.get_generation(generation_id)

    def add_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        generation_id: str | None = None,
    ) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO events(session_id, generation_id, event_type, payload_json, created_at) VALUES(?, ?, ?, ?, ?)",
                (session_id, generation_id, event_type, encode_json(payload), utc_now()),
            )
            connection.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (utc_now(), session_id))
            return int(cursor.lastrowid)

    def add_feedback(
        self,
        session_id: str,
        signal: str,
        *,
        generation_id: str | None = None,
        asset_id: str | None = None,
        reason: str = "",
        structured: dict[str, Any] | None = None,
        scope: str = "session",
        feedback_id: str | None = None,
    ) -> dict[str, Any]:
        feedback_id = str(feedback_id or new_id("fb"))
        candidate = {
            "id": feedback_id,
            "session_id": str(session_id),
            "generation_id": generation_id,
            "asset_id": asset_id,
            "signal": str(signal),
            "reason": str(reason),
            "structured": dict(structured or {}),
            "scope": str(scope),
        }
        now = utc_now()
        with self._immediate_connection() as connection:
            existing_row = connection.execute(
                "SELECT * FROM feedback WHERE id = ?", (feedback_id,)
            ).fetchone()
            if existing_row is not None:
                existing = self._feedback_row(existing_row)
                comparable = {key: existing.get(key) for key in candidate}
                if comparable != candidate:
                    raise IdempotencyConflictError(
                        "feedback id already belongs to different evidence"
                    )
                return existing
            connection.execute(
                """
                INSERT INTO feedback(
                    id, session_id, generation_id, asset_id, signal, reason,
                    structured_json, scope, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id, candidate["session_id"], generation_id, asset_id,
                    candidate["signal"], candidate["reason"],
                    encode_json(candidate["structured"]), candidate["scope"], now,
                ),
            )
            connection.execute(
                """
                INSERT INTO events(
                    session_id, generation_id, event_type, payload_json, created_at
                ) VALUES(?, ?, 'feedback.recorded', ?, ?)
                """,
                (
                    candidate["session_id"], generation_id,
                    encode_json({
                        "feedback_id": feedback_id,
                        "signal": candidate["signal"],
                        "scope": candidate["scope"],
                        "review_id": candidate["structured"].get("review_id"),
                    }),
                    now,
                ),
            )
            connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, candidate["session_id"]),
            )
            row = connection.execute(
                "SELECT * FROM feedback WHERE id = ?", (feedback_id,)
            ).fetchone()
        assert row is not None
        return self._feedback_row(row)

    def get_feedback(self, feedback_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM feedback WHERE id = ?", (feedback_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown feedback: {feedback_id}")
        return self._feedback_row(row)

    def list_feedback(self, limit: int = 500) -> list[dict[str, Any]]:
        """Return recent explicit feedback with the session scope needed for synthesis."""
        limit = max(1, min(int(limit), 2000))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT f.*, s.mode, s.designer_profile, s.brand_profile, s.category
                FROM feedback f
                JOIN sessions s ON s.id = f.session_id
                ORDER BY f.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._feedback_row(row) for row in rows]

    def record_execution_trace(
        self,
        client_request_id: str,
        *,
        stage: str,
        status: str,
        job_id: str | None = None,
        job_item_id: str | None = None,
        generation_id: str | None = None,
        user_input: Mapping[str, Any] | None = None,
        compiled_prompt: str = "",
        applied_knowledge: Iterable[Any] | None = None,
        ignored_fields: Iterable[Any] | None = None,
        model: str = "",
        parameters: Mapping[str, Any] | None = None,
        output: Mapping[str, Any] | None = None,
        error_code: str = "",
        error_message: str = "",
    ) -> dict[str, Any]:
        """Persist one explainable execution stage with retry-safe identity."""
        trace_id = idempotent_id("trace", client_request_id)
        stage = str(stage).strip()
        if not stage:
            raise ValueError("trace stage is required")
        if status not in {"started", "completed", "failed", "skipped"}:
            raise ValueError(f"unsupported trace status: {status}")
        candidate = {
            "id": trace_id,
            "job_id": job_id,
            "job_item_id": job_item_id,
            "generation_id": generation_id,
            "stage": stage,
            "status": status,
            "user_input": dict(user_input or {}),
            "compiled_prompt": str(compiled_prompt),
            "applied_knowledge": list(applied_knowledge or []),
            "ignored_fields": list(ignored_fields or []),
            "model": str(model),
            "parameters": dict(parameters or {}),
            "output": dict(output or {}),
            "error_code": str(error_code),
            "error_message": str(error_message),
        }
        with self._immediate_connection() as connection:
            existing_row = connection.execute(
                "SELECT * FROM execution_traces WHERE id = ?", (trace_id,)
            ).fetchone()
            if existing_row is not None:
                existing = self._trace_row(existing_row)
                comparable = {key: existing.get(key) for key in candidate}
                if comparable != candidate:
                    raise IdempotencyConflictError(
                        "client_request_id already belongs to a different execution trace"
                    )
                return existing
            if job_id:
                if connection.execute(
                    "SELECT 1 FROM jobs WHERE id = ?", (job_id,)
                ).fetchone() is None:
                    raise KeyError(f"unknown job: {job_id}")
            if job_item_id:
                item = connection.execute(
                    "SELECT job_id, generation_id FROM job_items WHERE id = ?",
                    (job_item_id,),
                ).fetchone()
                if item is None:
                    raise KeyError(f"unknown job item: {job_item_id}")
                if job_id and str(item["job_id"]) != job_id:
                    raise ValueError("trace job item does not belong to its job")
                if generation_id and str(item["generation_id"] or "") != generation_id:
                    raise ValueError("trace generation does not belong to its job item")
            elif generation_id:
                generation = connection.execute(
                    """
                    SELECT ji.job_id FROM job_items ji
                    WHERE ji.generation_id = ?
                    """,
                    (generation_id,),
                ).fetchone()
                if generation is None:
                    raise KeyError(f"unknown job generation: {generation_id}")
                if job_id and str(generation["job_id"]) != job_id:
                    raise ValueError("trace generation does not belong to its job")
            now = utc_now()
            connection.execute(
                """
                INSERT INTO execution_traces(
                    id, job_id, job_item_id, generation_id, stage, status,
                    user_input_json, compiled_prompt, applied_knowledge_json,
                    ignored_fields_json, model, parameters_json, output_json,
                    error_code, error_message, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id, job_id, job_item_id, generation_id, stage, status,
                    encode_json(candidate["user_input"]), candidate["compiled_prompt"],
                    encode_json(candidate["applied_knowledge"]),
                    encode_json(candidate["ignored_fields"]), candidate["model"],
                    encode_json(candidate["parameters"]), encode_json(candidate["output"]),
                    candidate["error_code"], candidate["error_message"], now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM execution_traces WHERE id = ?", (trace_id,)
            ).fetchone()
        assert row is not None
        return self._trace_row(row)

    def list_execution_traces(self, job_id: str, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM execution_traces WHERE job_id = ? "
                "ORDER BY created_at ASC, id ASC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        return [self._trace_row(row) for row in rows]

    def submit_result_review(
        self,
        client_request_id: str,
        *,
        result_asset_id: str,
        decision: str,
        job_id: str | None = None,
        generation_id: str | None = None,
        reason_codes: Iterable[str] | None = None,
        note: str = "",
        learning_action: str = "none",
    ) -> dict[str, Any]:
        """Record a result judgment without conflating it with learning scope."""
        review_id = idempotent_id("review", client_request_id)
        if decision not in {"adopt", "adjust", "reject"}:
            raise ValueError(f"unsupported review decision: {decision}")
        if learning_action not in {"none", "record", "regenerate", "suggest"}:
            raise ValueError(f"unsupported learning action: {learning_action}")
        normalized_reasons = [str(reason).strip() for reason in reason_codes or [] if str(reason).strip()]
        if len(normalized_reasons) > 20:
            raise ValueError("a result review accepts at most 20 reason codes")
        candidate = {
            "id": review_id,
            "job_id": job_id,
            "generation_id": generation_id,
            "result_asset_id": str(result_asset_id),
            "decision": decision,
            "reason_codes": normalized_reasons,
            "note": str(note).strip(),
            "learning_action": learning_action,
            "status": "submitted",
        }
        with self._immediate_connection() as connection:
            existing_row = connection.execute(
                "SELECT * FROM result_reviews WHERE id = ?", (review_id,)
            ).fetchone()
            if existing_row is not None:
                existing = self._review_row(existing_row)
                comparable = {key: existing.get(key) for key in candidate}
                if comparable != candidate:
                    raise IdempotencyConflictError(
                        "client_request_id already belongs to a different result review"
                    )
                return existing
            asset = connection.execute(
                "SELECT role FROM assets WHERE id = ?", (result_asset_id,)
            ).fetchone()
            if asset is None or str(asset["role"]) not in {"result_main", "result_cutout"}:
                raise KeyError(f"unknown result asset: {result_asset_id}")
            if job_id:
                generations = connection.execute(
                    """
                    SELECT g.id, g.result_asset_ids_json
                    FROM generations g
                    JOIN job_items ji ON ji.generation_id = g.id
                    WHERE ji.job_id = ?
                    """,
                    (job_id,),
                ).fetchall()
                if not generations:
                    raise KeyError(f"unknown job: {job_id}")
                matching = [
                    row for row in generations
                    if result_asset_id in decode_json(row["result_asset_ids_json"], [])
                ]
                if not matching:
                    raise ValueError("review result does not belong to its job")
                if generation_id and all(
                    str(row["id"]) != generation_id for row in matching
                ):
                    raise ValueError("review result does not belong to its generation")
            now = utc_now()
            connection.execute(
                """
                INSERT INTO result_reviews(
                    id, job_id, generation_id, result_asset_id, decision,
                    reason_codes_json, note, learning_action, status,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'submitted', ?, ?)
                """,
                (
                    review_id, job_id, generation_id, result_asset_id, decision,
                    encode_json(normalized_reasons), candidate["note"], learning_action,
                    now, now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM result_reviews WHERE id = ?", (review_id,)
            ).fetchone()
        assert row is not None
        return self._review_row(row)

    def list_result_reviews(self, job_id: str, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM result_reviews WHERE job_id = ? "
                "ORDER BY created_at ASC, id ASC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        return [self._review_row(row) for row in rows]

    def add_memory_suggestion(
        self,
        scope_type: str,
        rule_key: str,
        proposed_value: Any,
        *,
        scope_id: str = "",
        category: str = "general",
        current_value: Any = None,
        evidence: list[Any] | None = None,
        confidence: float = 0,
    ) -> dict[str, Any]:
        now = utc_now()
        suggestion_id = new_id("mem")
        proposed = dict(proposed_value) if isinstance(proposed_value, Mapping) else proposed_value
        if isinstance(proposed, dict):
            proposed["_governance"] = {
                "revision": 1,
                "last_action": "created",
                "updated_at": now,
                "manual_edit": False,
                "history": [],
                "redo": [],
            }
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO memory_suggestions(
                    id, scope_type, scope_id, category, rule_key, current_value_json,
                    proposed_value_json, evidence_json, confidence, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    suggestion_id, scope_type, scope_id, category, rule_key,
                    encode_json(current_value), encode_json(proposed),
                    encode_json(evidence or []), max(0.0, min(float(confidence), 1.0)), now,
                ),
            )
        return self.get_memory_suggestion(suggestion_id)

    def upsert_memory_suggestion(
        self,
        scope_type: str,
        rule_key: str,
        proposed_value: Any,
        *,
        scope_id: str = "",
        category: str = "general",
        current_value: Any = None,
        evidence: list[Any] | None = None,
        confidence: float = 0,
    ) -> dict[str, Any]:
        """Refresh a pending suggestion without spamming duplicate review cards.

        Approved values stay approved. Rejected values need at least two new pieces
        of evidence before the same rule can be proposed again.
        """
        evidence = list(evidence or [])
        proposed_rule_value = (
            proposed_value.get("value")
            if isinstance(proposed_value, Mapping) and "value" in proposed_value
            else proposed_value
        )
        now = utc_now()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM memory_suggestions
                WHERE scope_type = ? AND scope_id = ? AND category = ? AND rule_key = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (scope_type, scope_id, category, rule_key),
            ).fetchone()
            if row is not None:
                existing = self._memory_row(row)
                existing_proposed = existing.get("proposed_value")
                existing_rule_value = (
                    existing_proposed.get("value")
                    if isinstance(existing_proposed, Mapping) and "value" in existing_proposed
                    else existing_proposed
                )
                same_value = encode_json(existing_rule_value) == encode_json(proposed_rule_value)
                if existing["status"] == "approved" and same_value:
                    return existing
                if existing["status"] in {"rejected", "dismissed", "disabled"} and same_value:
                    old_count = len(existing.get("evidence") or [])
                    if len(evidence) < old_count + 2:
                        return existing
                if existing["status"] == "pending":
                    incoming = (
                        dict(proposed_value)
                        if isinstance(proposed_value, Mapping)
                        else proposed_value
                    )
                    governance = dict(existing.get("governance") or {})
                    if isinstance(incoming, dict) and governance.get("manual_edit"):
                        for field in ("label", "directive"):
                            if isinstance(existing_proposed, Mapping) and existing_proposed.get(field):
                                incoming[field] = existing_proposed[field]
                    if isinstance(incoming, dict):
                        current_snapshot = self._memory_snapshot(existing)
                        history = list(governance.get("history") or [])
                        history.append(current_snapshot)
                        for derived_key in ("history_count", "redo_count", "available_actions"):
                            governance.pop(derived_key, None)
                        governance.update({
                            "revision": int(governance.get("revision") or 1) + 1,
                            "last_action": "evidence_refresh",
                            "updated_at": now,
                            "history": history[-50:],
                            "redo": [],
                        })
                        governance.pop("postponed_at", None)
                        incoming["_governance"] = governance
                    connection.execute(
                        """
                        UPDATE memory_suggestions
                        SET current_value_json = ?, proposed_value_json = ?, evidence_json = ?,
                            confidence = ?, created_at = ?, reviewed_at = NULL
                        WHERE id = ?
                        """,
                        (
                            encode_json(current_value), encode_json(incoming), encode_json(evidence),
                            max(0.0, min(float(confidence), 1.0)), now, existing["id"],
                        ),
                    )
                    connection.commit()
                    return self.get_memory_suggestion(existing["id"])
        return self.add_memory_suggestion(
            scope_type,
            rule_key,
            proposed_value,
            scope_id=scope_id,
            category=category,
            current_value=current_value,
            evidence=evidence,
            confidence=confidence,
        )

    def review_memory_suggestion(self, suggestion_id: str, status: str) -> dict[str, Any]:
        if status not in {"approved", "rejected", "dismissed"}:
            raise ValueError("invalid memory suggestion status")
        action = {
            "approved": "approve",
            "rejected": "reject",
            "dismissed": "dismiss",
        }[status]
        return self.govern_memory_suggestion(suggestion_id, action=action)

    @staticmethod
    def _memory_snapshot(item: Mapping[str, Any]) -> dict[str, Any]:
        proposed = item.get("proposed_value")
        proposed = proposed if isinstance(proposed, Mapping) else {}
        governance = item.get("governance")
        governance = governance if isinstance(governance, Mapping) else {}
        return {
            "revision": int(governance.get("revision") or 1),
            "label": str(proposed.get("label") or ""),
            "directive": str(proposed.get("directive") or ""),
            "status": str(item.get("status") or "pending"),
            "reviewed_at": item.get("reviewed_at"),
            "changed_at": str(governance.get("updated_at") or item.get("created_at") or ""),
            "action": str(governance.get("last_action") or "created"),
            "postponed_at": governance.get("postponed_at"),
        }

    def govern_memory_suggestion(
        self,
        suggestion_id: str,
        *,
        action: str,
        expected_revision: int | None = None,
        label: str | None = None,
        directive: str | None = None,
    ) -> dict[str, Any]:
        """Apply one reversible governance action without changing evidence."""
        action = str(action or "").strip().lower()
        now = utc_now()
        with self._immediate_connection() as connection:
            row = connection.execute(
                "SELECT * FROM memory_suggestions WHERE id = ?", (suggestion_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown suggestion: {suggestion_id}")
            item = self._memory_row(row)
            governance = dict(item.get("governance") or {})
            revision = int(governance.get("revision") or 1)
            if expected_revision is not None and int(expected_revision) != revision:
                raise MemorySuggestionRevisionConflictError(
                    f"memory suggestion {suggestion_id} is revision {revision}, not {expected_revision}",
                    item,
                )

            status = str(item.get("status") or "pending")
            transitions = {
                "edit": {"pending"},
                "approve": {"pending"},
                "reject": {"pending"},
                "postpone": {"pending"},
                "dismiss": {"pending"},
                "disable": {"approved"},
                "enable": {"disabled"},
                "reopen": {"rejected", "dismissed"},
                "undo": {"pending", "approved", "rejected", "dismissed", "disabled"},
                "redo": {"pending", "approved", "rejected", "dismissed", "disabled"},
            }
            if action not in transitions or status not in transitions[action]:
                raise ValueError(f"invalid memory governance action {action!r} for status {status!r}")

            proposed = dict(item.get("proposed_value") or {})
            history = list(governance.get("history") or [])
            redo = list(governance.get("redo") or [])
            current_snapshot = self._memory_snapshot(item)
            next_status = status
            next_reviewed_at = item.get("reviewed_at")

            if action == "undo":
                if not history:
                    raise ValueError("memory suggestion has no earlier version to restore")
                target = dict(history.pop())
                redo.append(current_snapshot)
                proposed["label"] = str(target.get("label") or proposed.get("label") or "")
                proposed["directive"] = str(
                    target.get("directive") or proposed.get("directive") or ""
                )
                next_status = str(target.get("status") or "pending")
                next_reviewed_at = target.get("reviewed_at")
                if target.get("postponed_at"):
                    governance["postponed_at"] = target["postponed_at"]
                else:
                    governance.pop("postponed_at", None)
            elif action == "redo":
                if not redo:
                    raise ValueError("memory suggestion has no reverted version to restore")
                target = dict(redo.pop())
                history.append(current_snapshot)
                proposed["label"] = str(target.get("label") or proposed.get("label") or "")
                proposed["directive"] = str(
                    target.get("directive") or proposed.get("directive") or ""
                )
                next_status = str(target.get("status") or "pending")
                next_reviewed_at = target.get("reviewed_at")
                if target.get("postponed_at"):
                    governance["postponed_at"] = target["postponed_at"]
                else:
                    governance.pop("postponed_at", None)
            else:
                history.append(current_snapshot)
                redo = []
                if action == "edit":
                    next_label = str(label if label is not None else proposed.get("label") or "").strip()
                    next_directive = str(
                        directive if directive is not None else proposed.get("directive") or ""
                    ).strip()
                    if not next_label or len(next_label) > 80:
                        raise ValueError("memory suggestion label must contain 1 to 80 characters")
                    if not next_directive or len(next_directive) > 600:
                        raise ValueError("memory suggestion directive must contain 1 to 600 characters")
                    if (
                        next_label == str(proposed.get("label") or "")
                        and next_directive == str(proposed.get("directive") or "")
                    ):
                        raise ValueError("memory suggestion edit did not change anything")
                    proposed["label"] = next_label
                    proposed["directive"] = next_directive
                    governance["manual_edit"] = True
                    next_reviewed_at = None
                elif action == "postpone":
                    governance["postponed_at"] = now
                    next_reviewed_at = None
                else:
                    next_status = {
                        "approve": "approved",
                        "reject": "rejected",
                        "dismiss": "dismissed",
                        "disable": "disabled",
                        "enable": "approved",
                        "reopen": "pending",
                    }[action]
                    if action in {"approve", "reject", "dismiss", "reopen"}:
                        governance.pop("postponed_at", None)
                    next_reviewed_at = None if next_status == "pending" else now

            governance.update({
                "revision": revision + 1,
                "last_action": action,
                "updated_at": now,
                "history": history[-50:],
                "redo": redo[-50:],
            })
            for derived_key in ("history_count", "redo_count", "available_actions"):
                governance.pop(derived_key, None)
            proposed["_governance"] = governance
            connection.execute(
                """
                UPDATE memory_suggestions
                SET proposed_value_json = ?, status = ?, reviewed_at = ?
                WHERE id = ?
                """,
                (encode_json(proposed), next_status, next_reviewed_at, suggestion_id),
            )
        return self.get_memory_suggestion(suggestion_id)

    def dismiss_pending_memory_rule(
        self,
        scope_type: str,
        rule_key: str,
        *,
        scope_id: str = "",
        category: str = "general",
    ) -> int:
        """Withdraw an unreviewed candidate when new evidence becomes ambiguous."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id FROM memory_suggestions
                WHERE scope_type = ? AND scope_id = ? AND category = ?
                  AND rule_key = ? AND status = 'pending'
                """,
                (scope_type, scope_id, category, rule_key),
            ).fetchall()
        changed = 0
        for row in rows:
            try:
                self.govern_memory_suggestion(str(row["id"]), action="dismiss")
                changed += 1
            except ValueError:
                # A concurrent human decision wins over automatic withdrawal.
                continue
        return changed

    def get_session(self, session_id: str, *, include_timeline: bool = True) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown session: {session_id}")
            result = self._session_row(row)
            if include_timeline:
                result["assets"] = [self._asset_row(r) for r in connection.execute(
                    "SELECT * FROM assets WHERE session_id = ? ORDER BY created_at", (session_id,)
                )]
                result["generations"] = [self._generation_row(r) for r in connection.execute(
                    "SELECT * FROM generations WHERE session_id = ? ORDER BY created_at", (session_id,)
                )]
                result["events"] = [self._event_row(r) for r in connection.execute(
                    "SELECT * FROM events WHERE session_id = ? ORDER BY id", (session_id,)
                )]
                result["feedback"] = [self._feedback_row(r) for r in connection.execute(
                    "SELECT * FROM feedback WHERE session_id = ? ORDER BY created_at", (session_id,)
                )]
            return result

    def list_sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT s.*,
                    (SELECT COUNT(*) FROM assets a WHERE a.session_id = s.id) AS asset_count,
                    (SELECT COUNT(*) FROM generations g WHERE g.session_id = s.id) AS generation_count,
                    (SELECT COUNT(*) FROM feedback f WHERE f.session_id = s.id) AS feedback_count
                FROM sessions s
                WHERE s.mode <> 'workspace'
                ORDER BY updated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        results = []
        for row in rows:
            item = self._session_row(row)
            item.update({
                "asset_count": row["asset_count"],
                "generation_count": row["generation_count"],
                "feedback_count": row["feedback_count"],
            })
            results.append(item)
        return results

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown asset: {asset_id}")
        return self._asset_row(row)

    def get_generation(self, generation_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM generations WHERE id = ?", (generation_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown generation: {generation_id}")
        return self._generation_row(row)

    def get_memory_suggestion(self, suggestion_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM memory_suggestions WHERE id = ?", (suggestion_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown suggestion: {suggestion_id}")
        return self._memory_row(row)

    def list_memory_suggestions(self, status: str = "pending", limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_suggestions WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        return [self._memory_row(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        with self._connection() as connection:
            schema_version = self._read_schema_version(connection)
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "sessions", "assets", "asset_blobs", "generations", "events",
                    "feedback", "memory_suggestions", "jobs", "job_items", "task_attempts",
                    "asset_collections", "asset_collection_members", "workflow_drafts",
                    "draft_asset_selections", "job_snapshots", "result_reviews",
                    "execution_traces",
                )
            }
            counts["sessions"] = connection.execute(
                "SELECT COUNT(*) FROM sessions WHERE mode <> 'workspace'"
            ).fetchone()[0]
            counts["workspace_assets"] = connection.execute(
                "SELECT COUNT(*) FROM assets WHERE role = 'workspace_source'"
            ).fetchone()[0]
            pending = connection.execute(
                "SELECT COUNT(*) FROM memory_suggestions WHERE status = 'pending'"
            ).fetchone()[0]
        return {
            "schema_version": schema_version,
            "database": str(self.db_path),
            "counts": counts,
            "pending_memory": pending,
        }

    @staticmethod
    def _session_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["brief"] = decode_json(item.pop("brief_json", "{}"), {})
        item["intent_locks"] = decode_json(item.pop("intent_locks_json", "{}"), {})
        return item

    @staticmethod
    def _asset_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = decode_json(item.pop("metadata_json", "{}"), {})
        return item

    @classmethod
    def _workspace_asset_row(cls, row: sqlite3.Row) -> dict[str, Any]:
        item = cls._asset_row(row)
        item["blob"] = {
            "id": item.pop("blob_record_id"),
            "sha256": item.pop("blob_sha256"),
            "storage_path": item.pop("blob_storage_path"),
            "mime": item.pop("blob_mime"),
            "size_bytes": item.pop("blob_size_bytes"),
            "width": item.pop("blob_width"),
            "height": item.pop("blob_height"),
            "created_at": item.pop("blob_created_at"),
        }
        item["size_bytes"] = item["blob"]["size_bytes"]
        return item

    @staticmethod
    def _job_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["parameters"] = decode_json(item.pop("parameters_json", "{}"), {})
        if "session_title" in item:
            item["title"] = item.pop("session_title") or item.get("mode", "")
        return item

    @staticmethod
    def _job_snapshot_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["source_asset_ids"] = decode_json(
            item.pop("source_asset_ids_json", "[]"), []
        )
        item["brief"] = decode_json(item.pop("brief_json", "{}"), {})
        item["intent"] = decode_json(item.pop("intent_json", "{}"), {})
        item["parameters"] = decode_json(item.pop("parameters_json", "{}"), {})
        item["knowledge_refs"] = decode_json(
            item.pop("knowledge_refs_json", "[]"), []
        )
        item["ui_context"] = decode_json(item.pop("ui_context_json", "{}"), {})
        return item

    @staticmethod
    def _job_item_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["result_asset_ids"] = decode_json(
            item.pop("result_asset_ids_json", "[]"), []
        )
        if "generation_model" in item:
            item["model"] = item.pop("generation_model") or ""
        return item

    @staticmethod
    def _task_attempt_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = decode_json(item.pop("metadata_json", "{}"), {})
        return item

    @staticmethod
    def _generation_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["parameters"] = decode_json(item.pop("parameters_json", "{}"), {})
        item["knowledge_refs"] = decode_json(item.pop("knowledge_refs_json", "[]"), [])
        item["result_asset_ids"] = decode_json(item.pop("result_asset_ids_json", "[]"), [])
        return item

    @staticmethod
    def _event_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = decode_json(item.pop("payload_json", "{}"), {})
        return item

    @staticmethod
    def _feedback_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["structured"] = decode_json(item.pop("structured_json", "{}"), {})
        return item

    @staticmethod
    def _review_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["reason_codes"] = decode_json(item.pop("reason_codes_json", "[]"), [])
        return item

    @staticmethod
    def _trace_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["user_input"] = decode_json(item.pop("user_input_json", "{}"), {})
        item["applied_knowledge"] = decode_json(
            item.pop("applied_knowledge_json", "[]"), []
        )
        item["ignored_fields"] = decode_json(
            item.pop("ignored_fields_json", "[]"), []
        )
        item["parameters"] = decode_json(item.pop("parameters_json", "{}"), {})
        item["output"] = decode_json(item.pop("output_json", "{}"), {})
        return item

    @staticmethod
    def _memory_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["current_value"] = decode_json(item.pop("current_value_json", "null"), None)
        proposed = decode_json(item.pop("proposed_value_json", "null"), None)
        if isinstance(proposed, dict):
            raw_governance = proposed.pop("_governance", {})
        else:
            raw_governance = {}
        governance = dict(raw_governance) if isinstance(raw_governance, Mapping) else {}
        try:
            revision = max(1, int(governance.get("revision") or 1))
        except (TypeError, ValueError):
            revision = 1
        history = [dict(entry) for entry in governance.get("history") or [] if isinstance(entry, Mapping)]
        redo = [dict(entry) for entry in governance.get("redo") or [] if isinstance(entry, Mapping)]
        status = str(item.get("status") or "pending")
        available = {
            "pending": ["edit", "approve", "reject"],
            "approved": ["disable"],
            "rejected": ["reopen"],
            "dismissed": ["reopen"],
            "disabled": ["enable"],
        }.get(status, [])
        if status == "pending" and not governance.get("postponed_at"):
            available.insert(2, "postpone")
        if history:
            available.append("undo")
        if redo:
            available.append("redo")
        governance.update({
            "revision": revision,
            "history": history,
            "redo": redo,
            "history_count": len(history),
            "redo_count": len(redo),
            "available_actions": available,
        })
        item["proposed_value"] = proposed
        item["governance"] = governance
        item["evidence"] = decode_json(item.pop("evidence_json", "[]"), [])
        return item
