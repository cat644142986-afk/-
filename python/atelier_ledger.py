# -*- coding: utf-8 -*-
"""Local-first creation ledger for Product Atelier.

The ledger stores design decisions and asset provenance, not image pixels or raw
interaction telemetry.  It is intentionally dependency-free so the packaged
sidecar can keep using the Python standard library.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def encode_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))


def decode_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


class AtelierLedger:
    """Thread-safe SQLite facade for sessions, generations and learning signals."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=20, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 20000")
        return connection

    @contextmanager
    def _connection(self):
        """Commit or roll back, then always release the Windows file handle."""
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._schema_lock, self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ledger_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

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
                );

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
                );

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
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    generation_id TEXT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                    FOREIGN KEY(generation_id) REFERENCES generations(id) ON DELETE SET NULL
                );

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
                );

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
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_assets_session ON assets(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_generations_session ON generations(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_generations_task ON generations(task_id);
                CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_feedback_session ON feedback(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_memory_pending ON memory_suggestions(status, created_at);
                """
            )
            connection.execute(
                "INSERT INTO ledger_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

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
    ) -> dict[str, Any]:
        feedback_id = new_id("fb")
        now = utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO feedback(
                    id, session_id, generation_id, asset_id, signal, reason,
                    structured_json, scope, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id, session_id, generation_id, asset_id, signal,
                    reason, encode_json(structured), scope, now,
                ),
            )
        self.add_event(
            session_id,
            "feedback.recorded",
            {"feedback_id": feedback_id, "signal": signal, "scope": scope},
            generation_id=generation_id,
        )
        return {
            "id": feedback_id,
            "session_id": session_id,
            "generation_id": generation_id,
            "asset_id": asset_id,
            "signal": signal,
            "reason": reason,
            "structured": structured or {},
            "scope": scope,
            "created_at": now,
        }

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
        suggestion_id = new_id("mem")
        now = utc_now()
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
                    encode_json(current_value), encode_json(proposed_value),
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
        proposed_json = encode_json(proposed_value)
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
                same_value = encode_json(existing.get("proposed_value")) == proposed_json
                if existing["status"] == "approved" and same_value:
                    return existing
                if existing["status"] in {"rejected", "dismissed"} and same_value:
                    old_count = len(existing.get("evidence") or [])
                    if len(evidence) < old_count + 2:
                        return existing
                if existing["status"] == "pending":
                    connection.execute(
                        """
                        UPDATE memory_suggestions
                        SET current_value_json = ?, proposed_value_json = ?, evidence_json = ?,
                            confidence = ?, created_at = ?, reviewed_at = NULL
                        WHERE id = ?
                        """,
                        (
                            encode_json(current_value), proposed_json, encode_json(evidence),
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
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE memory_suggestions SET status = ?, reviewed_at = ? WHERE id = ?",
                (status, utc_now(), suggestion_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"unknown suggestion: {suggestion_id}")
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
            cursor = connection.execute(
                """
                UPDATE memory_suggestions SET status = 'dismissed', reviewed_at = ?
                WHERE scope_type = ? AND scope_id = ? AND category = ?
                  AND rule_key = ? AND status = 'pending'
                """,
                (utc_now(), scope_type, scope_id, category, rule_key),
            )
            return int(cursor.rowcount)

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
                FROM sessions s ORDER BY updated_at DESC LIMIT ?
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
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("sessions", "assets", "generations", "events", "feedback", "memory_suggestions")
            }
            pending = connection.execute(
                "SELECT COUNT(*) FROM memory_suggestions WHERE status = 'pending'"
            ).fetchone()[0]
        return {
            "schema_version": SCHEMA_VERSION,
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
    def _memory_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["current_value"] = decode_json(item.pop("current_value_json", "null"), None)
        item["proposed_value"] = decode_json(item.pop("proposed_value_json", "null"), None)
        item["evidence"] = decode_json(item.pop("evidence_json", "[]"), [])
        return item
