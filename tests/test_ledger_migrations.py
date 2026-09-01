from __future__ import annotations

import hashlib
import multiprocessing
import sqlite3
import tempfile
import unittest
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

from python.atelier_ledger import (
    AtelierLedger,
    DraftRevisionConflictError,
    InvalidStatusTransitionError,
    LedgerSchemaError,
    PartialSchemaError,
    SCHEMA_VERSION,
    UnsupportedSchemaVersionError,
    validate_status_transition,
)


V1_DATA_TABLES = (
    "sessions",
    "assets",
    "generations",
    "events",
    "feedback",
    "memory_suggestions",
)


def create_v1_fixture(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        fixture_path = Path(__file__).parent / "fixtures" / "ledger-v1.sql"
        connection.executescript(
            fixture_path.read_text(encoding="utf-8")
        )
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO sessions(
                id, mode, status, title, project_name, designer_profile,
                brand_profile, category, brief_json, intent_locks_json,
                started_at, updated_at, completed_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ses_fixture", "multi-file", "completed", "迁移样本", "项目A",
                "designer-a", "brand-a", "食品饮料", '{"goal":"white"}',
                '{"logo":true}', "2026-08-20T00:00:00+00:00",
                "2026-08-20T00:10:00+00:00", "2026-08-20T00:10:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO assets(
                id, session_id, parent_asset_id, role, kind, path, name, mime,
                width, height, sha256, metadata_json, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ast_fixture", "ses_fixture", None, "source", "image", "",
                "source.png", "image/png", 128, 96, "a" * 64,
                '{"legacy":true}', "2026-08-20T00:01:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO generations(
                id, session_id, task_id, parent_generation_id, model, prompt,
                negative_prompt, parameters_json, knowledge_refs_json,
                prompt_version, status, result_asset_ids_json, error,
                latency_ms, estimated_cost, created_at, completed_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "gen_fixture", "ses_fixture", "task-legacy", None, "mock-model",
                "legacy prompt", "legacy negative", '{"batch":1}', '["K-1"]',
                "v1", "completed", '["ast_fixture"]', "", 1234, 0.01,
                "2026-08-20T00:02:00+00:00", "2026-08-20T00:03:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO events(session_id, generation_id, event_type, payload_json, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                "ses_fixture", "gen_fixture", "fixture.created", '{"ok":true}',
                "2026-08-20T00:04:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO feedback(
                id, session_id, generation_id, asset_id, signal, reason,
                structured_json, scope, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "fb_fixture", "ses_fixture", "gen_fixture", "ast_fixture",
                "adopted", "保留这版", '{"score":5}', "session",
                "2026-08-20T00:05:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO memory_suggestions(
                id, scope_type, scope_id, category, rule_key,
                current_value_json, proposed_value_json, evidence_json,
                confidence, status, created_at, reviewed_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "mem_fixture", "brand", "brand-a", "食品饮料", "shadow.weight",
                'null', '"lighter"', '["fb_fixture"]', 0.8, "approved",
                "2026-08-20T00:06:00+00:00", "2026-08-20T00:07:00+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()


def create_v2_workspace_fixture(path: Path) -> None:
    """Create a real v2 database without opening it through the v3 initializer."""
    create_v1_fixture(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        AtelierLedger._migrate_v1_to_v2(connection)
        now = "2026-08-21T01:00:00+00:00"
        connection.execute(
            """
            INSERT INTO sessions(
                id, mode, status, title, project_name, designer_profile,
                brand_profile, category, brief_json, intent_locks_json,
                started_at, updated_at
            ) VALUES('ses_workspace', 'workspace', 'active', '素材工作区', '',
                     'default', '', 'general', '{}', '{}', ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO asset_blobs(
                id, sha256, storage_path, mime, size_bytes, width, height, created_at
            ) VALUES('blob_workspace', ?, 'D:/assets/legacy.png', 'image/png', 64, 8, 8, ?)
            """,
            ("b" * 64, now),
        )
        connection.execute(
            """
            INSERT INTO assets(
                id, session_id, parent_asset_id, role, kind, path, name, mime,
                width, height, sha256, metadata_json, created_at, blob_id
            ) VALUES('ast_workspace', 'ses_workspace', NULL, 'workspace_source',
                     'image', 'D:/assets/legacy.png', 'legacy.png', 'image/png',
                     8, 8, ?, '{}', ?, 'blob_workspace')
            """,
            ("b" * 64, now),
        )
        connection.execute(
            "UPDATE ledger_meta SET value = '2' WHERE key = 'schema_version'"
        )
        connection.commit()
    finally:
        connection.close()


def snapshot_v1_data(path: Path) -> dict[str, list[dict[str, object]]]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        result: dict[str, list[dict[str, object]]] = {}
        for table in V1_DATA_TABLES:
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            legacy_columns = [column for column in columns if column != "blob_id"]
            rows = connection.execute(
                f"SELECT {', '.join(legacy_columns)} FROM {table} ORDER BY rowid"
            ).fetchall()
            result[table] = [dict(row) for row in rows]
        return result
    finally:
        connection.close()


def read_schema_version(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute(
            "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
        ).fetchone()[0])
    finally:
        connection.close()


def initialize_ledger_process(path: str) -> int:
    return AtelierLedger(path).stats()["schema_version"]


class LedgerMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "atelier.sqlite3"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_new_database_is_created_at_latest_schema_and_reopens_cleanly(self) -> None:
        first = AtelierLedger(self.db_path)
        self.assertEqual(first.stats()["schema_version"], SCHEMA_VERSION)
        self.assertIsNone(first.last_migration_backup)

        second = AtelierLedger(self.db_path)
        self.assertEqual(second.stats()["schema_version"], SCHEMA_VERSION)
        self.assertIsNone(second.last_migration_backup)
        self.assertEqual(list(Path(self.temp_dir.name).glob("*.backup-*.sqlite3")), [])

    def test_v1_upgrade_preserves_every_legacy_row_and_creates_queryable_backup(self) -> None:
        create_v1_fixture(self.db_path)
        before = snapshot_v1_data(self.db_path)

        ledger = AtelierLedger(self.db_path)

        self.assertEqual(read_schema_version(self.db_path), SCHEMA_VERSION)
        self.assertEqual(snapshot_v1_data(self.db_path), before)
        self.assertIsNotNone(ledger.last_migration_backup)
        backup_path = ledger.last_migration_backup
        assert backup_path is not None
        self.assertTrue(backup_path.exists())
        self.assertEqual(read_schema_version(backup_path), 1)
        self.assertEqual(snapshot_v1_data(backup_path), before)

        connection = sqlite3.connect(self.db_path)
        try:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertTrue({
                "asset_blobs", "jobs", "job_items", "task_attempts",
                "asset_collections", "asset_collection_members", "workflow_drafts",
                "draft_asset_selections", "job_snapshots", "result_reviews",
                "execution_traces",
            }.issubset(tables))
            asset_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(assets)")
            }
            self.assertIn("blob_id", asset_columns)
            self.assertIsNone(connection.execute(
                "SELECT blob_id FROM assets WHERE id = 'ast_fixture'"
            ).fetchone()[0])
        finally:
            connection.close()

        backup_count = len(list(Path(self.temp_dir.name).glob("*.backup-v1-*.sqlite3")))
        reopened = AtelierLedger(self.db_path)
        self.assertIsNone(reopened.last_migration_backup)
        self.assertEqual(
            len(list(Path(self.temp_dir.name).glob("*.backup-v1-*.sqlite3"))),
            backup_count,
        )

    def test_complete_v2_schema_with_stale_v1_marker_is_repaired_safely(self) -> None:
        AtelierLedger(self.db_path)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE ledger_meta SET value = '1' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()

        repaired = AtelierLedger(self.db_path)

        self.assertEqual(read_schema_version(self.db_path), SCHEMA_VERSION)
        self.assertEqual(
            repaired.last_schema_repair,
            "recovered complete v2 schema with stale v1 metadata; "
            "recovered complete v3 schema with stale v2 metadata; "
            "recovered complete v4 schema with stale v3 metadata",
        )
        self.assertIsNotNone(repaired.last_migration_backup)
        backup_path = repaired.last_migration_backup
        assert backup_path is not None
        self.assertEqual(read_schema_version(backup_path), 1)

        reopened = AtelierLedger(self.db_path)
        self.assertIsNone(reopened.last_schema_repair)
        self.assertIsNone(reopened.last_migration_backup)

    def test_incomplete_v2_schema_with_v1_marker_is_refused_without_mutation(self) -> None:
        AtelierLedger(self.db_path)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("DROP INDEX idx_task_attempts_item")
            connection.execute(
                "UPDATE ledger_meta SET value = '1' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(PartialSchemaError, "missing indexes"):
            AtelierLedger(self.db_path)

        self.assertEqual(read_schema_version(self.db_path), 1)
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND name = 'idx_task_attempts_item'"
            ).fetchone())
        finally:
            connection.close()
        backups = list(Path(self.temp_dir.name).glob("*.backup-v1-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(read_schema_version(backups[0]), 1)

    def test_v2_upgrade_creates_scoped_defaults_and_preserves_workspace_assets(self) -> None:
        create_v2_workspace_fixture(self.db_path)

        ledger = AtelierLedger(self.db_path)

        self.assertEqual(read_schema_version(self.db_path), SCHEMA_VERSION)
        self.assertIsNotNone(ledger.last_migration_backup)
        backup_path = ledger.last_migration_backup
        assert backup_path is not None
        self.assertEqual(read_schema_version(backup_path), 2)
        self.assertEqual(
            [item["id"] for item in ledger.list_collection_assets("product")],
            ["ast_workspace"],
        )
        self.assertEqual(ledger.list_collection_assets("group"), [])
        self.assertEqual(ledger.list_collection_assets("cutout"), [])
        drafts = {mode: ledger.get_workflow_draft(mode) for mode in (
            "single", "multi-file", "group-split", "cutout-batch"
        )}
        self.assertEqual(drafts["single"]["collection_id"], drafts["multi-file"]["collection_id"])
        self.assertNotEqual(drafts["single"]["collection_id"], drafts["group-split"]["collection_id"])
        self.assertNotEqual(drafts["group-split"]["collection_id"], drafts["cutout-batch"]["collection_id"])

    def test_complete_v3_schema_with_stale_v2_marker_is_repaired_safely(self) -> None:
        AtelierLedger(self.db_path)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE ledger_meta SET value = '2' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()

        repaired = AtelierLedger(self.db_path)

        self.assertEqual(read_schema_version(self.db_path), SCHEMA_VERSION)
        self.assertEqual(
            repaired.last_schema_repair,
            "recovered complete v3 schema with stale v2 metadata; "
            "recovered complete v4 schema with stale v3 metadata",
        )
        self.assertIsNotNone(repaired.last_migration_backup)
        backup_path = repaired.last_migration_backup
        assert backup_path is not None
        self.assertEqual(read_schema_version(backup_path), 2)

    def test_incomplete_v3_schema_with_v2_marker_is_refused_without_mutation(self) -> None:
        AtelierLedger(self.db_path)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("DROP INDEX idx_traces_item")
            connection.execute(
                "UPDATE ledger_meta SET value = '2' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(PartialSchemaError, "idx_traces_item"):
            AtelierLedger(self.db_path)

        self.assertEqual(read_schema_version(self.db_path), 2)
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND name = 'idx_traces_item'"
            ).fetchone())
        finally:
            connection.close()
        backups = list(Path(self.temp_dir.name).glob("*.backup-v2-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(read_schema_version(backups[0]), 2)

    def test_scoped_drafts_soft_removal_and_job_snapshots_are_isolated(self) -> None:
        ledger = AtelierLedger(self.db_path)
        product = ledger.register_workspace_asset(
            sha256="1" * 64,
            storage_path="D:/assets/product.png",
            mime="image/png",
            size_bytes=64,
            width=8,
            height=8,
            name="product.png",
        )
        group = ledger.register_workspace_asset(
            sha256="2" * 64,
            storage_path="D:/assets/group.png",
            mime="image/png",
            size_bytes=64,
            width=8,
            height=8,
            name="group.png",
            collection_key="group",
        )
        cutout = ledger.register_workspace_asset(
            sha256="3" * 64,
            storage_path="D:/assets/cutout.png",
            mime="image/png",
            size_bytes=64,
            width=8,
            height=8,
            name="cutout.png",
            collection_key="cutout",
        )

        self.assertEqual(
            [item["id"] for item in ledger.list_collection_assets("product")],
            [product["id"]],
        )
        self.assertEqual(
            [item["id"] for item in ledger.list_collection_assets("group")],
            [group["id"]],
        )
        self.assertEqual(
            [item["id"] for item in ledger.list_collection_assets("cutout")],
            [cutout["id"]],
        )

        single = ledger.save_workflow_draft(
            "single",
            expected_revision=1,
            selected_asset_ids=[product["id"]],
            brief={"goal": "single"},
            parameters={"batch": 1},
            ui_state={"zoom": 1.25},
        )
        multi = ledger.save_workflow_draft(
            "multi-file",
            expected_revision=1,
            selected_asset_ids=[product["id"]],
            brief={"goal": "submitted"},
            parameters={"batch": 2},
            ui_state={"scroll_top": 180},
        )
        self.assertEqual(single["selected_asset_ids"], multi["selected_asset_ids"])
        self.assertNotEqual(single["brief"], multi["brief"])
        with self.assertRaises(DraftRevisionConflictError):
            ledger.save_workflow_draft(
                "single",
                expected_revision=1,
                selected_asset_ids=[product["id"]],
            )
        with self.assertRaisesRegex(ValueError, "outside its active collection"):
            ledger.save_workflow_draft(
                "single",
                expected_revision=single["revision"],
                selected_asset_ids=[group["id"]],
            )

        job, created = ledger.create_job(
            "multi-file",
            [product["id"]],
            engine_key="mock-cloud",
            parameters={
                "brief": {"goal": "submitted"},
                "intent_locks": {"packaging_text": True},
                "batch": 2,
                "knowledge_refs": ["K-1"],
            },
            idempotency_key="snapshot-contract",
        )
        self.assertTrue(created)
        self.assertEqual(job["snapshot"]["draft_revision"], multi["revision"])
        self.assertEqual(job["snapshot"]["source_asset_ids"], [product["id"]])
        self.assertEqual(job["snapshot"]["brief"], {"goal": "submitted"})
        ledger.save_workflow_draft(
            "multi-file",
            expected_revision=multi["revision"],
            selected_asset_ids=[product["id"]],
            brief={"goal": "changed-after-submit"},
            parameters={"batch": 4},
        )
        self.assertEqual(
            ledger.get_job(job["id"])["snapshot"]["brief"],
            {"goal": "submitted"},
        )

        ledger.add_asset_to_collection(product["id"], "group")
        single_before_remove = ledger.get_workflow_draft("single")
        multi_before_remove = ledger.get_workflow_draft("multi-file")
        removed = ledger.remove_asset_from_collection(product["id"], "product")
        self.assertEqual(removed["membership"]["status"], "trashed")
        self.assertEqual(ledger.list_collection_assets("product"), [])
        single_after_remove = ledger.get_workflow_draft("single")
        multi_after_remove = ledger.get_workflow_draft("multi-file")
        self.assertEqual(single_after_remove["selected_asset_ids"], [])
        self.assertEqual(multi_after_remove["selected_asset_ids"], [])
        self.assertEqual(
            single_after_remove["revision"], single_before_remove["revision"] + 1
        )
        self.assertEqual(
            multi_after_remove["revision"], multi_before_remove["revision"] + 1
        )
        self.assertEqual(
            ledger.get_job(job["id"])["snapshot"]["source_asset_ids"],
            [product["id"]],
        )
        self.assertIn(
            product["id"],
            [item["id"] for item in ledger.list_collection_assets("group")],
        )
        restored = ledger.add_asset_to_collection(product["id"], "product")
        self.assertEqual(restored["membership"]["status"], "active")
        self.assertEqual(
            ledger.get_workflow_draft("single")["selected_asset_ids"], []
        )
        self.assertEqual(
            ledger.get_workflow_draft("multi-file")["selected_asset_ids"], []
        )

    def test_migration_failure_rolls_back_schema_version_ddl_and_data(self) -> None:
        create_v1_fixture(self.db_path)
        before = snapshot_v1_data(self.db_path)

        class FailingLedger(AtelierLedger):
            @staticmethod
            def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
                connection.execute("ALTER TABLE assets ADD COLUMN should_rollback TEXT")
                raise RuntimeError("injected migration failure")

        with self.assertRaises(LedgerSchemaError):
            FailingLedger(self.db_path)

        self.assertEqual(read_schema_version(self.db_path), 1)
        self.assertEqual(snapshot_v1_data(self.db_path), before)
        connection = sqlite3.connect(self.db_path)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(assets)")}
            self.assertNotIn("should_rollback", columns)
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'asset_blobs'"
            ).fetchone())
        finally:
            connection.close()

        backups = list(Path(self.temp_dir.name).glob("*.backup-v1-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(read_schema_version(backups[0]), 1)
        self.assertEqual(snapshot_v1_data(backups[0]), before)

    def test_v3_migration_failure_rolls_back_all_workspace_objects(self) -> None:
        create_v2_workspace_fixture(self.db_path)

        class FailingV3Ledger(AtelierLedger):
            @staticmethod
            def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
                connection.execute(
                    "CREATE TABLE should_rollback_v3(id TEXT PRIMARY KEY)"
                )
                raise RuntimeError("injected v3 migration failure")

        with self.assertRaises(LedgerSchemaError):
            FailingV3Ledger(self.db_path)

        self.assertEqual(read_schema_version(self.db_path), 2)
        connection = sqlite3.connect(self.db_path)
        try:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertNotIn("should_rollback_v3", tables)
            self.assertNotIn("asset_collections", tables)
            self.assertEqual(
                connection.execute(
                    "SELECT name FROM assets WHERE id = 'ast_workspace'"
                ).fetchone()[0],
                "legacy.png",
            )
        finally:
            connection.close()

        backups = list(Path(self.temp_dir.name).glob("*.backup-v2-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(read_schema_version(backups[0]), 2)

    def test_concurrent_initializers_do_not_repeat_the_same_migration(self) -> None:
        create_v1_fixture(self.db_path)

        with ThreadPoolExecutor(max_workers=4) as pool:
            ledgers = list(pool.map(lambda _: AtelierLedger(self.db_path), range(8)))

        self.assertTrue(all(
            ledger.stats()["schema_version"] == SCHEMA_VERSION for ledger in ledgers
        ))
        self.assertEqual(
            len(list(Path(self.temp_dir.name).glob("*.backup-v1-*.sqlite3"))),
            1,
        )
        self.assertEqual(snapshot_v1_data(self.db_path)["sessions"][0]["id"], "ses_fixture")

    def test_cross_process_initializers_create_one_correctly_versioned_backup(self) -> None:
        create_v1_fixture(self.db_path)
        context = multiprocessing.get_context("spawn")

        with ProcessPoolExecutor(max_workers=4, mp_context=context) as pool:
            versions = list(pool.map(initialize_ledger_process, [str(self.db_path)] * 8))

        self.assertEqual(versions, [SCHEMA_VERSION] * 8)
        backups = list(Path(self.temp_dir.name).glob("*.backup-v1-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(read_schema_version(backups[0]), 1)
        self.assertEqual(read_schema_version(self.db_path), SCHEMA_VERSION)

    def test_v2_schema_contract_enforces_keys_ranges_and_partial_idempotency(self) -> None:
        ledger = AtelierLedger(self.db_path)
        session = ledger.create_session("multi-file", title="schema contract")
        source = ledger.add_asset(session["id"], "source", name="source.png")
        now = "2026-08-21T00:00:00+00:00"

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            expected_columns = {
                "asset_blobs": {
                    "id", "sha256", "storage_path", "mime", "size_bytes",
                    "width", "height", "created_at",
                },
                "jobs": {
                    "id", "session_id", "mode", "status", "priority", "total_items",
                    "completed_items", "failed_items", "canceled_items",
                    "requested_concurrency", "idempotency_key", "parameters_json",
                    "created_at", "queued_at", "started_at", "updated_at", "completed_at",
                },
                "job_items": {
                    "id", "job_id", "position", "source_asset_id", "generation_id",
                    "engine_key", "status", "progress", "attempt_count", "max_attempts",
                    "error_code", "error_message", "queued_at", "started_at",
                    "updated_at", "completed_at",
                },
                "task_attempts": {
                    "id", "job_item_id", "attempt_number", "engine_key", "model",
                    "status", "error_code", "error_message", "latency_ms",
                    "metadata_json", "started_at", "completed_at",
                },
            }
            for table, expected in expected_columns.items():
                actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                self.assertEqual(actual, expected)
            asset_columns = {row[1] for row in connection.execute("PRAGMA table_info(assets)")}
            self.assertIn("blob_id", asset_columns)

            connection.execute(
                """
                INSERT INTO asset_blobs(id, sha256, storage_path, mime, size_bytes, width, height, created_at)
                VALUES('blob_1', ?, '/assets/aa/one.png', 'image/png', 10, 2, 2, ?)
                """,
                ("a" * 64, now),
            )
            connection.execute(
                "UPDATE assets SET blob_id = 'blob_1' WHERE id = ?", (source["id"],)
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO asset_blobs(id, sha256, storage_path, mime, size_bytes, width, height, created_at)
                    VALUES('blob_dup_hash', ?, '/assets/bb/two.png', 'image/png', 10, 2, 2, ?)
                    """,
                    ("a" * 64, now),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO asset_blobs(id, sha256, storage_path, mime, size_bytes, width, height, created_at)
                    VALUES('blob_dup_path', ?, '/assets/aa/one.png', 'image/png', 10, 2, 2, ?)
                    """,
                    ("b" * 64, now),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM asset_blobs WHERE id = 'blob_1'")

            job_insert = """
                INSERT INTO jobs(
                    id, session_id, mode, idempotency_key, parameters_json,
                    created_at, queued_at, updated_at
                ) VALUES(?, ?, 'multi-file', ?, '{}', ?, ?, ?)
            """
            connection.execute(job_insert, ("job_empty_1", session["id"], "", now, now, now))
            connection.execute(job_insert, ("job_empty_2", session["id"], "", now, now, now))
            connection.execute(job_insert, ("job_keyed", session["id"], "request-1", now, now, now))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    job_insert,
                    ("job_keyed_duplicate", session["id"], "request-1", now, now, now),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO jobs(
                        id, session_id, mode, status, requested_concurrency,
                        created_at, queued_at, updated_at
                    ) VALUES('job_invalid', ?, 'single', 'not-a-state', 0, ?, ?, ?)
                    """,
                    (session["id"], now, now, now),
                )

            item_insert = """
                INSERT INTO job_items(
                    id, job_id, position, source_asset_id, engine_key,
                    queued_at, updated_at
                ) VALUES(?, 'job_keyed', ?, ?, 'mock-cloud', ?, ?)
            """
            connection.execute(item_insert, ("item_1", 0, source["id"], now, now))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(item_insert, ("item_duplicate_position", 0, source["id"], now, now))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO job_items(
                        id, job_id, position, source_asset_id, engine_key, status,
                        progress, queued_at, updated_at
                    ) VALUES('item_bad_progress', 'job_keyed', 1, ?, 'mock-cloud',
                             'queued', 1.5, ?, ?)
                    """,
                    (source["id"], now, now),
                )

            attempt_insert = """
                INSERT INTO task_attempts(
                    id, job_item_id, attempt_number, engine_key, status, started_at
                ) VALUES(?, 'item_1', ?, 'mock-cloud', 'running', ?)
            """
            connection.execute(attempt_insert, ("attempt_1", 1, now))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(attempt_insert, ("attempt_duplicate", 1, now))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(attempt_insert, ("attempt_zero", 0, now))

            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            connection.commit()
        finally:
            connection.close()

    def test_future_schema_is_rejected_without_overwriting_version_or_data(self) -> None:
        create_v1_fixture(self.db_path)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE ledger_meta SET value = '99' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()
        before = snapshot_v1_data(self.db_path)
        before_hash = hashlib.sha256(self.db_path.read_bytes()).hexdigest()

        with self.assertRaises(UnsupportedSchemaVersionError):
            AtelierLedger(self.db_path)

        self.assertEqual(read_schema_version(self.db_path), 99)
        self.assertEqual(snapshot_v1_data(self.db_path), before)
        self.assertEqual(hashlib.sha256(self.db_path.read_bytes()).hexdigest(), before_hash)
        self.assertEqual(list(Path(self.temp_dir.name).glob("*.backup-*.sqlite3")), [])
        self.assertFalse(Path(f"{self.db_path}-wal").exists())
        self.assertFalse(Path(f"{self.db_path}-shm").exists())
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        finally:
            connection.close()

    def test_frozen_job_state_machine_accepts_retry_and_rejects_illegal_edges(self) -> None:
        validate_status_transition("queued", "running")
        validate_status_transition("running", "partial")
        validate_status_transition("interrupted", "partial")
        validate_status_transition("partial", "running")
        validate_status_transition("failed", "queued", item=True)
        validate_status_transition("running", "canceling", item=True)
        validate_status_transition("canceling", "canceled", item=True)

        with self.assertRaises(InvalidStatusTransitionError):
            validate_status_transition("completed", "running")
        with self.assertRaises(InvalidStatusTransitionError):
            validate_status_transition("queued", "completed", item=True)
        with self.assertRaises(InvalidStatusTransitionError):
            validate_status_transition("unknown", "queued")


if __name__ == "__main__":
    unittest.main(verbosity=2)
