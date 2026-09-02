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
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from command_registry import command_for_mode, get_command
except ImportError:
    from .command_registry import command_for_mode, get_command

try:
    from local_edit_contract import (
        canvas_mask_fingerprint,
        normalize_canvas_mask_definition,
        normalize_local_edit_contract,
    )
except ImportError:
    from .local_edit_contract import (
        canvas_mask_fingerprint,
        normalize_canvas_mask_definition,
        normalize_local_edit_contract,
    )

try:
    from spatial_canvas_contract import (
        empty_spatial_scene,
        normalize_spatial_scene,
        spatial_scene_references,
        spatial_scene_thumbnail,
    )
except ImportError:
    from .spatial_canvas_contract import (
        empty_spatial_scene,
        normalize_spatial_scene,
        spatial_scene_references,
        spatial_scene_thumbnail,
    )


SCHEMA_VERSION = 8
CANVAS_DOCUMENT_SCHEMA_VERSION = 1
PRODUCT_PROFILE_SCHEMA_VERSION = 1
CANVAS_COORDINATE_SYSTEM = {
    "unit": "canvas-pixel",
    "origin": "top-left",
    "x_axis": "right",
    "y_axis": "down",
}
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


class CanvasRevisionConflictError(ValueError):
    """Raised when a stale canvas mutation targets an older document version."""

    def __init__(self, message: str, current: Mapping[str, Any]):
        super().__init__(message)
        self.current = dict(current)


class SpatialCanvasRevisionConflictError(ValueError):
    """Raised when a stale spatial scene save targets an older revision."""

    def __init__(self, message: str, current: Mapping[str, Any]):
        super().__init__(message)
        self.current = dict(current)


class SpatialSceneCorruptedError(LedgerSchemaError):
    """Raised when a stored spatial scene no longer matches its immutable receipt."""


class ProductProfileRevisionConflictError(ValueError):
    """Raised when a stale product-profile edit targets an older version."""

    def __init__(self, message: str, current: Mapping[str, Any]):
        super().__init__(message)
        self.current = dict(current)


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

V4_TABLE_COLUMNS = {
    "canvas_documents": frozenset({
        "id", "draft_id", "current_version_id", "current_revision",
        "created_at", "updated_at",
    }),
    "canvas_document_versions": frozenset({
        "id", "document_id", "revision", "parent_version_id",
        "client_request_id", "request_fingerprint", "document_json",
        "document_sha256", "created_at",
    }),
    "canvas_version_sources": frozenset({
        "version_id", "layer_id", "source_kind", "source_asset_id",
        "proxy_ref", "original_pixel_width", "original_pixel_height",
    }),
}

V4_JOB_SNAPSHOT_COLUMNS = frozenset({
    "command_id", "canvas_document_version_id", "canvas_operation_id",
})

V4_EXECUTION_TRACE_COLUMNS = frozenset({
    "command_id", "canvas_document_version_id", "canvas_operation_id",
})

V4_REQUIRED_INDEXES = frozenset({
    "idx_canvas_documents_draft",
    "idx_canvas_versions_document",
    "idx_canvas_sources_asset",
    "idx_job_snapshots_canvas_version",
})

V4_REQUIRED_TRIGGERS = frozenset({
    "trg_canvas_versions_no_update",
    "trg_canvas_versions_no_delete",
    "trg_canvas_sources_no_update",
    "trg_canvas_sources_no_delete",
})

V5_TABLE_COLUMNS = {
    "product_profiles": frozenset({
        "id", "sku", "current_version_id", "current_revision",
        "created_at", "updated_at",
    }),
    "product_profile_versions": frozenset({
        "id", "profile_id", "revision", "parent_version_id",
        "client_request_id", "request_fingerprint", "profile_json",
        "profile_sha256", "created_at",
    }),
    "product_profile_version_assets": frozenset({
        "version_id", "asset_id", "role",
    }),
}

V5_JOB_SNAPSHOT_COLUMNS = frozenset({"product_profile_version_id"})
V5_EXECUTION_TRACE_COLUMNS = frozenset({"product_profile_version_id"})

V5_REQUIRED_INDEXES = frozenset({
    "idx_product_profiles_sku",
    "idx_product_profile_versions_profile",
    "idx_product_profile_assets_asset",
    "idx_job_snapshots_product_profile",
})

V5_REQUIRED_TRIGGERS = frozenset({
    "trg_product_profile_versions_no_update",
    "trg_product_profile_versions_no_delete",
    "trg_product_profile_assets_no_update",
    "trg_product_profile_assets_no_delete",
})

V6_TABLE_COLUMNS = {
    "canvas_rois": frozenset({
        "id", "canvas_document_version_id", "source_layer_id",
        "coordinate_space", "x", "y", "width", "height", "purpose",
        "client_request_id", "request_fingerprint", "created_at",
    }),
    "canvas_masks": frozenset({
        "id", "roi_id", "current_version_id", "current_revision",
        "created_at", "updated_at",
    }),
    "canvas_mask_versions": frozenset({
        "id", "mask_id", "revision", "parent_version_id",
        "client_request_id", "request_fingerprint", "definition_json",
        "definition_sha256", "pixel_sha256", "created_at",
    }),
    "local_edit_specs": frozenset({
        "id", "operation_id", "canvas_document_version_id", "source_layer_id",
        "roi_id", "mask_version_id", "mode", "client_request_id",
        "request_fingerprint", "contract_json", "contract_sha256", "created_at",
    }),
}

V6_JOB_SNAPSHOT_COLUMNS = frozenset({"local_edit_spec_id"})
V6_EXECUTION_TRACE_COLUMNS = frozenset({"local_edit_spec_id"})

V6_REQUIRED_INDEXES = frozenset({
    "idx_canvas_rois_canvas_version",
    "idx_canvas_rois_layer",
    "idx_canvas_masks_roi",
    "idx_canvas_mask_versions_mask",
    "idx_local_edit_specs_canvas_version",
    "idx_local_edit_specs_roi",
    "idx_local_edit_specs_mask",
    "idx_job_snapshots_local_edit",
    "idx_execution_traces_local_edit",
})

V6_REQUIRED_TRIGGERS = frozenset({
    "trg_canvas_rois_no_update",
    "trg_canvas_rois_no_delete",
    "trg_canvas_mask_versions_no_update",
    "trg_canvas_mask_versions_no_delete",
    "trg_local_edit_specs_no_update",
    "trg_local_edit_specs_no_delete",
})

V7_TABLE_COLUMNS = {
    "local_edit_compositions": frozenset({
        "id", "local_edit_spec_id", "candidate_asset_id", "result_asset_id",
        "canvas_document_version_id", "client_request_id", "request_fingerprint",
        "receipt_json", "receipt_sha256", "created_at",
    }),
}

V7_REQUIRED_INDEXES = frozenset({
    "idx_local_edit_compositions_spec",
    "idx_local_edit_compositions_candidate",
})

V7_REQUIRED_TRIGGERS = frozenset({
    "trg_local_edit_compositions_no_update",
    "trg_local_edit_compositions_no_delete",
})

V8_TABLE_COLUMNS = {
    "spatial_canvas_documents": frozenset({
        "id", "name", "current_version_id", "current_revision",
        "create_request_id", "create_request_fingerprint",
        "created_at", "updated_at", "last_opened_at",
    }),
    "spatial_canvas_scene_versions": frozenset({
        "id", "document_id", "revision", "parent_version_id",
        "client_request_id", "request_fingerprint", "scene_json",
        "scene_sha256", "thumbnail_json", "thumbnail_sha256", "created_at",
    }),
    "spatial_scene_requests": frozenset({
        "client_request_id", "document_id", "expected_revision",
        "request_fingerprint", "resulting_version_id", "outcome", "created_at",
    }),
    "spatial_scene_references": frozenset({
        "version_id", "element_id", "ref_kind", "ref_id",
    }),
}

V8_REQUIRED_INDEXES = frozenset({
    "idx_spatial_documents_recent",
    "idx_spatial_versions_document",
    "idx_spatial_requests_document",
    "idx_spatial_references_lookup",
})

V8_REQUIRED_TRIGGERS = frozenset({
    "trg_spatial_scene_versions_no_update",
    "trg_spatial_scene_versions_no_delete",
    "trg_spatial_scene_requests_no_update",
    "trg_spatial_scene_requests_no_delete",
    "trg_spatial_scene_references_no_update",
    "trg_spatial_scene_references_no_delete",
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


def canonical_json(value: Any) -> str:
    return json.dumps(
        value if value is not None else {},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


_CANVAS_ID_PATTERN = re.compile(r"^[a-z][a-z0-9._:-]{2,127}$")
_CANVAS_PROXY_PATTERN = re.compile(r"^proxy:(thumbnail|preview):([1-9][0-9]{1,4})$")


def _canvas_object(
    value: Any,
    *,
    label: str,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    item = dict(value)
    allowed = required | set(optional or set())
    missing = sorted(required - set(item))
    unknown = sorted(set(item) - allowed)
    if missing:
        raise ValueError(f"{label} is missing fields: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"{label} has unknown fields: {', '.join(unknown)}")
    return item


def _canvas_id(value: Any, label: str) -> str:
    item = str(value or "").strip()
    if not _CANVAS_ID_PATTERN.fullmatch(item):
        raise ValueError(f"{label} is not a valid canvas identifier")
    return item


def _canvas_number(
    value: Any,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    if minimum is not None:
        invalid = number <= minimum if exclusive_minimum else number < minimum
        if invalid:
            raise ValueError(f"{label} is below its minimum")
    if maximum is not None and number > maximum:
        raise ValueError(f"{label} exceeds its maximum")
    return number


def _canvas_integer(
    value: Any,
    label: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} is below its minimum")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} exceeds its maximum")
    return value


def _canvas_timestamp(value: Any, label: str) -> str:
    item = str(value or "").strip()
    try:
        datetime.fromisoformat(item.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    return item


def _validate_layer_transform(value: Any, label: str) -> None:
    transform = _canvas_object(
        value,
        label=label,
        required={"x", "y", "scale_x", "scale_y", "rotation_degrees", "opacity"},
    )
    _canvas_number(transform["x"], f"{label}.x")
    _canvas_number(transform["y"], f"{label}.y")
    _canvas_number(transform["scale_x"], f"{label}.scale_x", minimum=0, exclusive_minimum=True)
    _canvas_number(transform["scale_y"], f"{label}.scale_y", minimum=0, exclusive_minimum=True)
    _canvas_number(
        transform["rotation_degrees"],
        f"{label}.rotation_degrees",
        minimum=-360,
        maximum=360,
    )
    _canvas_number(transform["opacity"], f"{label}.opacity", minimum=0, maximum=1)


def _validate_layer_snapshot(value: Any, label: str) -> None:
    snapshot = _canvas_object(
        value,
        label=label,
        required={"transform", "z_index", "visible", "locked"},
        optional={"source"},
    )
    _validate_layer_transform(snapshot["transform"], f"{label}.transform")
    _canvas_integer(snapshot["z_index"], f"{label}.z_index", minimum=0)
    if not isinstance(snapshot["visible"], bool) or not isinstance(snapshot["locked"], bool):
        raise ValueError(f"{label} visibility and lock fields must be booleans")
    if "source" in snapshot:
        _validate_layer_source(snapshot["source"], f"{label}.source")


def _validate_layer_source(value: Any, label: str) -> dict[str, Any]:
    source = _canvas_object(
        value,
        label=label,
        required={
            "kind", "id", "proxy_ref", "original_pixel_width",
            "original_pixel_height",
        },
    )
    if source["kind"] not in {"asset", "result"}:
        raise ValueError(f"{label}.kind is unsupported")
    _canvas_id(source["id"], f"{label}.id")
    proxy_match = _CANVAS_PROXY_PATTERN.fullmatch(str(source["proxy_ref"] or ""))
    if proxy_match is None or not 64 <= int(proxy_match.group(2)) <= 4096:
        raise ValueError(f"{label}.proxy_ref is not rebuildable")
    _canvas_integer(
        source["original_pixel_width"],
        f"{label}.original_pixel_width",
        minimum=1,
    )
    _canvas_integer(
        source["original_pixel_height"],
        f"{label}.original_pixel_height",
        minimum=1,
    )
    return source


def normalize_canvas_document(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the production CanvasDocument v1 contract without optional packages."""
    document = _canvas_object(
        value,
        label="CanvasDocument",
        required={
            "id", "schema_version", "coordinate_system", "revision",
            "active_artboard_id", "source_asset_ids", "artboards", "layers",
            "operations", "undo_cursor", "created_at", "updated_at",
        },
    )
    _canvas_id(document["id"], "CanvasDocument.id")
    if document["schema_version"] != CANVAS_DOCUMENT_SCHEMA_VERSION:
        raise ValueError("CanvasDocument.schema_version is unsupported")
    if document["coordinate_system"] != CANVAS_COORDINATE_SYSTEM:
        raise ValueError("CanvasDocument.coordinate_system must use top-left canvas pixels")
    _canvas_integer(document["revision"], "CanvasDocument.revision", minimum=0)
    active_artboard_id = _canvas_id(
        document["active_artboard_id"], "CanvasDocument.active_artboard_id"
    )

    source_asset_ids = document["source_asset_ids"]
    if not isinstance(source_asset_ids, list):
        raise ValueError("CanvasDocument.source_asset_ids must be a list")
    normalized_source_ids = [
        _canvas_id(item, "CanvasDocument.source_asset_ids item") for item in source_asset_ids
    ]
    if len(normalized_source_ids) != len(set(normalized_source_ids)):
        raise ValueError("CanvasDocument.source_asset_ids must be unique")

    artboards = document["artboards"]
    if not isinstance(artboards, list) or not artboards:
        raise ValueError("CanvasDocument.artboards must be a non-empty list")
    artboard_ids: set[str] = set()
    for index, raw_artboard in enumerate(artboards):
        label = f"CanvasDocument.artboards[{index}]"
        artboard = _canvas_object(
            raw_artboard,
            label=label,
            required={"id", "name", "rect", "export"},
        )
        artboard_id = _canvas_id(artboard["id"], f"{label}.id")
        if artboard_id in artboard_ids:
            raise ValueError(f"duplicate artboard id: {artboard_id}")
        artboard_ids.add(artboard_id)
        if not isinstance(artboard["name"], str) or not 1 <= len(artboard["name"]) <= 120:
            raise ValueError(f"{label}.name must contain 1 to 120 characters")
        rect = _canvas_object(
            artboard["rect"],
            label=f"{label}.rect",
            required={"x", "y", "width", "height"},
        )
        _canvas_number(rect["x"], f"{label}.rect.x")
        _canvas_number(rect["y"], f"{label}.rect.y")
        _canvas_number(rect["width"], f"{label}.rect.width", minimum=0, exclusive_minimum=True)
        _canvas_number(rect["height"], f"{label}.rect.height", minimum=0, exclusive_minimum=True)
        export = _canvas_object(
            artboard["export"],
            label=f"{label}.export",
            required={"pixel_width", "pixel_height", "color_space"},
        )
        _canvas_integer(export["pixel_width"], f"{label}.export.pixel_width", minimum=1, maximum=32768)
        _canvas_integer(export["pixel_height"], f"{label}.export.pixel_height", minimum=1, maximum=32768)
        if export["color_space"] not in {"srgb", "display-p3"}:
            raise ValueError(f"{label}.export.color_space is unsupported")
    if active_artboard_id not in artboard_ids:
        raise ValueError("CanvasDocument.active_artboard_id references a missing artboard")

    layers = document["layers"]
    if not isinstance(layers, list):
        raise ValueError("CanvasDocument.layers must be a list")
    layer_ids: set[str] = set()
    asset_layer_sources: set[str] = set()
    for index, raw_layer in enumerate(layers):
        label = f"CanvasDocument.layers[{index}]"
        layer = _canvas_object(
            raw_layer,
            label=label,
            required={"id", "artboard_id", "source", "transform", "z_index", "visible", "locked"},
        )
        layer_id = _canvas_id(layer["id"], f"{label}.id")
        if layer_id in layer_ids:
            raise ValueError(f"duplicate layer id: {layer_id}")
        layer_ids.add(layer_id)
        if _canvas_id(layer["artboard_id"], f"{label}.artboard_id") not in artboard_ids:
            raise ValueError(f"{label} references a missing artboard")
        source = _validate_layer_source(layer["source"], f"{label}.source")
        source_id = str(source["id"])
        if source["kind"] == "asset":
            asset_layer_sources.add(source_id)
        _validate_layer_transform(layer["transform"], f"{label}.transform")
        _canvas_integer(layer["z_index"], f"{label}.z_index", minimum=0)
        if not isinstance(layer["visible"], bool) or not isinstance(layer["locked"], bool):
            raise ValueError(f"{label} visibility and lock fields must be booleans")
    if asset_layer_sources != set(normalized_source_ids):
        raise ValueError("CanvasDocument.source_asset_ids must match asset-backed layers")

    operations = document["operations"]
    if not isinstance(operations, list):
        raise ValueError("CanvasDocument.operations must be a list")
    operation_ids: set[str] = set()
    for index, raw_operation in enumerate(operations):
        label = f"CanvasDocument.operations[{index}]"
        operation = _canvas_object(
            raw_operation,
            label=label,
            required={
                "id", "command_id", "input_layer_ids", "output_layer_id",
                "roi_id", "mask_id", "product_profile_id", "mutation",
                "cost", "status", "created_at",
            },
        )
        operation_id = _canvas_id(operation["id"], f"{label}.id")
        if operation_id in operation_ids:
            raise ValueError(f"duplicate operation id: {operation_id}")
        operation_ids.add(operation_id)
        command_id = _canvas_id(operation["command_id"], f"{label}.command_id")
        get_command(command_id)
        inputs = operation["input_layer_ids"]
        if not isinstance(inputs, list) or not inputs:
            raise ValueError(f"{label}.input_layer_ids must be a non-empty list")
        input_ids = [_canvas_id(item, f"{label}.input_layer_ids item") for item in inputs]
        if len(input_ids) != len(set(input_ids)) or not set(input_ids).issubset(layer_ids):
            raise ValueError(f"{label}.input_layer_ids are duplicate or missing")
        if _canvas_id(operation["output_layer_id"], f"{label}.output_layer_id") not in layer_ids:
            raise ValueError(f"{label}.output_layer_id references a missing layer")
        for field in ("roi_id", "mask_id", "product_profile_id"):
            if operation[field] is not None:
                _canvas_id(operation[field], f"{label}.{field}")
        mutation = operation["mutation"]
        if mutation is not None:
            mutation = _canvas_object(
                mutation,
                label=f"{label}.mutation",
                required={"target_layer_id", "before", "after"},
            )
            if _canvas_id(mutation["target_layer_id"], f"{label}.mutation.target_layer_id") not in layer_ids:
                raise ValueError(f"{label}.mutation references a missing layer")
            _validate_layer_snapshot(mutation["before"], f"{label}.mutation.before")
            _validate_layer_snapshot(mutation["after"], f"{label}.mutation.after")
        cost = _canvas_object(
            operation["cost"],
            label=f"{label}.cost",
            required={
                "mode", "confirmed_call_count", "user_confirmation_required",
                "automatic_paid_retry",
            },
        )
        if cost["mode"] not in {"free", "paid"}:
            raise ValueError(f"{label}.cost.mode is unsupported")
        calls = _canvas_integer(
            cost["confirmed_call_count"], f"{label}.cost.confirmed_call_count", minimum=0
        )
        if not isinstance(cost["user_confirmation_required"], bool) or not isinstance(
            cost["automatic_paid_retry"], bool
        ):
            raise ValueError(f"{label}.cost confirmation fields must be booleans")
        if cost["mode"] == "paid" and (
            calls < 1
            or cost["user_confirmation_required"] is not True
            or cost["automatic_paid_retry"] is not False
        ):
            raise ValueError(f"{label}.cost violates the paid-operation safety contract")
        if operation["status"] not in {"planned", "running", "succeeded", "failed", "canceled"}:
            raise ValueError(f"{label}.status is unsupported")
        _canvas_timestamp(operation["created_at"], f"{label}.created_at")

    undo_cursor = _canvas_integer(document["undo_cursor"], "CanvasDocument.undo_cursor", minimum=-1)
    if undo_cursor > len(operations) - 1:
        raise ValueError("CanvasDocument.undo_cursor exceeds the operation history")
    _canvas_timestamp(document["created_at"], "CanvasDocument.created_at")
    _canvas_timestamp(document["updated_at"], "CanvasDocument.updated_at")
    return json.loads(canonical_json(document))


_PROFILE_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|/(?:Users|home|var|tmp)/)")
_PROFILE_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _profile_text(
    value: Any,
    label: str,
    *,
    minimum: int = 0,
    maximum: int,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    item = value.strip()
    if not minimum <= len(item) <= maximum:
        raise ValueError(f"{label} must contain {minimum} to {maximum} characters")
    lowered = item.lower()
    if "base64," in lowered or lowered.startswith("data:"):
        raise ValueError(f"{label} cannot contain embedded data")
    if _PROFILE_ABSOLUTE_PATH.match(item):
        raise ValueError(f"{label} cannot contain an absolute path")
    return item


def _profile_array(value: Any, label: str, *, minimum: int = 0, maximum: int) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{label} must contain {minimum} to {maximum} items")
    return value


def normalize_product_profile(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate ProductProfile v1 as strict, model-independent product facts."""
    profile = _canvas_object(
        value,
        label="ProductProfile",
        required={
            "id", "schema_version", "sku", "name", "revision", "category",
            "specification", "components", "materials", "brand_colors",
            "packaging_texts", "logos", "platform_specs", "selection_mode",
            "approved_reference_ids", "created_at", "updated_at",
        },
    )
    _canvas_id(profile["id"], "ProductProfile.id")
    if profile["schema_version"] != PRODUCT_PROFILE_SCHEMA_VERSION:
        raise ValueError("ProductProfile.schema_version is unsupported")
    _profile_text(profile["sku"], "ProductProfile.sku", minimum=1, maximum=120)
    _profile_text(profile["name"], "ProductProfile.name", minimum=1, maximum=160)
    _canvas_integer(profile["revision"], "ProductProfile.revision", minimum=0)
    _profile_text(profile["category"], "ProductProfile.category", minimum=1, maximum=120)

    specification = _canvas_object(
        profile["specification"],
        label="ProductProfile.specification",
        required={"display", "net_content", "unit_count", "attributes"},
    )
    _profile_text(
        specification["display"], "ProductProfile.specification.display",
        minimum=1, maximum=160,
    )
    _profile_text(
        specification["net_content"], "ProductProfile.specification.net_content",
        maximum=120,
    )
    _canvas_integer(
        specification["unit_count"], "ProductProfile.specification.unit_count",
        minimum=1, maximum=10000,
    )
    attribute_keys: set[str] = set()
    for index, raw_attribute in enumerate(_profile_array(
        specification["attributes"], "ProductProfile.specification.attributes", maximum=32
    )):
        label = f"ProductProfile.specification.attributes[{index}]"
        attribute = _canvas_object(raw_attribute, label=label, required={"key", "value"})
        key = _profile_text(attribute["key"], f"{label}.key", minimum=1, maximum=80)
        _profile_text(attribute["value"], f"{label}.value", minimum=1, maximum=160)
        normalized_key = key.casefold()
        if normalized_key in attribute_keys:
            raise ValueError("ProductProfile specification attribute keys must be unique")
        attribute_keys.add(normalized_key)

    component_ids: set[str] = set()
    allowed_roles = {"core", "container", "cap", "label", "accessory", "shadow", "background", "other"}
    allowed_policies = {"must_preserve", "optional_preserve", "allow_modify", "forbid_modify"}
    for index, raw_component in enumerate(_profile_array(
        profile["components"], "ProductProfile.components", minimum=1, maximum=64
    )):
        label = f"ProductProfile.components[{index}]"
        component = _canvas_object(
            raw_component,
            label=label,
            required={"id", "name", "role", "policy", "quantity"},
        )
        component_id = _canvas_id(component["id"], f"{label}.id")
        if component_id in component_ids:
            raise ValueError(f"duplicate product component id: {component_id}")
        component_ids.add(component_id)
        _profile_text(component["name"], f"{label}.name", minimum=1, maximum=120)
        if component["role"] not in allowed_roles:
            raise ValueError(f"{label}.role is unsupported")
        if component["policy"] not in allowed_policies:
            raise ValueError(f"{label}.policy is unsupported")
        _canvas_integer(component["quantity"], f"{label}.quantity", minimum=1, maximum=10000)

    material_components: set[str] = set()
    for index, raw_material in enumerate(_profile_array(
        profile["materials"], "ProductProfile.materials", maximum=64
    )):
        label = f"ProductProfile.materials[{index}]"
        material = _canvas_object(
            raw_material,
            label=label,
            required={"component_id", "material", "finish", "transparent"},
        )
        component_id = _canvas_id(material["component_id"], f"{label}.component_id")
        if component_id not in component_ids:
            raise ValueError(f"{label}.component_id references a missing component")
        if component_id in material_components:
            raise ValueError("ProductProfile materials must identify each component once")
        material_components.add(component_id)
        _profile_text(material["material"], f"{label}.material", minimum=1, maximum=120)
        _profile_text(material["finish"], f"{label}.finish", maximum=120)
        if not isinstance(material["transparent"], bool):
            raise ValueError(f"{label}.transparent must be a boolean")

    color_names: set[str] = set()
    for index, raw_color in enumerate(_profile_array(
        profile["brand_colors"], "ProductProfile.brand_colors", maximum=16
    )):
        label = f"ProductProfile.brand_colors[{index}]"
        color = _canvas_object(raw_color, label=label, required={"name", "value"})
        name = _profile_text(color["name"], f"{label}.name", minimum=1, maximum=80)
        if name.casefold() in color_names:
            raise ValueError("ProductProfile brand color names must be unique")
        color_names.add(name.casefold())
        if not isinstance(color["value"], str) or not _PROFILE_COLOR.fullmatch(color["value"]):
            raise ValueError(f"{label}.value must be a six-digit hex color")

    def validate_component_annotations(
        raw_items: Any,
        *,
        field: str,
        maximum: int,
        required: set[str],
        policies: set[str],
        text_field: str,
        text_maximum: int,
    ) -> None:
        item_ids: set[str] = set()
        for index, raw_item in enumerate(_profile_array(
            raw_items, f"ProductProfile.{field}", maximum=maximum
        )):
            label = f"ProductProfile.{field}[{index}]"
            item = _canvas_object(raw_item, label=label, required=required)
            item_id = _canvas_id(item["id"], f"{label}.id")
            if item_id in item_ids:
                raise ValueError(f"duplicate ProductProfile.{field} id: {item_id}")
            item_ids.add(item_id)
            component_id = _canvas_id(item["component_id"], f"{label}.component_id")
            if component_id not in component_ids:
                raise ValueError(f"{label}.component_id references a missing component")
            _profile_text(item[text_field], f"{label}.{text_field}", minimum=1, maximum=text_maximum)
            if item["policy"] not in policies:
                raise ValueError(f"{label}.policy is unsupported")

    validate_component_annotations(
        profile["packaging_texts"],
        field="packaging_texts",
        maximum=64,
        required={"id", "component_id", "content", "policy"},
        policies={"exact_preserve", "readable_preserve", "allow_modify"},
        text_field="content",
        text_maximum=500,
    )
    validate_component_annotations(
        profile["logos"],
        field="logos",
        maximum=32,
        required={"id", "component_id", "name", "policy"},
        policies={"exact_preserve", "allow_reposition", "allow_modify"},
        text_field="name",
        text_maximum=120,
    )

    platform_keys: set[tuple[str, str]] = set()
    for index, raw_spec in enumerate(_profile_array(
        profile["platform_specs"], "ProductProfile.platform_specs", minimum=1, maximum=32
    )):
        label = f"ProductProfile.platform_specs[{index}]"
        spec = _canvas_object(
            raw_spec,
            label=label,
            required={
                "platform", "role", "pixel_width", "pixel_height", "format",
                "safe_area_percent",
            },
        )
        platform = _profile_text(spec["platform"], f"{label}.platform", minimum=1, maximum=80)
        role = _profile_text(spec["role"], f"{label}.role", minimum=1, maximum=80)
        key = (platform.casefold(), role.casefold())
        if key in platform_keys:
            raise ValueError("ProductProfile platform and role pairs must be unique")
        platform_keys.add(key)
        _canvas_integer(spec["pixel_width"], f"{label}.pixel_width", minimum=1, maximum=32768)
        _canvas_integer(spec["pixel_height"], f"{label}.pixel_height", minimum=1, maximum=32768)
        if spec["format"] not in {"jpeg", "png", "webp"}:
            raise ValueError(f"{label}.format is unsupported")
        _canvas_number(
            spec["safe_area_percent"], f"{label}.safe_area_percent", minimum=0, maximum=45
        )

    if profile["selection_mode"] not in {
        "core_only", "core_with_container", "full_composition", "separate_all",
    }:
        raise ValueError("ProductProfile.selection_mode is unsupported")
    references = _profile_array(
        profile["approved_reference_ids"],
        "ProductProfile.approved_reference_ids",
        minimum=1,
        maximum=64,
    )
    reference_ids = [
        _canvas_id(item, "ProductProfile.approved_reference_ids item") for item in references
    ]
    if len(reference_ids) != len(set(reference_ids)):
        raise ValueError("ProductProfile.approved_reference_ids must be unique")
    _canvas_timestamp(profile["created_at"], "ProductProfile.created_at")
    _canvas_timestamp(profile["updated_at"], "ProductProfile.updated_at")
    return json.loads(canonical_json(profile))


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

    @classmethod
    def _v4_objects_present(cls, connection: sqlite3.Connection) -> bool:
        tables = cls._table_names(connection)
        if tables.intersection(V4_TABLE_COLUMNS):
            return True
        if "job_snapshots" in tables:
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(job_snapshots)")
            }
            if columns.intersection(V4_JOB_SNAPSHOT_COLUMNS):
                return True
        if "execution_traces" in tables:
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(execution_traces)")
            }
            if columns.intersection(V4_EXECUTION_TRACE_COLUMNS):
                return True
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        return bool(triggers.intersection(V4_REQUIRED_TRIGGERS))

    @classmethod
    def _v4_contract_issues(cls, connection: sqlite3.Connection) -> list[str]:
        issues: list[str] = []
        tables = cls._table_names(connection)
        for table, required_columns in V4_TABLE_COLUMNS.items():
            if table not in tables:
                issues.append(f"missing table {table}")
                continue
            actual_columns = {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            missing_columns = sorted(required_columns - actual_columns)
            if missing_columns:
                issues.append(f"{table} missing columns: {', '.join(missing_columns)}")

        if "job_snapshots" not in tables:
            issues.append("missing table job_snapshots")
        else:
            snapshot_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(job_snapshots)")
            }
            missing = sorted(V4_JOB_SNAPSHOT_COLUMNS - snapshot_columns)
            if missing:
                issues.append(f"job_snapshots missing columns: {', '.join(missing)}")

        if "execution_traces" not in tables:
            issues.append("missing table execution_traces")
        else:
            trace_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(execution_traces)")
            }
            missing = sorted(V4_EXECUTION_TRACE_COLUMNS - trace_columns)
            if missing:
                issues.append(f"execution_traces missing columns: {', '.join(missing)}")

        indexes = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
            )
        }
        missing_indexes = sorted(V4_REQUIRED_INDEXES - indexes)
        if missing_indexes:
            issues.append(f"missing indexes: {', '.join(missing_indexes)}")

        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        missing_triggers = sorted(V4_REQUIRED_TRIGGERS - triggers)
        if missing_triggers:
            issues.append(f"missing triggers: {', '.join(missing_triggers)}")

        integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        if integrity_rows != ["ok"]:
            issues.append(f"integrity_check failed: {'; '.join(integrity_rows[:3])}")
        foreign_key_rows = list(connection.execute("PRAGMA foreign_key_check"))
        if foreign_key_rows:
            issues.append(f"foreign_key_check found {len(foreign_key_rows)} violation(s)")
        return issues

    @classmethod
    def _v5_objects_present(cls, connection: sqlite3.Connection) -> bool:
        tables = cls._table_names(connection)
        if tables.intersection(V5_TABLE_COLUMNS):
            return True
        for table, expected in (
            ("job_snapshots", V5_JOB_SNAPSHOT_COLUMNS),
            ("execution_traces", V5_EXECUTION_TRACE_COLUMNS),
        ):
            if table in tables:
                columns = {
                    str(row[1])
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                if columns.intersection(expected):
                    return True
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        return bool(triggers.intersection(V5_REQUIRED_TRIGGERS))

    @classmethod
    def _v5_contract_issues(cls, connection: sqlite3.Connection) -> list[str]:
        issues: list[str] = []
        tables = cls._table_names(connection)
        for table, required_columns in V5_TABLE_COLUMNS.items():
            if table not in tables:
                issues.append(f"missing table {table}")
                continue
            actual_columns = {
                str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
            }
            missing_columns = sorted(required_columns - actual_columns)
            if missing_columns:
                issues.append(f"{table} missing columns: {', '.join(missing_columns)}")

        for table, required_columns in (
            ("job_snapshots", V5_JOB_SNAPSHOT_COLUMNS),
            ("execution_traces", V5_EXECUTION_TRACE_COLUMNS),
        ):
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
        missing_indexes = sorted(V5_REQUIRED_INDEXES - indexes)
        if missing_indexes:
            issues.append(f"missing indexes: {', '.join(missing_indexes)}")
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        missing_triggers = sorted(V5_REQUIRED_TRIGGERS - triggers)
        if missing_triggers:
            issues.append(f"missing triggers: {', '.join(missing_triggers)}")
        integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        if integrity_rows != ["ok"]:
            issues.append(f"integrity_check failed: {'; '.join(integrity_rows[:3])}")
        foreign_key_rows = list(connection.execute("PRAGMA foreign_key_check"))
        if foreign_key_rows:
            issues.append(f"foreign_key_check found {len(foreign_key_rows)} violation(s)")
        return issues

    @classmethod
    def _v6_objects_present(cls, connection: sqlite3.Connection) -> bool:
        tables = cls._table_names(connection)
        if tables.intersection(V6_TABLE_COLUMNS):
            return True
        for table, expected in (
            ("job_snapshots", V6_JOB_SNAPSHOT_COLUMNS),
            ("execution_traces", V6_EXECUTION_TRACE_COLUMNS),
        ):
            if table in tables:
                columns = {
                    str(row[1])
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                if columns.intersection(expected):
                    return True
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        return bool(triggers.intersection(V6_REQUIRED_TRIGGERS))

    @classmethod
    def _v6_contract_issues(cls, connection: sqlite3.Connection) -> list[str]:
        issues: list[str] = []
        tables = cls._table_names(connection)
        for table, required_columns in V6_TABLE_COLUMNS.items():
            if table not in tables:
                issues.append(f"missing table {table}")
                continue
            actual_columns = {
                str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
            }
            missing_columns = sorted(required_columns - actual_columns)
            if missing_columns:
                issues.append(f"{table} missing columns: {', '.join(missing_columns)}")

        for table, required_columns in (
            ("job_snapshots", V6_JOB_SNAPSHOT_COLUMNS),
            ("execution_traces", V6_EXECUTION_TRACE_COLUMNS),
        ):
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
        missing_indexes = sorted(V6_REQUIRED_INDEXES - indexes)
        if missing_indexes:
            issues.append(f"missing indexes: {', '.join(missing_indexes)}")
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        missing_triggers = sorted(V6_REQUIRED_TRIGGERS - triggers)
        if missing_triggers:
            issues.append(f"missing triggers: {', '.join(missing_triggers)}")
        integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        if integrity_rows != ["ok"]:
            issues.append(f"integrity_check failed: {'; '.join(integrity_rows[:3])}")
        foreign_key_rows = list(connection.execute("PRAGMA foreign_key_check"))
        if foreign_key_rows:
            issues.append(f"foreign_key_check found {len(foreign_key_rows)} violation(s)")
        return issues

    @classmethod
    def _v7_objects_present(cls, connection: sqlite3.Connection) -> bool:
        tables = cls._table_names(connection)
        if tables.intersection(V7_TABLE_COLUMNS):
            return True
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        return bool(triggers.intersection(V7_REQUIRED_TRIGGERS))

    @classmethod
    def _v7_contract_issues(cls, connection: sqlite3.Connection) -> list[str]:
        issues: list[str] = []
        tables = cls._table_names(connection)
        for table, required_columns in V7_TABLE_COLUMNS.items():
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
        missing_indexes = sorted(V7_REQUIRED_INDEXES - indexes)
        if missing_indexes:
            issues.append(f"missing indexes: {', '.join(missing_indexes)}")
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        missing_triggers = sorted(V7_REQUIRED_TRIGGERS - triggers)
        if missing_triggers:
            issues.append(f"missing triggers: {', '.join(missing_triggers)}")
        integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        if integrity_rows != ["ok"]:
            issues.append(f"integrity_check failed: {'; '.join(integrity_rows[:3])}")
        foreign_key_rows = list(connection.execute("PRAGMA foreign_key_check"))
        if foreign_key_rows:
            issues.append(f"foreign_key_check found {len(foreign_key_rows)} violation(s)")
        return issues

    @classmethod
    def _v8_objects_present(cls, connection: sqlite3.Connection) -> bool:
        tables = cls._table_names(connection)
        if tables.intersection(V8_TABLE_COLUMNS):
            return True
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        return bool(triggers.intersection(V8_REQUIRED_TRIGGERS))

    @classmethod
    def _v8_contract_issues(cls, connection: sqlite3.Connection) -> list[str]:
        issues: list[str] = []
        tables = cls._table_names(connection)
        for table, required_columns in V8_TABLE_COLUMNS.items():
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
        missing_indexes = sorted(V8_REQUIRED_INDEXES - indexes)
        if missing_indexes:
            issues.append(f"missing indexes: {', '.join(missing_indexes)}")
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        missing_triggers = sorted(V8_REQUIRED_TRIGGERS - triggers)
        if missing_triggers:
            issues.append(f"missing triggers: {', '.join(missing_triggers)}")
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

    @staticmethod
    def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
        """Add immutable canvas versions and bind jobs to canonical commands."""
        connection.execute(
            """
            CREATE TABLE canvas_documents (
                id TEXT PRIMARY KEY,
                draft_id TEXT NOT NULL UNIQUE,
                current_version_id TEXT,
                current_revision INTEGER NOT NULL DEFAULT 0 CHECK(current_revision >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(draft_id) REFERENCES workflow_drafts(id) ON DELETE RESTRICT,
                FOREIGN KEY(current_version_id) REFERENCES canvas_document_versions(id) ON DELETE RESTRICT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE canvas_document_versions (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision >= 1),
                parent_version_id TEXT,
                client_request_id TEXT NOT NULL UNIQUE,
                request_fingerprint TEXT NOT NULL,
                document_json TEXT NOT NULL,
                document_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES canvas_documents(id) ON DELETE RESTRICT,
                FOREIGN KEY(parent_version_id) REFERENCES canvas_document_versions(id) ON DELETE RESTRICT,
                UNIQUE(document_id, revision)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE canvas_version_sources (
                version_id TEXT NOT NULL,
                layer_id TEXT NOT NULL,
                source_kind TEXT NOT NULL CHECK(source_kind IN ('asset','result')),
                source_asset_id TEXT NOT NULL,
                proxy_ref TEXT NOT NULL,
                original_pixel_width INTEGER NOT NULL CHECK(original_pixel_width > 0),
                original_pixel_height INTEGER NOT NULL CHECK(original_pixel_height > 0),
                PRIMARY KEY(version_id, layer_id),
                FOREIGN KEY(version_id) REFERENCES canvas_document_versions(id) ON DELETE RESTRICT,
                FOREIGN KEY(source_asset_id) REFERENCES assets(id) ON DELETE RESTRICT
            )
            """
        )
        connection.execute(
            "ALTER TABLE job_snapshots ADD COLUMN command_id TEXT NOT NULL DEFAULT ''"
        )
        connection.execute(
            "ALTER TABLE job_snapshots ADD COLUMN canvas_document_version_id TEXT "
            "REFERENCES canvas_document_versions(id) ON DELETE RESTRICT"
        )
        connection.execute(
            "ALTER TABLE job_snapshots ADD COLUMN canvas_operation_id TEXT"
        )
        connection.execute(
            "ALTER TABLE execution_traces ADD COLUMN command_id TEXT NOT NULL DEFAULT ''"
        )
        connection.execute(
            "ALTER TABLE execution_traces ADD COLUMN canvas_document_version_id TEXT "
            "REFERENCES canvas_document_versions(id) ON DELETE RESTRICT"
        )
        connection.execute(
            "ALTER TABLE execution_traces ADD COLUMN canvas_operation_id TEXT"
        )
        connection.execute(
            """
            UPDATE job_snapshots
            SET command_id = CASE mode
                WHEN 'single' THEN 'command:existing-generate-single'
                WHEN 'multi-file' THEN 'command:existing-generate-multi-file'
                WHEN 'group-split' THEN 'command:existing-group-split'
                WHEN 'cutout-batch' THEN 'command:existing-remove-background'
                ELSE ''
            END
            """
        )
        connection.execute(
            """
            UPDATE execution_traces
            SET command_id = COALESCE((
                    SELECT s.command_id FROM job_snapshots s
                    WHERE s.job_id = execution_traces.job_id
                ), ''),
                canvas_document_version_id = (
                    SELECT s.canvas_document_version_id FROM job_snapshots s
                    WHERE s.job_id = execution_traces.job_id
                ),
                canvas_operation_id = (
                    SELECT s.canvas_operation_id FROM job_snapshots s
                    WHERE s.job_id = execution_traces.job_id
                )
            """
        )
        connection.execute(
            "CREATE UNIQUE INDEX idx_canvas_documents_draft ON canvas_documents(draft_id)"
        )
        connection.execute(
            "CREATE INDEX idx_canvas_versions_document "
            "ON canvas_document_versions(document_id, revision DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_canvas_sources_asset ON canvas_version_sources(source_asset_id)"
        )
        connection.execute(
            "CREATE INDEX idx_job_snapshots_canvas_version "
            "ON job_snapshots(canvas_document_version_id)"
        )
        connection.execute(
            """
            CREATE TRIGGER trg_canvas_versions_no_update
            BEFORE UPDATE ON canvas_document_versions
            BEGIN
                SELECT RAISE(ABORT, 'canvas document versions are immutable');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER trg_canvas_versions_no_delete
            BEFORE DELETE ON canvas_document_versions
            BEGIN
                SELECT RAISE(ABORT, 'canvas document versions are immutable');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER trg_canvas_sources_no_update
            BEFORE UPDATE ON canvas_version_sources
            BEGIN
                SELECT RAISE(ABORT, 'canvas version sources are immutable');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER trg_canvas_sources_no_delete
            BEFORE DELETE ON canvas_version_sources
            BEGIN
                SELECT RAISE(ABORT, 'canvas version sources are immutable');
            END
            """
        )

    @staticmethod
    def _migrate_v4_to_v5(connection: sqlite3.Connection) -> None:
        """Add immutable SKU product profiles and exact job/trace bindings."""
        connection.execute(
            """
            CREATE TABLE product_profiles (
                id TEXT PRIMARY KEY,
                sku TEXT NOT NULL,
                current_version_id TEXT,
                current_revision INTEGER NOT NULL DEFAULT 0 CHECK(current_revision >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(current_version_id)
                    REFERENCES product_profile_versions(id) ON DELETE RESTRICT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE product_profile_versions (
                id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision >= 1),
                parent_version_id TEXT,
                client_request_id TEXT NOT NULL UNIQUE,
                request_fingerprint TEXT NOT NULL,
                profile_json TEXT NOT NULL,
                profile_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(profile_id) REFERENCES product_profiles(id) ON DELETE RESTRICT,
                FOREIGN KEY(parent_version_id)
                    REFERENCES product_profile_versions(id) ON DELETE RESTRICT,
                UNIQUE(profile_id, revision)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE product_profile_version_assets (
                version_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role = 'approved_reference'),
                PRIMARY KEY(version_id, asset_id, role),
                FOREIGN KEY(version_id)
                    REFERENCES product_profile_versions(id) ON DELETE RESTRICT,
                FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE RESTRICT
            )
            """
        )
        connection.execute(
            "ALTER TABLE job_snapshots ADD COLUMN product_profile_version_id TEXT "
            "REFERENCES product_profile_versions(id) ON DELETE RESTRICT"
        )
        connection.execute(
            "ALTER TABLE execution_traces ADD COLUMN product_profile_version_id TEXT "
            "REFERENCES product_profile_versions(id) ON DELETE RESTRICT"
        )
        connection.execute(
            "CREATE UNIQUE INDEX idx_product_profiles_sku "
            "ON product_profiles(sku COLLATE NOCASE)"
        )
        connection.execute(
            "CREATE INDEX idx_product_profile_versions_profile "
            "ON product_profile_versions(profile_id, revision DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_product_profile_assets_asset "
            "ON product_profile_version_assets(asset_id)"
        )
        connection.execute(
            "CREATE INDEX idx_job_snapshots_product_profile "
            "ON job_snapshots(product_profile_version_id)"
        )
        for trigger_name, table, message in (
            (
                "trg_product_profile_versions_no_update",
                "product_profile_versions",
                "product profile versions are immutable",
            ),
            (
                "trg_product_profile_versions_no_delete",
                "product_profile_versions",
                "product profile versions are immutable",
            ),
            (
                "trg_product_profile_assets_no_update",
                "product_profile_version_assets",
                "product profile version assets are immutable",
            ),
            (
                "trg_product_profile_assets_no_delete",
                "product_profile_version_assets",
                "product profile version assets are immutable",
            ),
        ):
            action = "UPDATE" if trigger_name.endswith("no_update") else "DELETE"
            connection.execute(
                f"""
                CREATE TRIGGER {trigger_name}
                BEFORE {action} ON {table}
                BEGIN
                    SELECT RAISE(ABORT, '{message}');
                END
                """
            )

    @staticmethod
    def _migrate_v5_to_v6(connection: sqlite3.Connection) -> None:
        """Add immutable ROI, mask-version, and local-edit specification facts."""
        connection.execute(
            """
            CREATE TABLE canvas_rois (
                id TEXT PRIMARY KEY,
                canvas_document_version_id TEXT NOT NULL,
                source_layer_id TEXT NOT NULL,
                coordinate_space TEXT NOT NULL
                    CHECK(coordinate_space IN ('source-pixel', 'output-pixel')),
                x INTEGER NOT NULL CHECK(x >= 0),
                y INTEGER NOT NULL CHECK(y >= 0),
                width INTEGER NOT NULL CHECK(width > 0),
                height INTEGER NOT NULL CHECK(height > 0),
                purpose TEXT NOT NULL CHECK(purpose IN ('inpaint', 'outpaint')),
                client_request_id TEXT NOT NULL UNIQUE,
                request_fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(canvas_document_version_id)
                    REFERENCES canvas_document_versions(id) ON DELETE RESTRICT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE canvas_masks (
                id TEXT PRIMARY KEY,
                roi_id TEXT NOT NULL,
                current_version_id TEXT,
                current_revision INTEGER NOT NULL DEFAULT 0 CHECK(current_revision >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(roi_id) REFERENCES canvas_rois(id) ON DELETE RESTRICT,
                FOREIGN KEY(current_version_id)
                    REFERENCES canvas_mask_versions(id) ON DELETE RESTRICT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE canvas_mask_versions (
                id TEXT PRIMARY KEY,
                mask_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision >= 1),
                parent_version_id TEXT,
                client_request_id TEXT NOT NULL UNIQUE,
                request_fingerprint TEXT NOT NULL,
                definition_json TEXT NOT NULL,
                definition_sha256 TEXT NOT NULL,
                pixel_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(mask_id) REFERENCES canvas_masks(id) ON DELETE RESTRICT,
                FOREIGN KEY(parent_version_id)
                    REFERENCES canvas_mask_versions(id) ON DELETE RESTRICT,
                UNIQUE(mask_id, revision)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE local_edit_specs (
                id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL UNIQUE,
                canvas_document_version_id TEXT NOT NULL,
                source_layer_id TEXT NOT NULL,
                roi_id TEXT NOT NULL,
                mask_version_id TEXT,
                mode TEXT NOT NULL CHECK(mode IN ('inpaint', 'outpaint')),
                client_request_id TEXT NOT NULL UNIQUE,
                request_fingerprint TEXT NOT NULL,
                contract_json TEXT NOT NULL,
                contract_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(canvas_document_version_id)
                    REFERENCES canvas_document_versions(id) ON DELETE RESTRICT,
                FOREIGN KEY(roi_id) REFERENCES canvas_rois(id) ON DELETE RESTRICT,
                FOREIGN KEY(mask_version_id)
                    REFERENCES canvas_mask_versions(id) ON DELETE RESTRICT
            )
            """
        )
        connection.execute(
            "ALTER TABLE job_snapshots ADD COLUMN local_edit_spec_id TEXT "
            "REFERENCES local_edit_specs(id) ON DELETE RESTRICT"
        )
        connection.execute(
            "ALTER TABLE execution_traces ADD COLUMN local_edit_spec_id TEXT "
            "REFERENCES local_edit_specs(id) ON DELETE RESTRICT"
        )
        for statement in (
            "CREATE INDEX idx_canvas_rois_canvas_version "
            "ON canvas_rois(canvas_document_version_id)",
            "CREATE INDEX idx_canvas_rois_layer "
            "ON canvas_rois(canvas_document_version_id, source_layer_id)",
            "CREATE UNIQUE INDEX idx_canvas_masks_roi ON canvas_masks(roi_id)",
            "CREATE INDEX idx_canvas_mask_versions_mask "
            "ON canvas_mask_versions(mask_id, revision DESC)",
            "CREATE INDEX idx_local_edit_specs_canvas_version "
            "ON local_edit_specs(canvas_document_version_id)",
            "CREATE INDEX idx_local_edit_specs_roi ON local_edit_specs(roi_id)",
            "CREATE INDEX idx_local_edit_specs_mask ON local_edit_specs(mask_version_id)",
            "CREATE INDEX idx_job_snapshots_local_edit "
            "ON job_snapshots(local_edit_spec_id)",
            "CREATE INDEX idx_execution_traces_local_edit "
            "ON execution_traces(local_edit_spec_id)",
        ):
            connection.execute(statement)
        for trigger_name, table, action, message in (
            (
                "trg_canvas_rois_no_update",
                "canvas_rois",
                "UPDATE",
                "canvas ROIs are immutable",
            ),
            (
                "trg_canvas_rois_no_delete",
                "canvas_rois",
                "DELETE",
                "canvas ROIs are immutable",
            ),
            (
                "trg_canvas_mask_versions_no_update",
                "canvas_mask_versions",
                "UPDATE",
                "canvas mask versions are immutable",
            ),
            (
                "trg_canvas_mask_versions_no_delete",
                "canvas_mask_versions",
                "DELETE",
                "canvas mask versions are immutable",
            ),
            (
                "trg_local_edit_specs_no_update",
                "local_edit_specs",
                "UPDATE",
                "local edit specifications are immutable",
            ),
            (
                "trg_local_edit_specs_no_delete",
                "local_edit_specs",
                "DELETE",
                "local edit specifications are immutable",
            ),
        ):
            connection.execute(
                f"""
                CREATE TRIGGER {trigger_name}
                BEFORE {action} ON {table}
                BEGIN
                    SELECT RAISE(ABORT, '{message}');
                END
                """
            )

    @staticmethod
    def _migrate_v6_to_v7(connection: sqlite3.Connection) -> None:
        """Add one immutable receipt for every atomic local-edit composition."""
        connection.execute(
            """
            CREATE TABLE local_edit_compositions (
                id TEXT PRIMARY KEY,
                local_edit_spec_id TEXT NOT NULL,
                candidate_asset_id TEXT NOT NULL,
                result_asset_id TEXT NOT NULL UNIQUE,
                canvas_document_version_id TEXT NOT NULL UNIQUE,
                client_request_id TEXT NOT NULL UNIQUE,
                request_fingerprint TEXT NOT NULL,
                receipt_json TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(local_edit_spec_id)
                    REFERENCES local_edit_specs(id) ON DELETE RESTRICT,
                FOREIGN KEY(candidate_asset_id)
                    REFERENCES assets(id) ON DELETE RESTRICT,
                FOREIGN KEY(result_asset_id)
                    REFERENCES assets(id) ON DELETE RESTRICT,
                FOREIGN KEY(canvas_document_version_id)
                    REFERENCES canvas_document_versions(id) ON DELETE RESTRICT
            )
            """
        )
        connection.execute(
            "CREATE INDEX idx_local_edit_compositions_spec "
            "ON local_edit_compositions(local_edit_spec_id, created_at)"
        )
        connection.execute(
            "CREATE INDEX idx_local_edit_compositions_candidate "
            "ON local_edit_compositions(candidate_asset_id, created_at)"
        )
        for trigger_name, action in (
            ("trg_local_edit_compositions_no_update", "UPDATE"),
            ("trg_local_edit_compositions_no_delete", "DELETE"),
        ):
            connection.execute(
                f"""
                CREATE TRIGGER {trigger_name}
                BEFORE {action} ON local_edit_compositions
                BEGIN
                    SELECT RAISE(ABORT, 'local edit compositions are immutable');
                END
                """
            )

    @staticmethod
    def _migrate_v7_to_v8(connection: sqlite3.Connection) -> None:
        """Add durable spatial canvases without changing Fabric CanvasDocument v1."""
        connection.execute(
            """
            CREATE TABLE spatial_canvas_documents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 60),
                current_version_id TEXT,
                current_revision INTEGER NOT NULL DEFAULT 0 CHECK(current_revision >= 0),
                create_request_id TEXT NOT NULL UNIQUE,
                create_request_fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_opened_at TEXT NOT NULL,
                FOREIGN KEY(current_version_id)
                    REFERENCES spatial_canvas_scene_versions(id) ON DELETE RESTRICT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE spatial_canvas_scene_versions (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision >= 1),
                parent_version_id TEXT,
                client_request_id TEXT NOT NULL UNIQUE,
                request_fingerprint TEXT NOT NULL,
                scene_json TEXT NOT NULL,
                scene_sha256 TEXT NOT NULL,
                thumbnail_json TEXT NOT NULL,
                thumbnail_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(document_id)
                    REFERENCES spatial_canvas_documents(id) ON DELETE RESTRICT,
                FOREIGN KEY(parent_version_id)
                    REFERENCES spatial_canvas_scene_versions(id) ON DELETE RESTRICT,
                UNIQUE(document_id, revision)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE spatial_scene_requests (
                client_request_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                expected_revision INTEGER NOT NULL CHECK(expected_revision >= 1),
                request_fingerprint TEXT NOT NULL,
                resulting_version_id TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK(outcome IN ('created','unchanged')),
                created_at TEXT NOT NULL,
                FOREIGN KEY(document_id)
                    REFERENCES spatial_canvas_documents(id) ON DELETE RESTRICT,
                FOREIGN KEY(resulting_version_id)
                    REFERENCES spatial_canvas_scene_versions(id) ON DELETE RESTRICT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE spatial_scene_references (
                version_id TEXT NOT NULL,
                element_id TEXT NOT NULL,
                ref_kind TEXT NOT NULL CHECK(ref_kind IN (
                    'asset','result','task','product_profile_version','lineage_parent'
                )),
                ref_id TEXT NOT NULL,
                PRIMARY KEY(version_id, element_id, ref_kind),
                FOREIGN KEY(version_id)
                    REFERENCES spatial_canvas_scene_versions(id) ON DELETE RESTRICT
            )
            """
        )
        connection.execute(
            "CREATE INDEX idx_spatial_documents_recent "
            "ON spatial_canvas_documents(last_opened_at DESC, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_spatial_versions_document "
            "ON spatial_canvas_scene_versions(document_id, revision DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_spatial_requests_document "
            "ON spatial_scene_requests(document_id, created_at DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_spatial_references_lookup "
            "ON spatial_scene_references(ref_kind, ref_id)"
        )
        for trigger_name, table, action, message in (
            (
                "trg_spatial_scene_versions_no_update",
                "spatial_canvas_scene_versions",
                "UPDATE",
                "spatial canvas scene versions are immutable",
            ),
            (
                "trg_spatial_scene_versions_no_delete",
                "spatial_canvas_scene_versions",
                "DELETE",
                "spatial canvas scene versions are immutable",
            ),
            (
                "trg_spatial_scene_requests_no_update",
                "spatial_scene_requests",
                "UPDATE",
                "spatial scene requests are immutable",
            ),
            (
                "trg_spatial_scene_requests_no_delete",
                "spatial_scene_requests",
                "DELETE",
                "spatial scene requests are immutable",
            ),
            (
                "trg_spatial_scene_references_no_update",
                "spatial_scene_references",
                "UPDATE",
                "spatial scene references are immutable",
            ),
            (
                "trg_spatial_scene_references_no_delete",
                "spatial_scene_references",
                "DELETE",
                "spatial scene references are immutable",
            ),
        ):
            connection.execute(
                f"""
                CREATE TRIGGER {trigger_name}
                BEFORE {action} ON {table}
                BEGIN
                    SELECT RAISE(ABORT, '{message}');
                END
                """
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
                                elif current_version == 3:
                                    if self._v4_objects_present(connection):
                                        issues = self._v4_contract_issues(connection)
                                        if issues:
                                            raise PartialSchemaError(
                                                "Detected an incomplete v4 ledger while schema metadata says v3; "
                                                "the database was not changed. Restore the automatic backup or "
                                                f"repair these objects first: {' | '.join(issues)}"
                                            )
                                        repair = "recovered complete v4 schema with stale v3 metadata"
                                        self.last_schema_repair = (
                                            f"{self.last_schema_repair}; {repair}"
                                            if self.last_schema_repair else repair
                                        )
                                    else:
                                        self._migrate_v3_to_v4(connection)
                                    current_version = 4
                                elif current_version == 4:
                                    if self._v5_objects_present(connection):
                                        issues = self._v5_contract_issues(connection)
                                        if issues:
                                            raise PartialSchemaError(
                                                "Detected an incomplete v5 ledger while schema metadata says v4; "
                                                "the database was not changed. Restore the automatic backup or "
                                                f"repair these objects first: {' | '.join(issues)}"
                                            )
                                        repair = "recovered complete v5 schema with stale v4 metadata"
                                        self.last_schema_repair = (
                                            f"{self.last_schema_repair}; {repair}"
                                            if self.last_schema_repair else repair
                                        )
                                    else:
                                        self._migrate_v4_to_v5(connection)
                                    current_version = 5
                                elif current_version == 5:
                                    if self._v6_objects_present(connection):
                                        issues = self._v6_contract_issues(connection)
                                        if issues:
                                            raise PartialSchemaError(
                                                "Detected an incomplete v6 ledger while schema metadata says v5; "
                                                "the database was not changed. Restore the automatic backup or "
                                                f"repair these objects first: {' | '.join(issues)}"
                                            )
                                        repair = "recovered complete v6 schema with stale v5 metadata"
                                        self.last_schema_repair = (
                                            f"{self.last_schema_repair}; {repair}"
                                            if self.last_schema_repair else repair
                                        )
                                    else:
                                        self._migrate_v5_to_v6(connection)
                                    current_version = 6
                                elif current_version == 6:
                                    if self._v7_objects_present(connection):
                                        issues = self._v7_contract_issues(connection)
                                        if issues:
                                            raise PartialSchemaError(
                                                "Detected an incomplete v7 ledger while schema metadata says v6; "
                                                "the database was not changed. Restore the automatic backup or "
                                                f"repair these objects first: {' | '.join(issues)}"
                                            )
                                        repair = "recovered complete v7 schema with stale v6 metadata"
                                        self.last_schema_repair = (
                                            f"{self.last_schema_repair}; {repair}"
                                            if self.last_schema_repair else repair
                                        )
                                    else:
                                        self._migrate_v6_to_v7(connection)
                                    current_version = 7
                                elif current_version == 7:
                                    if self._v8_objects_present(connection):
                                        issues = self._v8_contract_issues(connection)
                                        if issues:
                                            raise PartialSchemaError(
                                                "Detected an incomplete v8 ledger while schema metadata says v7; "
                                                "the database was not changed. Restore the automatic backup or "
                                                f"repair these objects first: {' | '.join(issues)}"
                                            )
                                        repair = "recovered complete v8 schema with stale v7 metadata"
                                        self.last_schema_repair = (
                                            f"{self.last_schema_repair}; {repair}"
                                            if self.last_schema_repair else repair
                                        )
                                    else:
                                        self._migrate_v7_to_v8(connection)
                                    current_version = 8
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
            "canvas_versions": [
                str(row["version_id"])
                for row in connection.execute(
                    "SELECT DISTINCT version_id FROM canvas_version_sources "
                    "WHERE source_asset_id = ?",
                    (asset_id,),
                )
            ],
            "spatial_scene_versions": [
                str(row["version_id"])
                for row in connection.execute(
                    "SELECT DISTINCT version_id FROM spatial_scene_references "
                    "WHERE ref_kind = 'asset' AND ref_id = ?",
                    (asset_id,),
                )
            ],
            "product_profile_versions": [
                str(row["version_id"])
                for row in connection.execute(
                    "SELECT DISTINCT version_id FROM product_profile_version_assets "
                    "WHERE asset_id = ?",
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

    @staticmethod
    def _canvas_version_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["document"] = decode_json(item.pop("document_json", "{}"), {})
        item.pop("request_fingerprint", None)
        return item

    @staticmethod
    def _canvas_proxy_manifest(document: Mapping[str, Any]) -> list[dict[str, Any]]:
        proxies: list[dict[str, Any]] = []
        for layer in document.get("layers") or []:
            source = layer["source"]
            proxy_ref = str(source["proxy_ref"])
            match = _CANVAS_PROXY_PATTERN.fullmatch(proxy_ref)
            if match is None:
                raise ValueError("stored canvas proxy_ref is invalid")
            max_edge = int(match.group(2))
            source_id = str(source["id"])
            proxies.append({
                "layer_id": str(layer["id"]),
                "source_kind": str(source["kind"]),
                "source_id": source_id,
                "proxy_ref": proxy_ref,
                "url": f"/api/assets/{source_id}/thumbnail?size={max_edge}",
                "max_edge": max_edge,
                "authoritative_source": "assets.id",
                "rebuildable": True,
                "cache_is_authoritative": False,
            })
        return proxies

    @classmethod
    def _canvas_result(
        cls,
        document_row: sqlite3.Row,
        version_row: sqlite3.Row,
        *,
        replayed: bool,
    ) -> dict[str, Any]:
        version = cls._canvas_version_row(version_row)
        document = version.pop("document")
        return {
            "id": str(document_row["id"]),
            "draft_id": str(document_row["draft_id"]),
            "current_revision": int(document_row["current_revision"]),
            "current_version_id": document_row["current_version_id"],
            "document": document,
            "version": version,
            "proxies": cls._canvas_proxy_manifest(document),
            "replayed": replayed,
        }

    def get_canvas_document(self, mode: str) -> dict[str, Any] | None:
        if mode not in WORKFLOW_DRAFT_IDS:
            raise ValueError(f"unsupported workflow mode: {mode}")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT c.* FROM canvas_documents c
                JOIN workflow_drafts d ON d.id = c.draft_id
                WHERE d.mode = ?
                """,
                (mode,),
            ).fetchone()
            if row is None:
                return None
            version = connection.execute(
                "SELECT * FROM canvas_document_versions WHERE id = ?",
                (row["current_version_id"],),
            ).fetchone()
            if version is None:
                raise LedgerSchemaError(f"canvas {row['id']} has no current version")
        return self._canvas_result(row, version, replayed=False)

    def get_canvas_document_version(self, version_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM canvas_document_versions WHERE id = ?",
                (str(version_id),),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown canvas document version: {version_id}")
        return self._canvas_version_row(row)

    @staticmethod
    def _validate_canvas_sources(
        connection: sqlite3.Connection,
        draft: sqlite3.Row,
        document: Mapping[str, Any],
    ) -> None:
        sources = [layer["source"] for layer in document["layers"]]
        source_ids = list(dict.fromkeys(str(source["id"]) for source in sources))
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            rows = connection.execute(
                f"SELECT id, role, width, height FROM assets WHERE id IN ({placeholders})",
                source_ids,
            ).fetchall()
            assets = {str(row["id"]): row for row in rows}
        else:
            assets = {}
        missing = [source_id for source_id in source_ids if source_id not in assets]
        if missing:
            raise KeyError(f"unknown canvas source assets: {', '.join(missing)}")

        for source in sources:
            asset = assets[str(source["id"])]
            role = str(asset["role"] or "")
            if source["kind"] == "asset" and role != "workspace_source":
                raise ValueError(f"canvas asset source {source['id']} is not a workspace source")
            if source["kind"] == "result" and not role.startswith("result_"):
                raise ValueError(f"canvas result source {source['id']} is not a result asset")
            width = int(asset["width"] or 0)
            height = int(asset["height"] or 0)
            if width > 0 and width != int(source["original_pixel_width"]):
                raise ValueError(f"canvas source {source['id']} width does not match the ledger")
            if height > 0 and height != int(source["original_pixel_height"]):
                raise ValueError(f"canvas source {source['id']} height does not match the ledger")

        asset_source_ids = [str(item) for item in document["source_asset_ids"]]
        if asset_source_ids:
            placeholders = ",".join("?" for _ in asset_source_ids)
            rows = connection.execute(
                f"""
                SELECT asset_id FROM asset_collection_members
                WHERE collection_id = ? AND status = 'active'
                  AND asset_id IN ({placeholders})
                """,
                (draft["collection_id"], *asset_source_ids),
            ).fetchall()
            active = {str(row["asset_id"]) for row in rows}
            outside = [asset_id for asset_id in asset_source_ids if asset_id not in active]
            if outside:
                raise ValueError(
                    "canvas source assets are outside the workflow collection: "
                    + ", ".join(outside)
                )

    def save_canvas_document(
        self,
        mode: str,
        *,
        expected_revision: int,
        client_request_id: str,
        document: Mapping[str, Any],
    ) -> dict[str, Any]:
        if mode not in WORKFLOW_DRAFT_IDS:
            raise ValueError(f"unsupported workflow mode: {mode}")
        expected_revision = _canvas_integer(
            expected_revision, "expected_revision", minimum=0
        )
        request_id = str(client_request_id or "").strip()
        version_id = idempotent_id("canvasver", request_id)
        normalized = normalize_canvas_document(document)
        if int(normalized["revision"]) != expected_revision:
            raise ValueError("CanvasDocument.revision must equal expected_revision")
        fingerprint = hashlib.sha256(canonical_json({
            "mode": mode,
            "expected_revision": expected_revision,
            "document": normalized,
        }).encode("utf-8")).hexdigest()
        replayed = False
        result_document_id = str(normalized["id"])

        with self._immediate_connection() as connection:
            prior_version = connection.execute(
                "SELECT * FROM canvas_document_versions WHERE client_request_id = ?",
                (request_id,),
            ).fetchone()
            if prior_version is not None:
                if str(prior_version["request_fingerprint"]) != fingerprint:
                    raise IdempotencyConflictError(
                        "client_request_id already belongs to a different canvas save"
                    )
                prior_document = connection.execute(
                    """
                    SELECT c.* FROM canvas_documents c
                    JOIN workflow_drafts d ON d.id = c.draft_id
                    WHERE c.id = ? AND d.mode = ?
                    """,
                    (prior_version["document_id"], mode),
                ).fetchone()
                if prior_document is None:
                    raise IdempotencyConflictError(
                        "client_request_id belongs to a different canvas workflow"
                    )
                replayed = True
                document_row = prior_document
                version_row = prior_version
            else:
                draft = connection.execute(
                    "SELECT * FROM workflow_drafts WHERE mode = ?", (mode,)
                ).fetchone()
                if draft is None:
                    raise LedgerSchemaError(f"missing workflow draft: {mode}")
                document_row = connection.execute(
                    "SELECT * FROM canvas_documents WHERE draft_id = ?",
                    (draft["id"],),
                ).fetchone()
                if document_row is None:
                    if expected_revision != 0:
                        raise CanvasRevisionConflictError(
                            f"canvas for {mode} is revision 0, not {expected_revision}",
                            {"id": None, "revision": 0, "version_id": None},
                        )
                    duplicate = connection.execute(
                        "SELECT id, current_revision, current_version_id FROM canvas_documents WHERE id = ?",
                        (result_document_id,),
                    ).fetchone()
                    if duplicate is not None:
                        raise ValueError("CanvasDocument.id already belongs to another workflow")
                    parent_version_id = None
                    created_at = utc_now()
                else:
                    current_revision = int(document_row["current_revision"])
                    if str(document_row["id"]) != result_document_id:
                        raise ValueError("CanvasDocument.id cannot change between versions")
                    if current_revision != expected_revision:
                        raise CanvasRevisionConflictError(
                            f"canvas {result_document_id} is revision {current_revision}, "
                            f"not {expected_revision}",
                            {
                                "id": result_document_id,
                                "revision": current_revision,
                                "version_id": document_row["current_version_id"],
                            },
                        )
                    parent_version_id = document_row["current_version_id"]
                    created_at = str(document_row["created_at"])

                self._validate_canvas_sources(connection, draft, normalized)
                next_revision = expected_revision + 1
                now = utc_now()
                stored_document = json.loads(canonical_json(normalized))
                stored_document["revision"] = next_revision
                stored_document["created_at"] = created_at
                stored_document["updated_at"] = now
                stored_json = canonical_json(stored_document)
                document_sha256 = hashlib.sha256(stored_json.encode("utf-8")).hexdigest()

                if document_row is None:
                    connection.execute(
                        """
                        INSERT INTO canvas_documents(
                            id, draft_id, current_version_id, current_revision,
                            created_at, updated_at
                        ) VALUES(?, ?, NULL, 0, ?, ?)
                        """,
                        (result_document_id, draft["id"], created_at, now),
                    )
                connection.execute(
                    """
                    INSERT INTO canvas_document_versions(
                        id, document_id, revision, parent_version_id,
                        client_request_id, request_fingerprint, document_json,
                        document_sha256, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id, result_document_id, next_revision, parent_version_id,
                        request_id, fingerprint, stored_json, document_sha256, now,
                    ),
                )
                for layer in stored_document["layers"]:
                    source = layer["source"]
                    connection.execute(
                        """
                        INSERT INTO canvas_version_sources(
                            version_id, layer_id, source_kind, source_asset_id,
                            proxy_ref, original_pixel_width, original_pixel_height
                        ) VALUES(?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            version_id, layer["id"], source["kind"], source["id"],
                            source["proxy_ref"], source["original_pixel_width"],
                            source["original_pixel_height"],
                        ),
                    )
                connection.execute(
                    """
                    UPDATE canvas_documents
                    SET current_version_id = ?, current_revision = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (version_id, next_revision, now, result_document_id),
                )
                document_row = connection.execute(
                    "SELECT * FROM canvas_documents WHERE id = ?", (result_document_id,)
                ).fetchone()
                version_row = connection.execute(
                    "SELECT * FROM canvas_document_versions WHERE id = ?", (version_id,)
                ).fetchone()
                assert document_row is not None and version_row is not None

        return self._canvas_result(document_row, version_row, replayed=replayed)

    @staticmethod
    def _spatial_canvas_name(value: Any, fallback: str = "未命名画布") -> str:
        name = re.sub(r"\s+", " ", str(value or "").strip())[:60]
        return name or fallback

    @staticmethod
    def _spatial_scene_thumbnail_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        thumbnail_json = str(item.pop("thumbnail_json", ""))
        expected_sha256 = str(item.get("thumbnail_sha256") or "")
        actual_sha256 = hashlib.sha256(thumbnail_json.encode("utf-8")).hexdigest()
        if not thumbnail_json or actual_sha256 != expected_sha256:
            raise SpatialSceneCorruptedError(
                f"spatial scene version {item.get('id')} failed its thumbnail SHA-256 receipt"
            )
        try:
            thumbnail = json.loads(thumbnail_json)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SpatialSceneCorruptedError(
                f"spatial scene version {item.get('id')} has an invalid thumbnail"
            ) from exc
        if not isinstance(thumbnail, Mapping):
            raise SpatialSceneCorruptedError(
                f"spatial scene version {item.get('id')} has an invalid thumbnail"
            )
        item["thumbnail"] = dict(thumbnail)
        item.pop("request_fingerprint", None)
        return item

    @classmethod
    def _spatial_scene_version_row(cls, row: sqlite3.Row) -> dict[str, Any]:
        item = cls._spatial_scene_thumbnail_row(row)
        thumbnail = item.pop("thumbnail")
        scene_json = str(item.pop("scene_json", ""))
        expected_sha256 = str(item.get("scene_sha256") or "")
        actual_sha256 = hashlib.sha256(scene_json.encode("utf-8")).hexdigest()
        if not scene_json or actual_sha256 != expected_sha256:
            raise SpatialSceneCorruptedError(
                f"spatial scene version {item.get('id')} failed its SHA-256 receipt"
            )
        try:
            decoded_scene = json.loads(scene_json)
            scene = normalize_spatial_scene(decoded_scene)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SpatialSceneCorruptedError(
                f"spatial scene version {item.get('id')} is not a valid scene"
            ) from exc
        if canonical_json(spatial_scene_thumbnail(scene)) != canonical_json(thumbnail):
            raise SpatialSceneCorruptedError(
                f"spatial scene version {item.get('id')} thumbnail does not match its scene"
            )
        item["scene"] = scene
        item["thumbnail"] = thumbnail
        return item

    @classmethod
    def _spatial_canvas_result(
        cls,
        document_row: sqlite3.Row,
        version_row: sqlite3.Row,
        *,
        include_scene: bool,
        replayed: bool = False,
        unchanged: bool = False,
    ) -> dict[str, Any]:
        version = (
            cls._spatial_scene_version_row(version_row)
            if include_scene else cls._spatial_scene_thumbnail_row(version_row)
        )
        scene = version.pop("scene", None)
        thumbnail = version.pop("thumbnail")
        version.pop("scene_json", None)
        result = {
            "id": str(document_row["id"]),
            "name": str(document_row["name"]),
            "created_at": str(document_row["created_at"]),
            "updated_at": str(document_row["updated_at"]),
            "last_opened_at": str(document_row["last_opened_at"]),
            "current_revision": int(document_row["current_revision"]),
            "current_version_id": str(document_row["current_version_id"]),
            "version": version,
            "thumbnail": thumbnail,
            "summary": {
                key: thumbnail.get(key, 0)
                for key in ("element_count", "image_count", "video_count", "frame_count")
            },
            "replayed": replayed,
            "unchanged": unchanged,
        }
        if include_scene:
            result["scene"] = scene
        return result

    def list_spatial_canvases(self, *, limit: int = 100) -> list[dict[str, Any]]:
        limit = _canvas_integer(limit, "limit", minimum=1, maximum=200)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM spatial_canvas_documents
                ORDER BY last_opened_at DESC, updated_at DESC, id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            results: list[dict[str, Any]] = []
            for row in rows:
                version = connection.execute(
                    """
                    SELECT id, document_id, revision, parent_version_id,
                           client_request_id, request_fingerprint, scene_sha256,
                           thumbnail_json, thumbnail_sha256, created_at
                    FROM spatial_canvas_scene_versions WHERE id = ?
                    """,
                    (row["current_version_id"],),
                ).fetchone()
                if version is None:
                    raise SpatialSceneCorruptedError(
                        f"spatial canvas {row['id']} has no current scene version"
                    )
                results.append(
                    self._spatial_canvas_result(row, version, include_scene=False)
                )
        return results

    def get_spatial_canvas(self, document_id: str) -> dict[str, Any]:
        document_id = _canvas_id(document_id, "spatial_canvas_id")
        with self._connection() as connection:
            document = connection.execute(
                "SELECT * FROM spatial_canvas_documents WHERE id = ?", (document_id,)
            ).fetchone()
            if document is None:
                raise KeyError(f"unknown spatial canvas: {document_id}")
            version = connection.execute(
                "SELECT * FROM spatial_canvas_scene_versions WHERE id = ?",
                (document["current_version_id"],),
            ).fetchone()
            if version is None:
                raise SpatialSceneCorruptedError(
                    f"spatial canvas {document_id} has no current scene version"
                )
        return self._spatial_canvas_result(document, version, include_scene=True)

    def open_spatial_canvas(self, document_id: str) -> dict[str, Any]:
        document_id = _canvas_id(document_id, "spatial_canvas_id")
        now = utc_now()
        with self._immediate_connection() as connection:
            updated = connection.execute(
                "UPDATE spatial_canvas_documents SET last_opened_at = ? WHERE id = ?",
                (now, document_id),
            )
            if updated.rowcount != 1:
                raise KeyError(f"unknown spatial canvas: {document_id}")
            document = connection.execute(
                "SELECT * FROM spatial_canvas_documents WHERE id = ?", (document_id,)
            ).fetchone()
            assert document is not None
            version = connection.execute(
                "SELECT * FROM spatial_canvas_scene_versions WHERE id = ?",
                (document["current_version_id"],),
            ).fetchone()
            if version is None:
                raise SpatialSceneCorruptedError(
                    f"spatial canvas {document_id} has no current scene version"
                )
        return self._spatial_canvas_result(document, version, include_scene=True)

    def create_spatial_canvas(
        self, *, name: str, client_request_id: str
    ) -> dict[str, Any]:
        normalized_name = self._spatial_canvas_name(name)
        request_id = str(client_request_id or "").strip()
        document_id = idempotent_id("spatial", request_id)
        version_id = idempotent_id("spatialver", f"{request_id}:initial")
        fingerprint = hashlib.sha256(canonical_json({
            "name": normalized_name,
        }).encode("utf-8")).hexdigest()
        scene = normalize_spatial_scene(empty_spatial_scene())
        scene_json = canonical_json(scene)
        scene_sha256 = hashlib.sha256(scene_json.encode("utf-8")).hexdigest()
        thumbnail_json = canonical_json(spatial_scene_thumbnail(scene))
        thumbnail_sha256 = hashlib.sha256(thumbnail_json.encode("utf-8")).hexdigest()
        replayed = False

        with self._immediate_connection() as connection:
            prior = connection.execute(
                "SELECT * FROM spatial_canvas_documents WHERE create_request_id = ?",
                (request_id,),
            ).fetchone()
            if prior is not None:
                if str(prior["create_request_fingerprint"]) != fingerprint:
                    raise IdempotencyConflictError(
                        "client_request_id already belongs to a different spatial canvas"
                    )
                document = prior
                version = connection.execute(
                    "SELECT * FROM spatial_canvas_scene_versions WHERE id = ?",
                    (document["current_version_id"],),
                ).fetchone()
                if version is None:
                    raise SpatialSceneCorruptedError(
                        f"spatial canvas {document['id']} has no current scene version"
                    )
                replayed = True
            else:
                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO spatial_canvas_documents(
                        id, name, current_version_id, current_revision,
                        create_request_id, create_request_fingerprint,
                        created_at, updated_at, last_opened_at
                    ) VALUES(?, ?, NULL, 0, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id, normalized_name, request_id, fingerprint,
                        now, now, now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO spatial_canvas_scene_versions(
                        id, document_id, revision, parent_version_id,
                        client_request_id, request_fingerprint, scene_json,
                        scene_sha256, thumbnail_json, thumbnail_sha256, created_at
                    ) VALUES(?, ?, 1, NULL, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id, document_id, f"{request_id}:initial", fingerprint,
                        scene_json, scene_sha256, thumbnail_json, thumbnail_sha256, now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE spatial_canvas_documents
                    SET current_version_id = ?, current_revision = 1
                    WHERE id = ?
                    """,
                    (version_id, document_id),
                )
                document = connection.execute(
                    "SELECT * FROM spatial_canvas_documents WHERE id = ?", (document_id,)
                ).fetchone()
                version = connection.execute(
                    "SELECT * FROM spatial_canvas_scene_versions WHERE id = ?", (version_id,)
                ).fetchone()
                assert document is not None and version is not None
        return self._spatial_canvas_result(
            document, version, include_scene=True, replayed=replayed
        )

    def rename_spatial_canvas(self, document_id: str, name: str) -> dict[str, Any]:
        document_id = _canvas_id(document_id, "spatial_canvas_id")
        normalized_name = self._spatial_canvas_name(name)
        now = utc_now()
        with self._immediate_connection() as connection:
            updated = connection.execute(
                """
                UPDATE spatial_canvas_documents
                SET name = ?, updated_at = ?
                WHERE id = ?
                """,
                (normalized_name, now, document_id),
            )
            if updated.rowcount != 1:
                raise KeyError(f"unknown spatial canvas: {document_id}")
            document = connection.execute(
                "SELECT * FROM spatial_canvas_documents WHERE id = ?", (document_id,)
            ).fetchone()
            assert document is not None
            version = connection.execute(
                "SELECT * FROM spatial_canvas_scene_versions WHERE id = ?",
                (document["current_version_id"],),
            ).fetchone()
            if version is None:
                raise SpatialSceneCorruptedError(
                    f"spatial canvas {document_id} has no current scene version"
                )
        return self._spatial_canvas_result(document, version, include_scene=False)

    @staticmethod
    def _validate_spatial_references(
        connection: sqlite3.Connection, references: list[dict[str, str]]
    ) -> None:
        for reference in references:
            ref_kind = reference["ref_kind"]
            ref_id = reference["ref_id"]
            found = True
            if ref_kind == "asset":
                found = connection.execute(
                    "SELECT 1 FROM assets WHERE id = ?", (ref_id,)
                ).fetchone() is not None
            elif ref_kind == "result":
                found = (
                    connection.execute(
                        "SELECT 1 FROM assets WHERE id = ?", (ref_id,)
                    ).fetchone() is not None
                    or connection.execute(
                        "SELECT 1 FROM generations WHERE id = ?", (ref_id,)
                    ).fetchone() is not None
                )
            elif ref_kind == "lineage_parent":
                found = connection.execute(
                    "SELECT 1 FROM assets WHERE id = ?", (ref_id,)
                ).fetchone() is not None
            elif ref_kind == "task":
                found = connection.execute(
                    "SELECT 1 FROM jobs WHERE id = ?", (ref_id,)
                ).fetchone() is not None
            elif ref_kind == "product_profile_version":
                found = connection.execute(
                    "SELECT 1 FROM product_profile_versions WHERE id = ?", (ref_id,)
                ).fetchone() is not None
            if not found:
                raise KeyError(f"unknown spatial {ref_kind} reference: {ref_id}")

    def save_spatial_canvas_scene(
        self,
        document_id: str,
        *,
        expected_revision: int,
        client_request_id: str,
        scene: Mapping[str, Any],
    ) -> dict[str, Any]:
        document_id = _canvas_id(document_id, "spatial_canvas_id")
        expected_revision = _canvas_integer(
            expected_revision, "expected_revision", minimum=1
        )
        request_id = str(client_request_id or "").strip()
        version_id = idempotent_id("spatialver", request_id)
        normalized_scene = normalize_spatial_scene(scene)
        scene_json = canonical_json(normalized_scene)
        scene_sha256 = hashlib.sha256(scene_json.encode("utf-8")).hexdigest()
        thumbnail_json = canonical_json(spatial_scene_thumbnail(normalized_scene))
        thumbnail_sha256 = hashlib.sha256(thumbnail_json.encode("utf-8")).hexdigest()
        references = spatial_scene_references(normalized_scene)
        fingerprint = hashlib.sha256(canonical_json({
            "document_id": document_id,
            "expected_revision": expected_revision,
            "scene_sha256": scene_sha256,
        }).encode("utf-8")).hexdigest()

        with self._immediate_connection() as connection:
            prior_request = connection.execute(
                "SELECT * FROM spatial_scene_requests WHERE client_request_id = ?",
                (request_id,),
            ).fetchone()
            if prior_request is not None:
                if (
                    str(prior_request["document_id"]) != document_id
                    or str(prior_request["request_fingerprint"]) != fingerprint
                ):
                    raise IdempotencyConflictError(
                        "client_request_id already belongs to a different spatial scene save"
                    )
                document = connection.execute(
                    "SELECT * FROM spatial_canvas_documents WHERE id = ?", (document_id,)
                ).fetchone()
                version = connection.execute(
                    "SELECT * FROM spatial_canvas_scene_versions WHERE id = ?",
                    (prior_request["resulting_version_id"],),
                ).fetchone()
                if document is None or version is None:
                    raise SpatialSceneCorruptedError(
                        f"spatial scene request {request_id} has broken references"
                    )
                return self._spatial_canvas_result(
                    document,
                    version,
                    include_scene=True,
                    replayed=True,
                    unchanged=str(prior_request["outcome"]) == "unchanged",
                )

            document = connection.execute(
                "SELECT * FROM spatial_canvas_documents WHERE id = ?", (document_id,)
            ).fetchone()
            if document is None:
                raise KeyError(f"unknown spatial canvas: {document_id}")
            current_revision = int(document["current_revision"])
            if current_revision != expected_revision:
                raise SpatialCanvasRevisionConflictError(
                    f"spatial canvas {document_id} is revision {current_revision}, "
                    f"not {expected_revision}",
                    {
                        "id": document_id,
                        "current_revision": current_revision,
                        "current_version_id": str(document["current_version_id"]),
                    },
                )
            self._validate_spatial_references(connection, references)
            current_version = connection.execute(
                "SELECT * FROM spatial_canvas_scene_versions WHERE id = ?",
                (document["current_version_id"],),
            ).fetchone()
            if current_version is None:
                raise SpatialSceneCorruptedError(
                    f"spatial canvas {document_id} has no current scene version"
                )
            now = utc_now()
            if str(current_version["scene_sha256"]) == scene_sha256:
                connection.execute(
                    """
                    INSERT INTO spatial_scene_requests(
                        client_request_id, document_id, expected_revision,
                        request_fingerprint, resulting_version_id, outcome, created_at
                    ) VALUES(?, ?, ?, ?, ?, 'unchanged', ?)
                    """,
                    (
                        request_id, document_id, expected_revision, fingerprint,
                        current_version["id"], now,
                    ),
                )
                return self._spatial_canvas_result(
                    document,
                    current_version,
                    include_scene=True,
                    replayed=False,
                    unchanged=True,
                )

            next_revision = current_revision + 1
            connection.execute(
                """
                INSERT INTO spatial_canvas_scene_versions(
                    id, document_id, revision, parent_version_id,
                    client_request_id, request_fingerprint, scene_json,
                    scene_sha256, thumbnail_json, thumbnail_sha256, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id, document_id, next_revision,
                    current_version["id"], request_id, fingerprint, scene_json,
                    scene_sha256, thumbnail_json, thumbnail_sha256, now,
                ),
            )
            for reference in references:
                connection.execute(
                    """
                    INSERT INTO spatial_scene_references(
                        version_id, element_id, ref_kind, ref_id
                    ) VALUES(?, ?, ?, ?)
                    """,
                    (
                        version_id, reference["element_id"],
                        reference["ref_kind"], reference["ref_id"],
                    ),
                )
            connection.execute(
                """
                INSERT INTO spatial_scene_requests(
                    client_request_id, document_id, expected_revision,
                    request_fingerprint, resulting_version_id, outcome, created_at
                ) VALUES(?, ?, ?, ?, ?, 'created', ?)
                """,
                (request_id, document_id, expected_revision, fingerprint, version_id, now),
            )
            connection.execute(
                """
                UPDATE spatial_canvas_documents
                SET current_version_id = ?, current_revision = ?, updated_at = ?
                WHERE id = ?
                """,
                (version_id, next_revision, now, document_id),
            )
            document = connection.execute(
                "SELECT * FROM spatial_canvas_documents WHERE id = ?", (document_id,)
            ).fetchone()
            version = connection.execute(
                "SELECT * FROM spatial_canvas_scene_versions WHERE id = ?", (version_id,)
            ).fetchone()
            assert document is not None and version is not None
        return self._spatial_canvas_result(document, version, include_scene=True)

    def get_spatial_canvas_version(self, version_id: str) -> dict[str, Any]:
        version_id = _canvas_id(version_id, "spatial_scene_version_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM spatial_canvas_scene_versions WHERE id = ?", (version_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown spatial scene version: {version_id}")
        return self._spatial_scene_version_row(row)

    @staticmethod
    def _canvas_roi_row(
        row: sqlite3.Row | Mapping[str, Any], *, replayed: bool = False
    ) -> dict[str, Any]:
        item = dict(row)
        item["rect"] = {
            "x": int(item.pop("x")),
            "y": int(item.pop("y")),
            "width": int(item.pop("width")),
            "height": int(item.pop("height")),
        }
        item.pop("request_fingerprint", None)
        item["replayed"] = replayed
        return item

    def get_canvas_roi(self, roi_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM canvas_rois WHERE id = ?", (str(roi_id),)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown canvas ROI: {roi_id}")
        return self._canvas_roi_row(row)

    def list_canvas_rois(
        self,
        canvas_document_version_id: str,
        *,
        source_layer_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        version_id = str(canvas_document_version_id or "").strip()
        layer_id = str(source_layer_id or "").strip() or None
        limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            version = connection.execute(
                "SELECT id FROM canvas_document_versions WHERE id = ?", (version_id,)
            ).fetchone()
            if version is None:
                raise KeyError(f"unknown canvas document version: {version_id}")
            if layer_id is None:
                rows = connection.execute(
                    """
                    SELECT * FROM canvas_rois
                    WHERE canvas_document_version_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (version_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM canvas_rois
                    WHERE canvas_document_version_id = ? AND source_layer_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (version_id, layer_id, limit),
                ).fetchall()
        return [self._canvas_roi_row(row) for row in rows]

    def create_canvas_roi(
        self,
        *,
        canvas_document_id: str,
        expected_canvas_revision: int,
        source_layer_id: str,
        coordinate_space: str,
        rect: Mapping[str, Any],
        purpose: str,
        client_request_id: str,
    ) -> dict[str, Any]:
        document_id = _canvas_id(canvas_document_id, "canvas_document_id")
        expected_revision = _canvas_integer(
            expected_canvas_revision, "expected_canvas_revision", minimum=0
        )
        layer_id = _canvas_id(source_layer_id, "source_layer_id")
        coordinate_space = str(coordinate_space or "").strip()
        purpose = str(purpose or "").strip()
        if purpose not in {"inpaint", "outpaint"}:
            raise ValueError("canvas ROI purpose must be inpaint or outpaint")
        expected_space = "source-pixel" if purpose == "inpaint" else "output-pixel"
        if coordinate_space != expected_space:
            raise ValueError(f"{purpose} ROI must use {expected_space} coordinates")
        rect_value = _canvas_object(
            rect,
            label="canvas ROI rect",
            required={"x", "y", "width", "height"},
        )
        normalized_rect = {
            "x": _canvas_integer(rect_value["x"], "canvas ROI rect.x", minimum=0),
            "y": _canvas_integer(rect_value["y"], "canvas ROI rect.y", minimum=0),
            "width": _canvas_integer(
                rect_value["width"], "canvas ROI rect.width", minimum=1, maximum=32768
            ),
            "height": _canvas_integer(
                rect_value["height"], "canvas ROI rect.height", minimum=1, maximum=32768
            ),
        }
        request_id = str(client_request_id or "").strip()
        roi_id = idempotent_id("roi", request_id)
        request_payload = {
            "canvas_document_id": document_id,
            "expected_canvas_revision": expected_revision,
            "source_layer_id": layer_id,
            "coordinate_space": coordinate_space,
            "rect": normalized_rect,
            "purpose": purpose,
        }
        fingerprint = hashlib.sha256(
            canonical_json(request_payload).encode("utf-8")
        ).hexdigest()
        replayed = False

        with self._immediate_connection() as connection:
            prior = connection.execute(
                "SELECT * FROM canvas_rois WHERE client_request_id = ?", (request_id,)
            ).fetchone()
            if prior is not None:
                if str(prior["request_fingerprint"]) != fingerprint:
                    raise IdempotencyConflictError(
                        "client_request_id already belongs to a different canvas ROI"
                    )
                row = prior
                replayed = True
            else:
                canvas = connection.execute(
                    "SELECT * FROM canvas_documents WHERE id = ?", (document_id,)
                ).fetchone()
                if canvas is None:
                    raise KeyError(f"unknown canvas document: {document_id}")
                current_revision = int(canvas["current_revision"])
                if current_revision != expected_revision:
                    raise CanvasRevisionConflictError(
                        f"canvas {document_id} is revision {current_revision}, "
                        f"not {expected_revision}",
                        {
                            "id": document_id,
                            "revision": current_revision,
                            "version_id": canvas["current_version_id"],
                        },
                    )
                version_id = str(canvas["current_version_id"])
                version = connection.execute(
                    "SELECT document_json FROM canvas_document_versions WHERE id = ?",
                    (version_id,),
                ).fetchone()
                if version is None:
                    raise LedgerSchemaError(f"canvas {document_id} has no current version")
                document = decode_json(version["document_json"], {})
                layer = next(
                    (item for item in document.get("layers") or [] if item.get("id") == layer_id),
                    None,
                )
                if layer is None:
                    raise KeyError(f"unknown canvas layer: {layer_id}")
                if purpose == "inpaint":
                    source = layer["source"]
                    if (
                        normalized_rect["x"] + normalized_rect["width"]
                        > int(source["original_pixel_width"])
                        or normalized_rect["y"] + normalized_rect["height"]
                        > int(source["original_pixel_height"])
                    ):
                        raise ValueError("inpaint ROI exceeds the source pixel bounds")
                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO canvas_rois(
                        id, canvas_document_version_id, source_layer_id,
                        coordinate_space, x, y, width, height, purpose,
                        client_request_id, request_fingerprint, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        roi_id, version_id, layer_id, coordinate_space,
                        normalized_rect["x"], normalized_rect["y"],
                        normalized_rect["width"], normalized_rect["height"], purpose,
                        request_id, fingerprint, now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM canvas_rois WHERE id = ?", (roi_id,)
                ).fetchone()
                assert row is not None
        return self._canvas_roi_row(row, replayed=replayed)

    @staticmethod
    def _canvas_mask_version_row(
        row: sqlite3.Row | Mapping[str, Any],
    ) -> dict[str, Any]:
        item = dict(row)
        item["definition"] = decode_json(item.pop("definition_json", "{}"), {})
        item.pop("request_fingerprint", None)
        return item

    @classmethod
    def _canvas_mask_result(
        cls,
        mask_row: sqlite3.Row | Mapping[str, Any],
        version_row: sqlite3.Row | Mapping[str, Any],
        *,
        replayed: bool,
    ) -> dict[str, Any]:
        return {
            "id": str(mask_row["id"]),
            "roi_id": str(mask_row["roi_id"]),
            "current_revision": int(mask_row["current_revision"]),
            "current_version_id": mask_row["current_version_id"],
            "version": cls._canvas_mask_version_row(version_row),
            "replayed": replayed,
        }

    def list_canvas_mask_versions(
        self, mask_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            mask = connection.execute(
                "SELECT id FROM canvas_masks WHERE id = ?", (str(mask_id),)
            ).fetchone()
            if mask is None:
                raise KeyError(f"unknown canvas mask: {mask_id}")
            rows = connection.execute(
                """
                SELECT * FROM canvas_mask_versions
                WHERE mask_id = ?
                ORDER BY revision DESC
                LIMIT ?
                """,
                (str(mask_id), limit),
            ).fetchall()
        return [self._canvas_mask_version_row(row) for row in rows]

    def get_canvas_mask(self, roi_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            mask = connection.execute(
                "SELECT * FROM canvas_masks WHERE roi_id = ?", (str(roi_id),)
            ).fetchone()
            if mask is None:
                raise KeyError(f"unknown canvas mask for ROI: {roi_id}")
            version = connection.execute(
                "SELECT * FROM canvas_mask_versions WHERE id = ?",
                (mask["current_version_id"],),
            ).fetchone()
        if version is None:
            raise LedgerSchemaError(f"canvas mask {mask['id']} has no current version")
        return self._canvas_mask_result(mask, version, replayed=False)

    def get_canvas_mask_version(self, version_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM canvas_mask_versions WHERE id = ?", (str(version_id),)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown canvas mask version: {version_id}")
        return self._canvas_mask_version_row(row)

    def save_canvas_mask(
        self,
        *,
        roi_id: str,
        expected_revision: int,
        definition: Mapping[str, Any],
        pixel_sha256: str,
        client_request_id: str,
    ) -> dict[str, Any]:
        roi_id = _canvas_id(roi_id, "roi_id")
        expected_revision = _canvas_integer(
            expected_revision, "expected_revision", minimum=0
        )
        normalized = normalize_canvas_mask_definition(definition)
        claimed_pixel_digest = str(pixel_sha256 or "").upper()
        if claimed_pixel_digest and re.fullmatch(r"[A-F0-9]{64}", claimed_pixel_digest) is None:
            raise ValueError("pixel_sha256 must be a SHA-256 digest")
        roi_for_digest = self.get_canvas_roi(roi_id)
        if normalized["coordinate_space"] != str(roi_for_digest["coordinate_space"]):
            raise ValueError("mask coordinate space must match its ROI")
        if str(roi_for_digest["purpose"]) != "inpaint":
            raise ValueError("outpaint write masks are derived locally")
        with self._connection() as validation_connection:
            source_for_digest = validation_connection.execute(
                """
                SELECT original_pixel_width, original_pixel_height
                FROM canvas_version_sources
                WHERE version_id = ? AND layer_id = ?
                """,
                (
                    roi_for_digest["canvas_document_version_id"],
                    roi_for_digest["source_layer_id"],
                ),
            ).fetchone()
        if source_for_digest is None:
            raise LedgerSchemaError("ROI source layer is missing from its canvas version")
        if (
            normalized["width"] != int(source_for_digest["original_pixel_width"])
            or normalized["height"] != int(source_for_digest["original_pixel_height"])
        ):
            raise ValueError("mask dimensions must match the ROI source layer")
        pixel_digest = canvas_mask_fingerprint(normalized, roi_for_digest["rect"])
        if claimed_pixel_digest and claimed_pixel_digest != pixel_digest:
            raise ValueError("pixel_sha256 does not match the rendered mask definition")
        request_id = str(client_request_id or "").strip()
        version_id = idempotent_id("maskver", request_id)
        mask_id = idempotent_id("mask", roi_id)
        fingerprint = hashlib.sha256(canonical_json({
            "roi_id": roi_id,
            "expected_revision": expected_revision,
            "definition": normalized,
            "pixel_sha256": pixel_digest,
        }).encode("utf-8")).hexdigest()
        replayed = False

        with self._immediate_connection() as connection:
            prior = connection.execute(
                "SELECT * FROM canvas_mask_versions WHERE client_request_id = ?",
                (request_id,),
            ).fetchone()
            if prior is not None:
                if str(prior["request_fingerprint"]) != fingerprint:
                    raise IdempotencyConflictError(
                        "client_request_id already belongs to a different canvas mask save"
                    )
                mask_row = connection.execute(
                    "SELECT * FROM canvas_masks WHERE id = ?", (prior["mask_id"],)
                ).fetchone()
                if mask_row is None or str(mask_row["roi_id"]) != roi_id:
                    raise IdempotencyConflictError(
                        "client_request_id belongs to a different canvas mask"
                    )
                version_row = prior
                replayed = True
            else:
                roi = connection.execute(
                    "SELECT * FROM canvas_rois WHERE id = ?", (roi_id,)
                ).fetchone()
                if roi is None:
                    raise KeyError(f"unknown canvas ROI: {roi_id}")
                if normalized["coordinate_space"] != str(roi["coordinate_space"]):
                    raise ValueError("mask coordinate space must match its ROI")
                if str(roi["purpose"]) != "inpaint":
                    raise ValueError("outpaint write masks are derived locally")
                source = connection.execute(
                    """
                    SELECT original_pixel_width, original_pixel_height
                    FROM canvas_version_sources
                    WHERE version_id = ? AND layer_id = ?
                    """,
                    (roi["canvas_document_version_id"], roi["source_layer_id"]),
                ).fetchone()
                if source is None:
                    raise LedgerSchemaError("ROI source layer is missing from its canvas version")
                if (
                    normalized["width"] != int(source["original_pixel_width"])
                    or normalized["height"] != int(source["original_pixel_height"])
                ):
                    raise ValueError("mask dimensions must match the ROI source layer")

                mask_row = connection.execute(
                    "SELECT * FROM canvas_masks WHERE roi_id = ?", (roi_id,)
                ).fetchone()
                if mask_row is None:
                    if expected_revision != 0:
                        raise CanvasRevisionConflictError(
                            f"canvas mask for {roi_id} is revision 0, not {expected_revision}",
                            {"id": None, "revision": 0, "version_id": None},
                        )
                    parent_version_id = None
                    created_at = utc_now()
                else:
                    current_revision = int(mask_row["current_revision"])
                    if current_revision != expected_revision:
                        raise CanvasRevisionConflictError(
                            f"canvas mask {mask_row['id']} is revision {current_revision}, "
                            f"not {expected_revision}",
                            {
                                "id": str(mask_row["id"]),
                                "revision": current_revision,
                                "version_id": mask_row["current_version_id"],
                            },
                        )
                    parent_version_id = mask_row["current_version_id"]
                    created_at = str(mask_row["created_at"])
                next_revision = expected_revision + 1
                now = utc_now()
                definition_json = canonical_json(normalized)
                definition_sha256 = hashlib.sha256(
                    definition_json.encode("utf-8")
                ).hexdigest().upper()
                if mask_row is None:
                    connection.execute(
                        """
                        INSERT INTO canvas_masks(
                            id, roi_id, current_version_id, current_revision,
                            created_at, updated_at
                        ) VALUES(?, ?, NULL, 0, ?, ?)
                        """,
                        (mask_id, roi_id, created_at, now),
                    )
                connection.execute(
                    """
                    INSERT INTO canvas_mask_versions(
                        id, mask_id, revision, parent_version_id,
                        client_request_id, request_fingerprint, definition_json,
                        definition_sha256, pixel_sha256, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id, mask_id, next_revision, parent_version_id,
                        request_id, fingerprint, definition_json, definition_sha256,
                        pixel_digest, now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE canvas_masks
                    SET current_version_id = ?, current_revision = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (version_id, next_revision, now, mask_id),
                )
                mask_row = connection.execute(
                    "SELECT * FROM canvas_masks WHERE id = ?", (mask_id,)
                ).fetchone()
                version_row = connection.execute(
                    "SELECT * FROM canvas_mask_versions WHERE id = ?", (version_id,)
                ).fetchone()
                assert mask_row is not None and version_row is not None
        return self._canvas_mask_result(mask_row, version_row, replayed=replayed)

    @staticmethod
    def _local_edit_spec_row(
        row: sqlite3.Row | Mapping[str, Any], *, replayed: bool = False
    ) -> dict[str, Any]:
        item = dict(row)
        item["contract"] = decode_json(item.pop("contract_json", "{}"), {})
        item.pop("request_fingerprint", None)
        item["replayed"] = replayed
        return item

    def get_local_edit_spec(self, spec_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM local_edit_specs WHERE id = ?", (str(spec_id),)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown local edit spec: {spec_id}")
        return self._local_edit_spec_row(row)

    def find_local_edit_spec(
        self,
        *,
        canvas_document_version_id: str,
        source_layer_id: str,
        roi_id: str,
        mask_version_id: str | None,
        mode: str,
    ) -> dict[str, Any] | None:
        normalized_mode = str(mode or "").strip()
        if normalized_mode not in {"inpaint", "outpaint"}:
            raise ValueError("local edit mode must be inpaint or outpaint")
        if normalized_mode == "inpaint" and not str(mask_version_id or "").strip():
            raise ValueError("inpaint spec lookup requires a mask version")
        if normalized_mode == "outpaint" and mask_version_id is not None:
            raise ValueError("outpaint spec lookup cannot include a mask version")
        params: list[Any] = [
            str(canvas_document_version_id),
            str(source_layer_id),
            str(roi_id),
            normalized_mode,
        ]
        mask_clause = "mask_version_id IS NULL"
        if mask_version_id is not None:
            mask_clause = "mask_version_id = ?"
            params.append(str(mask_version_id))
        with self._connection() as connection:
            row = connection.execute(
                f"""
                SELECT *
                FROM local_edit_specs
                WHERE canvas_document_version_id = ?
                  AND source_layer_id = ?
                  AND roi_id = ?
                  AND mode = ?
                  AND {mask_clause}
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
        return self._local_edit_spec_row(row) if row is not None else None

    def create_local_edit_spec(
        self,
        *,
        contract: Mapping[str, Any],
        source_pixel_sha256: str,
        client_request_id: str,
    ) -> dict[str, Any]:
        normalized = normalize_local_edit_contract(contract)
        authoritative_pixel_sha256 = str(source_pixel_sha256 or "").upper()
        if re.fullmatch(r"[A-F0-9]{64}", authoritative_pixel_sha256) is None:
            raise ValueError("source_pixel_sha256 must be an authoritative SHA-256 digest")
        if normalized["source_pixel_sha256"] != authoritative_pixel_sha256:
            raise ValueError(
                "local edit source pixel fingerprint does not match the decoded source"
            )
        request_id = str(client_request_id or "").strip()
        spec_id = idempotent_id("editspec", request_id)
        stored_json = canonical_json(normalized)
        contract_sha256 = hashlib.sha256(stored_json.encode("utf-8")).hexdigest().upper()
        fingerprint = hashlib.sha256(canonical_json({
            "client_request_id": request_id,
            "contract": normalized,
        }).encode("utf-8")).hexdigest()
        replayed = False

        with self._immediate_connection() as connection:
            prior = connection.execute(
                "SELECT * FROM local_edit_specs WHERE client_request_id = ?",
                (request_id,),
            ).fetchone()
            if prior is not None:
                if str(prior["request_fingerprint"]) != fingerprint:
                    raise IdempotencyConflictError(
                        "client_request_id already belongs to a different local edit spec"
                    )
                row = prior
                replayed = True
            else:
                version_id = normalized["source_canvas_version_id"]
                version = connection.execute(
                    "SELECT * FROM canvas_document_versions WHERE id = ?", (version_id,)
                ).fetchone()
                if version is None:
                    raise KeyError(f"unknown canvas document version: {version_id}")
                document = decode_json(version["document_json"], {})
                layer = next(
                    (
                        item for item in document.get("layers") or []
                        if item.get("id") == normalized["source_layer_id"]
                    ),
                    None,
                )
                if layer is None:
                    raise KeyError(
                        f"unknown canvas layer: {normalized['source_layer_id']}"
                    )
                source = layer["source"]
                if normalized["source_size"] != {
                    "width": int(source["original_pixel_width"]),
                    "height": int(source["original_pixel_height"]),
                }:
                    raise ValueError("local edit source size does not match the canvas layer")
                source_asset = connection.execute(
                    """
                    SELECT a.role, a.sha256, b.sha256 AS blob_sha256
                    FROM assets a
                    LEFT JOIN asset_blobs b ON b.id = a.blob_id
                    WHERE a.id = ?
                    """,
                    (str(source["id"]),),
                ).fetchone()
                if source_asset is None:
                    raise KeyError(f"unknown local edit source asset: {source['id']}")
                source_role = str(source_asset["role"] or "")
                if source["kind"] == "asset" and source_role != "workspace_source":
                    raise ValueError("local edit asset source is not a workspace source")
                if source["kind"] == "result" and not source_role.startswith("result_"):
                    raise ValueError("local edit result source is not a result asset")
                stored_file_sha256 = str(
                    source_asset["blob_sha256"]
                    if source_role == "workspace_source"
                    else source_asset["sha256"]
                ).upper()
                if (
                    stored_file_sha256 != str(normalized["source_sha256"]).upper()
                ):
                    raise ValueError(
                        "local edit source file fingerprint does not match the canvas layer asset"
                    )
                roi = connection.execute(
                    "SELECT * FROM canvas_rois WHERE id = ?", (normalized["roi"]["id"],)
                ).fetchone()
                if roi is None:
                    raise KeyError(f"unknown canvas ROI: {normalized['roi']['id']}")
                stored_rect = {
                    "x": int(roi["x"]),
                    "y": int(roi["y"]),
                    "width": int(roi["width"]),
                    "height": int(roi["height"]),
                }
                if (
                    str(roi["canvas_document_version_id"]) != version_id
                    or str(roi["source_layer_id"]) != normalized["source_layer_id"]
                    or str(roi["coordinate_space"])
                    != normalized["roi"]["coordinate_space"]
                    or stored_rect != normalized["roi"]["rect"]
                    or str(roi["purpose"]) != normalized["mode"]
                ):
                    raise ValueError("local edit contract does not match its immutable ROI")

                mask_version_id: str | None = None
                if normalized["mode"] == "inpaint":
                    mask_version_id = str(normalized["mask"]["id"])
                    mask_version = connection.execute(
                        """
                        SELECT v.*, m.roi_id
                        FROM canvas_mask_versions v
                        JOIN canvas_masks m ON m.id = v.mask_id
                        WHERE v.id = ?
                        """,
                        (mask_version_id,),
                    ).fetchone()
                    if mask_version is None:
                        raise KeyError(f"unknown canvas mask version: {mask_version_id}")
                    if str(mask_version["roi_id"]) != str(roi["id"]):
                        raise ValueError("mask version and local edit spec must use the same ROI")
                    if (
                        str(mask_version["pixel_sha256"]).upper()
                        != str(normalized["mask"]["sha256"]).upper()
                    ):
                        raise ValueError("mask pixel fingerprint does not match its version")
                duplicate_operation = connection.execute(
                    "SELECT id FROM local_edit_specs WHERE operation_id = ?",
                    (normalized["operation_id"],),
                ).fetchone()
                if duplicate_operation is not None:
                    raise ValueError("local edit operation_id already exists")
                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO local_edit_specs(
                        id, operation_id, canvas_document_version_id, source_layer_id,
                        roi_id, mask_version_id, mode, client_request_id,
                        request_fingerprint, contract_json, contract_sha256, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        spec_id, normalized["operation_id"], version_id,
                        normalized["source_layer_id"], roi["id"], mask_version_id,
                        normalized["mode"], request_id, fingerprint, stored_json,
                        contract_sha256, now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM local_edit_specs WHERE id = ?", (spec_id,)
                ).fetchone()
                assert row is not None
        return self._local_edit_spec_row(row, replayed=replayed)

    @staticmethod
    def _local_edit_composition_row(
        row: sqlite3.Row | Mapping[str, Any], *, replayed: bool = False
    ) -> dict[str, Any]:
        item = dict(row)
        item["receipt"] = decode_json(item.pop("receipt_json", "{}"), {})
        item.pop("request_fingerprint", None)
        item["replayed"] = replayed
        return item

    @staticmethod
    def _local_edit_result_source(
        result_asset_id: str,
        width: int,
        height: int,
    ) -> dict[str, Any]:
        return {
            "kind": "result",
            "id": result_asset_id,
            "proxy_ref": "proxy:thumbnail:512",
            "original_pixel_width": width,
            "original_pixel_height": height,
        }

    @staticmethod
    def _local_edit_layer_snapshot(layer: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "source": json.loads(canonical_json(layer["source"])),
            "transform": json.loads(canonical_json(layer["transform"])),
            "z_index": int(layer["z_index"]),
            "visible": bool(layer["visible"]),
            "locked": bool(layer["locked"]),
        }

    @staticmethod
    def _outpaint_result_transform(
        transform: Mapping[str, Any],
        outpaint: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = json.loads(canonical_json(transform))
        source_x = float(outpaint["source_x"])
        source_y = float(outpaint["source_y"])
        scale_x = float(result["scale_x"])
        scale_y = float(result["scale_y"])
        radians = math.radians(float(result["rotation_degrees"]))
        offset_x = source_x * scale_x
        offset_y = source_y * scale_y
        result["x"] = float(result["x"]) - (
            offset_x * math.cos(radians) - offset_y * math.sin(radians)
        )
        result["y"] = float(result["y"]) - (
            offset_x * math.sin(radians) + offset_y * math.cos(radians)
        )
        return result

    @staticmethod
    def _validate_local_edit_receipt(
        receipt: Mapping[str, Any],
        *,
        contract: Mapping[str, Any],
        candidate_pixel_sha256: str,
        result_pixel_sha256: str,
    ) -> dict[str, Any]:
        if not isinstance(receipt, Mapping):
            raise ValueError("local edit receipt must be an object")
        normalized = json.loads(canonical_json(receipt))
        required = {
            "contract_schema_version", "operation_id", "mode", "source_sha256",
            "source_pixel_sha256", "candidate_sha256", "output_sha256",
            "undo_source_sha256", "automatic_paid_retry",
        }
        missing = sorted(required - set(normalized))
        if missing:
            raise ValueError(f"local edit receipt is missing fields: {', '.join(missing)}")
        expected = {
            "operation_id": str(contract["operation_id"]),
            "mode": str(contract["mode"]),
            "source_sha256": str(contract["source_sha256"]),
            "source_pixel_sha256": str(contract["source_pixel_sha256"]),
            "candidate_sha256": candidate_pixel_sha256,
            "output_sha256": result_pixel_sha256,
            "undo_source_sha256": str(contract["source_pixel_sha256"]),
        }
        for field, value in expected.items():
            if str(normalized.get(field) or "").upper() != str(value).upper():
                raise ValueError(f"local edit receipt {field} does not match its inputs")
        if normalized["automatic_paid_retry"] is not False:
            raise ValueError("local edit receipt cannot authorize an automatic paid retry")
        if contract["mode"] == "inpaint":
            if int(normalized.get("outside_mask_changed_pixels", -1)) != 0:
                raise ValueError("local edit receipt reports changed protected pixels")
            if str(normalized.get("mask_sha256") or "").upper() != str(
                contract["mask"]["sha256"]
            ).upper():
                raise ValueError("local edit receipt mask does not match its specification")
        elif int(normalized.get("protected_changed_pixels", -1)) != 0:
            raise ValueError("outpaint receipt reports changed protected pixels")
        return normalized

    def _before_local_edit_composition_commit(
        self, connection: sqlite3.Connection
    ) -> None:
        """Internal fault-injection point used by transaction regression tests."""

    def get_local_edit_composition(self, composition_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM local_edit_compositions WHERE id = ?",
                (str(composition_id),),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown local edit composition: {composition_id}")
        return self._local_edit_composition_row(row)

    def get_local_edit_composition_by_request(
        self, client_request_id: str
    ) -> dict[str, Any] | None:
        request_id = str(client_request_id or "").strip()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM local_edit_compositions WHERE client_request_id = ?",
                (request_id,),
            ).fetchone()
        return self._local_edit_composition_row(row, replayed=True) if row else None

    def commit_local_edit_composition(
        self,
        mode: str,
        *,
        local_edit_spec_id: str,
        candidate_asset_id: str,
        expected_canvas_revision: int,
        client_request_id: str,
        result: Mapping[str, Any],
        receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Atomically publish local-edit lineage, its result asset and canvas version."""
        if mode not in WORKFLOW_DRAFT_IDS:
            raise ValueError(f"unsupported workflow mode: {mode}")
        spec_id = str(local_edit_spec_id or "").strip()
        candidate_id = str(candidate_asset_id or "").strip()
        if not spec_id or not candidate_id:
            raise ValueError("local_edit_spec_id and candidate_asset_id are required")
        expected_revision = _canvas_integer(
            expected_canvas_revision, "expected_canvas_revision", minimum=0
        )
        request_id = str(client_request_id or "").strip()
        composition_id = idempotent_id("composition", request_id)
        request_payload = {
            "mode": mode,
            "local_edit_spec_id": spec_id,
            "candidate_asset_id": candidate_id,
            "expected_canvas_revision": expected_revision,
        }
        fingerprint = hashlib.sha256(
            canonical_json(request_payload).encode("utf-8")
        ).hexdigest()
        internal_request_id = "local-compose:" + hashlib.sha256(
            request_id.encode("utf-8")
        ).hexdigest()
        version_id = idempotent_id("canvasver", internal_request_id)
        result_id = idempotent_id("ast", "result:" + internal_request_id)
        result_value = dict(result or {})
        result_path = str(result_value.get("path") or "").strip()
        if not result_path:
            raise ValueError("local edit result path is required")
        width = _canvas_integer(result_value.get("width"), "result.width", minimum=1)
        height = _canvas_integer(result_value.get("height"), "result.height", minimum=1)
        file_sha256 = str(result_value.get("sha256") or "").lower()
        pixel_sha256 = str(result_value.get("pixel_sha256") or "").upper()
        candidate_pixel_sha256 = str(
            result_value.get("candidate_pixel_sha256") or ""
        ).upper()
        if re.fullmatch(r"[a-f0-9]{64}", file_sha256) is None:
            raise ValueError("local edit result file SHA-256 is invalid")
        for label, digest in (
            ("result pixel", pixel_sha256),
            ("candidate pixel", candidate_pixel_sha256),
        ):
            if re.fullmatch(r"[A-F0-9]{64}", digest) is None:
                raise ValueError(f"local edit {label} SHA-256 is invalid")
        replayed = False

        with self._immediate_connection() as connection:
            prior = connection.execute(
                "SELECT * FROM local_edit_compositions WHERE client_request_id = ?",
                (request_id,),
            ).fetchone()
            if prior is not None:
                if str(prior["request_fingerprint"]) != fingerprint:
                    raise IdempotencyConflictError(
                        "client_request_id already belongs to a different local edit composition"
                    )
                composition_row = prior
                replayed = True
            else:
                spec_row = connection.execute(
                    "SELECT * FROM local_edit_specs WHERE id = ?", (spec_id,)
                ).fetchone()
                if spec_row is None:
                    raise KeyError(f"unknown local edit spec: {spec_id}")
                contract = decode_json(spec_row["contract_json"], {})
                if not contract:
                    raise LedgerSchemaError(f"local edit spec {spec_id} has no contract")
                candidate = connection.execute(
                    "SELECT * FROM assets WHERE id = ?", (candidate_id,)
                ).fetchone()
                if candidate is None:
                    raise KeyError(f"unknown local edit candidate asset: {candidate_id}")
                if not str(candidate["role"] or "").startswith("result_"):
                    raise ValueError("local edit candidate is not a result asset")
                expected_size = contract["source_size"]
                if contract["mode"] == "outpaint":
                    expected_size = {
                        "width": contract["outpaint"]["output_width"],
                        "height": contract["outpaint"]["output_height"],
                    }
                if (
                    int(candidate["width"] or 0) != int(expected_size["width"])
                    or int(candidate["height"] or 0) != int(expected_size["height"])
                ):
                    raise ValueError("local edit candidate dimensions do not match the contract")
                if (width, height) != (
                    int(expected_size["width"]), int(expected_size["height"])
                ):
                    raise ValueError("local edit result dimensions do not match the contract")
                normalized_receipt = self._validate_local_edit_receipt(
                    receipt,
                    contract=contract,
                    candidate_pixel_sha256=candidate_pixel_sha256,
                    result_pixel_sha256=pixel_sha256,
                )

                source_version_id = str(spec_row["canvas_document_version_id"])
                source_version = connection.execute(
                    "SELECT * FROM canvas_document_versions WHERE id = ?",
                    (source_version_id,),
                ).fetchone()
                if source_version is None:
                    raise LedgerSchemaError(
                        f"local edit spec {spec_id} has no canvas version"
                    )
                document_row = connection.execute(
                    """
                    SELECT c.* FROM canvas_documents c
                    JOIN workflow_drafts d ON d.id = c.draft_id
                    WHERE c.id = ? AND d.mode = ?
                    """,
                    (source_version["document_id"], mode),
                ).fetchone()
                if document_row is None:
                    raise ValueError("local edit spec belongs to a different workflow")
                current_revision = int(document_row["current_revision"])
                if (
                    current_revision != expected_revision
                    or str(document_row["current_version_id"]) != source_version_id
                ):
                    raise CanvasRevisionConflictError(
                        f"canvas {document_row['id']} is revision {current_revision}, "
                        f"not the frozen local edit revision {expected_revision}",
                        {
                            "id": str(document_row["id"]),
                            "revision": current_revision,
                            "version_id": document_row["current_version_id"],
                        },
                    )
                source_document = decode_json(source_version["document_json"], {})
                source_layer = next(
                    (
                        layer for layer in source_document.get("layers") or []
                        if str(layer.get("id") or "") == str(spec_row["source_layer_id"])
                    ),
                    None,
                )
                if source_layer is None:
                    raise LedgerSchemaError(
                        f"local edit spec {spec_id} has no source layer"
                    )
                source_asset_id = str(source_layer["source"]["id"])
                source_asset = connection.execute(
                    "SELECT id FROM assets WHERE id = ?", (source_asset_id,)
                ).fetchone()
                if source_asset is None:
                    raise KeyError(f"unknown local edit source asset: {source_asset_id}")

                now = utc_now()
                next_document = json.loads(canonical_json(source_document))
                layer = next(
                    item for item in next_document["layers"]
                    if str(item["id"]) == str(spec_row["source_layer_id"])
                )
                before = self._local_edit_layer_snapshot(layer)
                layer["source"] = self._local_edit_result_source(
                    result_id, width, height
                )
                if contract["mode"] == "outpaint":
                    layer["transform"] = self._outpaint_result_transform(
                        layer["transform"], contract["outpaint"]
                    )
                after = self._local_edit_layer_snapshot(layer)
                retained = list(next_document["operations"])[
                    : int(next_document["undo_cursor"]) + 1
                ]
                retained.append({
                    "id": str(contract["operation_id"]),
                    "command_id": "command:local-edit-compose",
                    "input_layer_ids": [str(layer["id"])],
                    "output_layer_id": str(layer["id"]),
                    "roi_id": str(spec_row["roi_id"]),
                    "mask_id": (
                        str(spec_row["mask_version_id"])
                        if spec_row["mask_version_id"] is not None else None
                    ),
                    "product_profile_id": None,
                    "mutation": {
                        "target_layer_id": str(layer["id"]),
                        "before": before,
                        "after": after,
                    },
                    "cost": {
                        "mode": str(contract["cost"]["mode"]),
                        "confirmed_call_count": int(
                            contract["cost"]["confirmed_call_count"]
                        ),
                        "user_confirmation_required": bool(
                            contract["cost"]["user_confirmation_required"]
                        ),
                        "automatic_paid_retry": False,
                    },
                    "status": "succeeded",
                    "created_at": now,
                })
                next_document["operations"] = retained
                next_document["undo_cursor"] = len(retained) - 1
                next_document["source_asset_ids"] = list(dict.fromkeys(
                    str(item["source"]["id"])
                    for item in next_document["layers"]
                    if item["source"]["kind"] == "asset"
                ))
                next_document["revision"] = expected_revision
                next_document["updated_at"] = now
                normalized_document = normalize_canvas_document(next_document)
                stored_document = json.loads(canonical_json(normalized_document))
                next_revision = expected_revision + 1
                stored_document["revision"] = next_revision
                stored_document["created_at"] = str(document_row["created_at"])
                stored_document["updated_at"] = now
                stored_json = canonical_json(stored_document)
                document_sha256 = hashlib.sha256(stored_json.encode("utf-8")).hexdigest()

                receipt_value = {
                    **normalized_receipt,
                    "workflow_mode": mode,
                    "source_canvas_revision": expected_revision,
                    "local_edit_spec_id": spec_id,
                    "candidate_asset_id": candidate_id,
                    "result_asset_id": result_id,
                    "canvas_document_version_id": version_id,
                }
                receipt_json = canonical_json(receipt_value)
                receipt_sha256 = hashlib.sha256(receipt_json.encode("utf-8")).hexdigest()
                result_metadata = {
                    **(
                        result_value.get("metadata")
                        if isinstance(result_value.get("metadata"), dict) else {}
                    ),
                    "local_edit_composition_id": composition_id,
                    "local_edit_spec_id": spec_id,
                    "candidate_asset_id": candidate_id,
                    "pixel_sha256": pixel_sha256,
                    "receipt_sha256": receipt_sha256,
                }
                connection.execute(
                    """
                    INSERT INTO assets(
                        id, session_id, parent_asset_id, role, kind, path, name,
                        mime, width, height, sha256, metadata_json, created_at
                    ) VALUES(?, ?, ?, 'result_local_edit', 'image', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result_id, candidate["session_id"], source_asset_id,
                        result_path,
                        str(result_value.get("name") or Path(result_path).name),
                        str(result_value.get("mime") or "image/png"),
                        width, height, file_sha256, encode_json(result_metadata), now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO canvas_document_versions(
                        id, document_id, revision, parent_version_id,
                        client_request_id, request_fingerprint, document_json,
                        document_sha256, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id, document_row["id"], next_revision, source_version_id,
                        internal_request_id, fingerprint, stored_json, document_sha256, now,
                    ),
                )
                for document_layer in stored_document["layers"]:
                    source = document_layer["source"]
                    connection.execute(
                        """
                        INSERT INTO canvas_version_sources(
                            version_id, layer_id, source_kind, source_asset_id,
                            proxy_ref, original_pixel_width, original_pixel_height
                        ) VALUES(?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            version_id, document_layer["id"], source["kind"], source["id"],
                            source["proxy_ref"], source["original_pixel_width"],
                            source["original_pixel_height"],
                        ),
                    )
                connection.execute(
                    """
                    UPDATE canvas_documents
                    SET current_version_id = ?, current_revision = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (version_id, next_revision, now, document_row["id"]),
                )
                connection.execute(
                    """
                    INSERT INTO local_edit_compositions(
                        id, local_edit_spec_id, candidate_asset_id, result_asset_id,
                        canvas_document_version_id, client_request_id,
                        request_fingerprint, receipt_json, receipt_sha256, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        composition_id, spec_id, candidate_id, result_id, version_id,
                        request_id, fingerprint, receipt_json, receipt_sha256, now,
                    ),
                )
                self._before_local_edit_composition_commit(connection)
                composition_row = connection.execute(
                    "SELECT * FROM local_edit_compositions WHERE id = ?",
                    (composition_id,),
                ).fetchone()
                assert composition_row is not None

        item = self._local_edit_composition_row(
            composition_row, replayed=replayed
        )
        item["result_asset"] = self.get_asset(item["result_asset_id"])
        item["canvas"] = self.get_canvas_document(mode)
        return item

    @staticmethod
    def _product_profile_version_row(
        row: sqlite3.Row | Mapping[str, Any],
    ) -> dict[str, Any]:
        item = dict(row)
        item["profile"] = decode_json(item.pop("profile_json", "{}"), {})
        item.pop("request_fingerprint", None)
        return item

    @classmethod
    def _product_profile_result(
        cls,
        profile_row: sqlite3.Row | Mapping[str, Any],
        version_row: sqlite3.Row | Mapping[str, Any],
        *,
        replayed: bool,
    ) -> dict[str, Any]:
        version = cls._product_profile_version_row(version_row)
        profile = version.pop("profile")
        return {
            "id": str(profile_row["id"]),
            "sku": str(profile_row["sku"]),
            "current_revision": int(profile_row["current_revision"]),
            "current_version_id": profile_row["current_version_id"],
            "profile": profile,
            "version": version,
            "replayed": replayed,
        }

    def get_product_profile(self, profile_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM product_profiles WHERE id = ?", (str(profile_id),)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown product profile: {profile_id}")
            version = connection.execute(
                "SELECT * FROM product_profile_versions WHERE id = ?",
                (row["current_version_id"],),
            ).fetchone()
            if version is None:
                raise LedgerSchemaError(f"product profile {profile_id} has no current version")
        return self._product_profile_result(row, version, replayed=False)

    def get_product_profile_version(self, version_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM product_profile_versions WHERE id = ?", (str(version_id),)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown product profile version: {version_id}")
        return self._product_profile_version_row(row)

    def list_product_profile_versions(
        self, profile_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            profile = connection.execute(
                "SELECT id FROM product_profiles WHERE id = ?", (str(profile_id),)
            ).fetchone()
            if profile is None:
                raise KeyError(f"unknown product profile: {profile_id}")
            rows = connection.execute(
                """
                SELECT * FROM product_profile_versions
                WHERE profile_id = ?
                ORDER BY revision DESC
                LIMIT ?
                """,
                (str(profile_id), limit),
            ).fetchall()
        return [self._product_profile_version_row(row) for row in rows]

    def list_product_profiles(self, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT p.*, v.id AS version_record_id, v.profile_id,
                       v.revision AS version_revision, v.parent_version_id,
                       v.client_request_id, v.request_fingerprint, v.profile_json,
                       v.profile_sha256, v.created_at AS version_created_at
                FROM product_profiles p
                JOIN product_profile_versions v ON v.id = p.current_version_id
                ORDER BY p.updated_at DESC, p.id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            profile_row = {
                "id": item["id"],
                "sku": item["sku"],
                "current_revision": item["current_revision"],
                "current_version_id": item["current_version_id"],
            }
            version = {
                "id": item["version_record_id"],
                "profile_id": item["profile_id"],
                "revision": item["version_revision"],
                "parent_version_id": item["parent_version_id"],
                "client_request_id": item["client_request_id"],
                "request_fingerprint": item["request_fingerprint"],
                "profile_json": item["profile_json"],
                "profile_sha256": item["profile_sha256"],
                "created_at": item["version_created_at"],
            }
            results.append(
                self._product_profile_result(profile_row, version, replayed=False)
            )
        return results

    @staticmethod
    def _validate_product_profile_references(
        connection: sqlite3.Connection,
        profile: Mapping[str, Any],
    ) -> None:
        reference_ids = [str(item) for item in profile["approved_reference_ids"]]
        placeholders = ",".join("?" for _ in reference_ids)
        rows = connection.execute(
            f"SELECT id, role FROM assets WHERE id IN ({placeholders})",
            reference_ids,
        ).fetchall()
        assets = {str(row["id"]): str(row["role"] or "") for row in rows}
        missing = [asset_id for asset_id in reference_ids if asset_id not in assets]
        if missing:
            raise KeyError(f"unknown approved reference assets: {', '.join(missing)}")
        invalid = [asset_id for asset_id in reference_ids if assets[asset_id] != "workspace_source"]
        if invalid:
            raise ValueError(
                "approved references must be workspace source assets: " + ", ".join(invalid)
            )

    def save_product_profile(
        self,
        *,
        expected_revision: int,
        client_request_id: str,
        profile: Mapping[str, Any],
    ) -> dict[str, Any]:
        expected_revision = _canvas_integer(
            expected_revision, "expected_revision", minimum=0
        )
        request_id = str(client_request_id or "").strip()
        version_id = idempotent_id("profilever", request_id)
        normalized = normalize_product_profile(profile)
        if int(normalized["revision"]) != expected_revision:
            raise ValueError("ProductProfile.revision must equal expected_revision")
        profile_id = str(normalized["id"])
        sku = str(normalized["sku"])
        fingerprint = hashlib.sha256(canonical_json({
            "expected_revision": expected_revision,
            "profile": normalized,
        }).encode("utf-8")).hexdigest()
        replayed = False

        with self._immediate_connection() as connection:
            prior_version = connection.execute(
                "SELECT * FROM product_profile_versions WHERE client_request_id = ?",
                (request_id,),
            ).fetchone()
            if prior_version is not None:
                if str(prior_version["request_fingerprint"]) != fingerprint:
                    raise IdempotencyConflictError(
                        "client_request_id already belongs to a different product profile save"
                    )
                profile_row = connection.execute(
                    "SELECT * FROM product_profiles WHERE id = ?",
                    (prior_version["profile_id"],),
                ).fetchone()
                if profile_row is None or str(profile_row["id"]) != profile_id:
                    raise IdempotencyConflictError(
                        "client_request_id belongs to a different product profile"
                    )
                replayed = True
                version_row = prior_version
            else:
                profile_row = connection.execute(
                    "SELECT * FROM product_profiles WHERE id = ?", (profile_id,)
                ).fetchone()
                if profile_row is None:
                    if expected_revision != 0:
                        raise ProductProfileRevisionConflictError(
                            f"product profile {profile_id} is revision 0, not {expected_revision}",
                            {"id": None, "revision": 0, "version_id": None},
                        )
                    duplicate_sku = connection.execute(
                        "SELECT id, current_revision, current_version_id "
                        "FROM product_profiles WHERE sku = ? COLLATE NOCASE",
                        (sku,),
                    ).fetchone()
                    if duplicate_sku is not None:
                        raise ValueError("ProductProfile.sku already belongs to another profile")
                    parent_version_id = None
                    created_at = utc_now()
                else:
                    current_revision = int(profile_row["current_revision"])
                    if current_revision != expected_revision:
                        raise ProductProfileRevisionConflictError(
                            f"product profile {profile_id} is revision {current_revision}, "
                            f"not {expected_revision}",
                            {
                                "id": profile_id,
                                "revision": current_revision,
                                "version_id": profile_row["current_version_id"],
                            },
                        )
                    if str(profile_row["sku"]).casefold() != sku.casefold():
                        raise ValueError("ProductProfile.sku cannot change between versions")
                    parent_version_id = profile_row["current_version_id"]
                    created_at = str(profile_row["created_at"])

                self._validate_product_profile_references(connection, normalized)
                next_revision = expected_revision + 1
                now = utc_now()
                stored_profile = json.loads(canonical_json(normalized))
                stored_profile["revision"] = next_revision
                stored_profile["created_at"] = created_at
                stored_profile["updated_at"] = now
                stored_json = canonical_json(stored_profile)
                profile_sha256 = hashlib.sha256(stored_json.encode("utf-8")).hexdigest()

                if profile_row is None:
                    connection.execute(
                        """
                        INSERT INTO product_profiles(
                            id, sku, current_version_id, current_revision,
                            created_at, updated_at
                        ) VALUES(?, ?, NULL, 0, ?, ?)
                        """,
                        (profile_id, sku, created_at, now),
                    )
                connection.execute(
                    """
                    INSERT INTO product_profile_versions(
                        id, profile_id, revision, parent_version_id,
                        client_request_id, request_fingerprint, profile_json,
                        profile_sha256, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id, profile_id, next_revision, parent_version_id,
                        request_id, fingerprint, stored_json, profile_sha256, now,
                    ),
                )
                for asset_id in stored_profile["approved_reference_ids"]:
                    connection.execute(
                        """
                        INSERT INTO product_profile_version_assets(version_id, asset_id, role)
                        VALUES(?, ?, 'approved_reference')
                        """,
                        (version_id, asset_id),
                    )
                connection.execute(
                    """
                    UPDATE product_profiles
                    SET current_version_id = ?, current_revision = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (version_id, next_revision, now, profile_id),
                )
                profile_row = connection.execute(
                    "SELECT * FROM product_profiles WHERE id = ?", (profile_id,)
                ).fetchone()
                version_row = connection.execute(
                    "SELECT * FROM product_profile_versions WHERE id = ?", (version_id,)
                ).fetchone()
                assert profile_row is not None and version_row is not None

        return self._product_profile_result(profile_row, version_row, replayed=replayed)

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

    @staticmethod
    def _validate_local_edit_job_binding(
        connection: sqlite3.Connection,
        *,
        local_edit_spec_id: str,
        canvas_document_version_id: str | None,
        canvas_operation_id: str | None,
        source_asset_ids: Iterable[str],
    ) -> None:
        spec = connection.execute(
            "SELECT * FROM local_edit_specs WHERE id = ?",
            (local_edit_spec_id,),
        ).fetchone()
        if spec is None:
            raise KeyError(f"unknown local edit spec: {local_edit_spec_id}")
        if canvas_document_version_id is None:
            raise ValueError("local edit spec requires an exact canvas version")
        if str(spec["canvas_document_version_id"]) != canvas_document_version_id:
            raise ValueError("local edit spec and job must use the same canvas version")
        if str(spec["operation_id"]) != str(canvas_operation_id or ""):
            raise ValueError("local edit spec and job must use the same canvas operation")

        roi = connection.execute(
            "SELECT * FROM canvas_rois WHERE id = ?",
            (str(spec["roi_id"]),),
        ).fetchone()
        if roi is None:
            raise LedgerSchemaError(f"local edit spec {local_edit_spec_id} has no ROI")
        if (
            str(roi["canvas_document_version_id"]) != canvas_document_version_id
            or str(roi["source_layer_id"]) != str(spec["source_layer_id"])
            or str(roi["purpose"]) != str(spec["mode"])
        ):
            raise LedgerSchemaError(
                f"local edit spec {local_edit_spec_id} has inconsistent ROI lineage"
            )

        contract = decode_json(spec["contract_json"], {})
        expected_mask_version_id = (
            str(contract.get("mask", {}).get("id") or "") or None
            if isinstance(contract.get("mask"), Mapping)
            else None
        )
        stored_mask_version_id = str(spec["mask_version_id"] or "") or None
        if expected_mask_version_id != stored_mask_version_id:
            raise LedgerSchemaError(
                f"local edit spec {local_edit_spec_id} has inconsistent mask lineage"
            )
        if stored_mask_version_id is not None:
            mask = connection.execute(
                """
                SELECT v.id, m.roi_id
                FROM canvas_mask_versions v
                JOIN canvas_masks m ON m.id = v.mask_id
                WHERE v.id = ?
                """,
                (stored_mask_version_id,),
            ).fetchone()
            if mask is None or str(mask["roi_id"]) != str(roi["id"]):
                raise LedgerSchemaError(
                    f"local edit spec {local_edit_spec_id} has inconsistent mask ROI"
                )

        version = connection.execute(
            "SELECT document_json FROM canvas_document_versions WHERE id = ?",
            (canvas_document_version_id,),
        ).fetchone()
        if version is None:
            raise LedgerSchemaError(
                f"local edit spec {local_edit_spec_id} has no canvas version"
            )
        document = decode_json(version["document_json"], {})
        layer = next(
            (
                item for item in document.get("layers") or []
                if item.get("id") == str(spec["source_layer_id"])
            ),
            None,
        )
        if layer is None:
            raise LedgerSchemaError(
                f"local edit spec {local_edit_spec_id} has no source layer"
            )
        source_asset_id = str(layer.get("source", {}).get("id") or "")
        if source_asset_id not in {str(asset_id) for asset_id in source_asset_ids}:
            raise ValueError("local edit source asset must be included in the job sources")

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
        command_id: str = "",
        canvas_document_id: str | None = None,
        expected_canvas_revision: int | None = None,
        canvas_operation_id: str | None = None,
        product_profile_id: str | None = None,
        expected_product_profile_revision: int | None = None,
        local_edit_spec_id: str | None = None,
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
        command = get_command(command_id) if str(command_id or "").strip() else command_for_mode(mode)
        if command["execution_kind"] != "durable-job" or command["mode"] != mode:
            raise ValueError(f"command {command['id']} does not execute workflow mode {mode}")
        command_id = str(command["id"])
        canvas_document_id = str(canvas_document_id or "").strip() or None
        canvas_operation_id = str(canvas_operation_id or "").strip() or None
        if canvas_document_id is None:
            if expected_canvas_revision is not None or canvas_operation_id is not None:
                raise ValueError("canvas revision and operation require canvas_document_id")
        else:
            _canvas_id(canvas_document_id, "canvas_document_id")
            if expected_canvas_revision is None:
                raise ValueError("expected_canvas_revision is required for a canvas command")
            expected_canvas_revision = _canvas_integer(
                expected_canvas_revision, "expected_canvas_revision", minimum=0
            )
            if canvas_operation_id is not None:
                _canvas_id(canvas_operation_id, "canvas_operation_id")
        product_profile_id = str(product_profile_id or "").strip() or None
        if product_profile_id is None:
            if expected_product_profile_revision is not None:
                raise ValueError(
                    "expected_product_profile_revision requires product_profile_id"
                )
        else:
            _canvas_id(product_profile_id, "product_profile_id")
            if expected_product_profile_revision is None:
                raise ValueError(
                    "expected_product_profile_revision is required for a product profile"
                )
            expected_product_profile_revision = _canvas_integer(
                expected_product_profile_revision,
                "expected_product_profile_revision",
                minimum=1,
            )
        local_edit_spec_id = str(local_edit_spec_id or "").strip() or None
        if local_edit_spec_id is not None:
            if len(local_edit_spec_id) > 200:
                raise ValueError("local_edit_spec_id is too long")
            if canvas_document_id is None:
                raise ValueError("local edit spec requires canvas_document_id")
            if canvas_operation_id is None:
                raise ValueError("local edit spec requires canvas_operation_id")
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
        canvas_document_version_id: str | None = None
        product_profile_version_id: str | None = None

        with self._immediate_connection() as connection:
            if idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if existing is not None:
                    job_id = str(existing["id"])
                    snapshot = connection.execute(
                        "SELECT * FROM job_snapshots WHERE job_id = ?", (job_id,)
                    ).fetchone()
                    if snapshot is None:
                        raise LedgerSchemaError(f"job {job_id} has no immutable snapshot")
                    existing_items = connection.execute(
                        "SELECT source_asset_id, engine_key, max_attempts FROM job_items "
                        "WHERE job_id = ? ORDER BY position",
                        (job_id,),
                    ).fetchall()
                    if canvas_document_id is not None:
                        requested_version = connection.execute(
                            """
                            SELECT v.id FROM canvas_document_versions v
                            JOIN canvas_documents c ON c.id = v.document_id
                            JOIN workflow_drafts d ON d.id = c.draft_id
                            WHERE c.id = ? AND v.revision = ? AND d.mode = ?
                            """,
                            (canvas_document_id, expected_canvas_revision, mode),
                        ).fetchone()
                        if requested_version is None:
                            current = connection.execute(
                                "SELECT current_revision, current_version_id "
                                "FROM canvas_documents WHERE id = ?",
                                (canvas_document_id,),
                            ).fetchone()
                            raise CanvasRevisionConflictError(
                                "the requested canvas revision is unavailable",
                                {
                                    "id": canvas_document_id,
                                    "revision": int(current["current_revision"]) if current else 0,
                                    "version_id": current["current_version_id"] if current else None,
                                },
                            )
                        canvas_document_version_id = str(requested_version["id"])
                    if product_profile_id is not None:
                        requested_profile_version = connection.execute(
                            """
                            SELECT v.id FROM product_profile_versions v
                            JOIN product_profiles p ON p.id = v.profile_id
                            WHERE p.id = ? AND v.revision = ?
                            """,
                            (product_profile_id, expected_product_profile_revision),
                        ).fetchone()
                        if requested_profile_version is None:
                            current = connection.execute(
                                "SELECT current_revision, current_version_id "
                                "FROM product_profiles WHERE id = ?",
                                (product_profile_id,),
                            ).fetchone()
                            raise ProductProfileRevisionConflictError(
                                "the requested product profile revision is unavailable",
                                {
                                    "id": product_profile_id if current else None,
                                    "revision": int(current["current_revision"]) if current else 0,
                                    "version_id": current["current_version_id"] if current else None,
                                },
                            )
                        product_profile_version_id = str(requested_profile_version["id"])
                    if local_edit_spec_id is not None:
                        self._validate_local_edit_job_binding(
                            connection,
                            local_edit_spec_id=local_edit_spec_id,
                            canvas_document_version_id=canvas_document_version_id,
                            canvas_operation_id=canvas_operation_id,
                            source_asset_ids=source_asset_ids,
                        )
                    same_request = (
                        str(existing["mode"]) == mode
                        and decode_json(existing["parameters_json"], {}) == parameters
                        and int(existing["requested_concurrency"]) == requested_concurrency
                        and [str(row["source_asset_id"]) for row in existing_items]
                        == source_asset_ids
                        and all(str(row["engine_key"]) == engine_key for row in existing_items)
                        and all(int(row["max_attempts"]) == max_attempts for row in existing_items)
                        and str(snapshot["command_id"] or "") == command_id
                        and (snapshot["canvas_document_version_id"] or None)
                        == canvas_document_version_id
                        and (snapshot["canvas_operation_id"] or None) == canvas_operation_id
                        and (snapshot["product_profile_version_id"] or None)
                        == product_profile_version_id
                        and (snapshot["local_edit_spec_id"] or None)
                        == local_edit_spec_id
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
                if canvas_document_id is not None:
                    canvas = connection.execute(
                        """
                        SELECT c.* FROM canvas_documents c
                        JOIN workflow_drafts d ON d.id = c.draft_id
                        WHERE c.id = ? AND d.mode = ?
                        """,
                        (canvas_document_id, mode),
                    ).fetchone()
                    if canvas is None:
                        raise KeyError(f"unknown canvas for {mode}: {canvas_document_id}")
                    current_revision = int(canvas["current_revision"])
                    if current_revision != expected_canvas_revision:
                        raise CanvasRevisionConflictError(
                            f"canvas {canvas_document_id} is revision {current_revision}, "
                            f"not {expected_canvas_revision}",
                            {
                                "id": canvas_document_id,
                                "revision": current_revision,
                                "version_id": canvas["current_version_id"],
                            },
                        )
                    canvas_document_version_id = str(canvas["current_version_id"])
                    canvas_sources = {
                        str(row["source_asset_id"])
                        for row in connection.execute(
                            "SELECT source_asset_id FROM canvas_version_sources "
                            "WHERE version_id = ?",
                            (canvas_document_version_id,),
                        )
                    }
                    outside = [
                        asset_id for asset_id in source_asset_ids if asset_id not in canvas_sources
                    ]
                    if outside:
                        raise ValueError(
                            "job source assets are not present in the selected canvas version: "
                            + ", ".join(outside)
                        )
                if product_profile_id is not None:
                    profile = connection.execute(
                        "SELECT * FROM product_profiles WHERE id = ?",
                        (product_profile_id,),
                    ).fetchone()
                    if profile is None:
                        raise KeyError(f"unknown product profile: {product_profile_id}")
                    current_revision = int(profile["current_revision"])
                    if current_revision != expected_product_profile_revision:
                        raise ProductProfileRevisionConflictError(
                            f"product profile {product_profile_id} is revision {current_revision}, "
                            f"not {expected_product_profile_revision}",
                            {
                                "id": product_profile_id,
                                "revision": current_revision,
                                "version_id": profile["current_version_id"],
                            },
                        )
                    product_profile_version_id = str(profile["current_version_id"])
                if local_edit_spec_id is not None:
                    self._validate_local_edit_job_binding(
                        connection,
                        local_edit_spec_id=local_edit_spec_id,
                        canvas_document_version_id=canvas_document_version_id,
                        canvas_operation_id=canvas_operation_id,
                        source_asset_ids=source_asset_ids,
                    )
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
                        command_id, canvas_document_version_id, canvas_operation_id,
                        product_profile_version_id, local_edit_spec_id,
                        created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        command_id,
                        canvas_document_version_id,
                        canvas_operation_id,
                        product_profile_version_id,
                        local_edit_spec_id,
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
                            "command_id": command_id,
                            "canvas_document_version_id": canvas_document_version_id,
                            "canvas_operation_id": canvas_operation_id,
                            "product_profile_version_id": product_profile_version_id,
                            "local_edit_spec_id": local_edit_spec_id,
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
            if job_id:
                snapshot = connection.execute(
                    "SELECT command_id, canvas_document_version_id, canvas_operation_id, "
                    "product_profile_version_id "
                    ", local_edit_spec_id "
                    "FROM job_snapshots WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                if snapshot is None:
                    raise KeyError(f"unknown job: {job_id}")
                candidate.update({
                    "command_id": str(snapshot["command_id"] or ""),
                    "canvas_document_version_id": snapshot["canvas_document_version_id"],
                    "canvas_operation_id": snapshot["canvas_operation_id"],
                    "product_profile_version_id": snapshot["product_profile_version_id"],
                    "local_edit_spec_id": snapshot["local_edit_spec_id"],
                })
            else:
                candidate.update({
                    "command_id": "",
                    "canvas_document_version_id": None,
                    "canvas_operation_id": None,
                    "product_profile_version_id": None,
                    "local_edit_spec_id": None,
                })
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
                    error_code, error_message, command_id,
                    canvas_document_version_id, canvas_operation_id,
                    product_profile_version_id, local_edit_spec_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id, job_id, job_item_id, generation_id, stage, status,
                    encode_json(candidate["user_input"]), candidate["compiled_prompt"],
                    encode_json(candidate["applied_knowledge"]),
                    encode_json(candidate["ignored_fields"]), candidate["model"],
                    encode_json(candidate["parameters"]), encode_json(candidate["output"]),
                    candidate["error_code"], candidate["error_message"],
                    candidate["command_id"], candidate["canvas_document_version_id"],
                    candidate["canvas_operation_id"],
                    candidate["product_profile_version_id"],
                    candidate["local_edit_spec_id"], now,
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
                    "execution_traces", "canvas_documents",
                    "canvas_document_versions", "canvas_version_sources",
                    "product_profiles", "product_profile_versions",
                    "product_profile_version_assets",
                    "canvas_rois", "canvas_masks", "canvas_mask_versions",
                    "local_edit_specs",
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
