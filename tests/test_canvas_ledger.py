from __future__ import annotations

import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path

from python.atelier_ledger import (
    AtelierLedger,
    CanvasRevisionConflictError,
    IdempotencyConflictError,
    LedgerSchemaError,
    PartialSchemaError,
    SCHEMA_VERSION,
)


def canvas_document(asset_id: str, *, document_id: str = "canvas:test-single") -> dict:
    return {
        "id": document_id,
        "schema_version": 1,
        "coordinate_system": {
            "unit": "canvas-pixel",
            "origin": "top-left",
            "x_axis": "right",
            "y_axis": "down",
        },
        "revision": 0,
        "active_artboard_id": "artboard:main",
        "source_asset_ids": [asset_id],
        "artboards": [{
            "id": "artboard:main",
            "name": "主画板",
            "rect": {"x": 0, "y": 0, "width": 1200, "height": 1200},
            "export": {
                "pixel_width": 2400,
                "pixel_height": 2400,
                "color_space": "srgb",
            },
        }],
        "layers": [{
            "id": "layer:source",
            "artboard_id": "artboard:main",
            "source": {
                "kind": "asset",
                "id": asset_id,
                "proxy_ref": "proxy:thumbnail:512",
                "original_pixel_width": 4096,
                "original_pixel_height": 4096,
            },
            "transform": {
                "x": 100,
                "y": 80,
                "scale_x": 0.25,
                "scale_y": 0.25,
                "rotation_degrees": 0,
                "opacity": 1,
            },
            "z_index": 0,
            "visible": True,
            "locked": False,
        }],
        "operations": [],
        "undo_cursor": -1,
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
    }


def create_v3_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        AtelierLedger._create_v1_schema(connection)
        AtelierLedger._write_schema_version(connection, 1)
        AtelierLedger._migrate_v1_to_v2(connection)
        AtelierLedger._write_schema_version(connection, 2)
        AtelierLedger._migrate_v2_to_v3(connection)
        AtelierLedger._write_schema_version(connection, 3)
        connection.commit()
    finally:
        connection.close()


class CanvasLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "atelier.sqlite3"
        self.ledger = AtelierLedger(self.db_path)
        self.asset = self.ledger.register_workspace_asset(
            sha256="a" * 64,
            storage_path=str(self.root / "source.png"),
            mime="image/png",
            size_bytes=128,
            width=4096,
            height=4096,
            name="source.png",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_current_schema_preserves_immutable_canvas_versions_and_job_bindings(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 7)
        connection = sqlite3.connect(self.db_path)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertTrue({
                "canvas_documents",
                "canvas_document_versions",
                "canvas_version_sources",
            }.issubset(tables))
            snapshot_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(job_snapshots)")
            }
            self.assertTrue({
                "command_id",
                "canvas_document_version_id",
                "canvas_operation_id",
            }.issubset(snapshot_columns))
            trace_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(execution_traces)")
            }
            self.assertTrue({
                "command_id",
                "canvas_document_version_id",
                "canvas_operation_id",
            }.issubset(trace_columns))
            triggers = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                )
            }
            self.assertTrue({
                "trg_canvas_versions_no_update",
                "trg_canvas_versions_no_delete",
                "trg_canvas_sources_no_update",
                "trg_canvas_sources_no_delete",
            }.issubset(triggers))
        finally:
            connection.close()

    def test_versions_are_immutable_replayable_and_optimistically_locked(self) -> None:
        original = canvas_document(self.asset["id"])
        first = self.ledger.save_canvas_document(
            "single",
            expected_revision=0,
            client_request_id="canvas-save-1",
            document=original,
        )
        replay = self.ledger.save_canvas_document(
            "single",
            expected_revision=0,
            client_request_id="canvas-save-1",
            document=original,
        )
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["version"]["id"], replay["version"]["id"])
        self.assertEqual(first["document"]["revision"], 1)

        changed = copy.deepcopy(first["document"])
        changed["layers"][0]["transform"]["x"] = 260
        second = self.ledger.save_canvas_document(
            "single",
            expected_revision=1,
            client_request_id="canvas-save-2",
            document=changed,
        )
        self.assertEqual(second["document"]["revision"], 2)
        self.assertEqual(
            second["version"]["parent_version_id"], first["version"]["id"]
        )
        first_again = self.ledger.get_canvas_document_version(first["version"]["id"])
        self.assertEqual(first_again["document"]["layers"][0]["transform"]["x"], 100)

        with self.assertRaises(CanvasRevisionConflictError) as conflict:
            self.ledger.save_canvas_document(
                "single",
                expected_revision=1,
                client_request_id="canvas-save-stale",
                document=changed,
            )
        self.assertEqual(conflict.exception.current["revision"], 2)

        conflicting = copy.deepcopy(original)
        conflicting["artboards"][0]["name"] = "不同请求"
        with self.assertRaises(IdempotencyConflictError):
            self.ledger.save_canvas_document(
                "single",
                expected_revision=0,
                client_request_id="canvas-save-1",
                document=conflicting,
            )

        connection = sqlite3.connect(self.db_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE canvas_document_versions SET document_json = '{}' WHERE id = ?",
                    (first["version"]["id"],),
                )
        finally:
            connection.close()

    def test_coordinate_source_and_proxy_contract_rejects_unsafe_documents(self) -> None:
        invalid_origin = canvas_document(self.asset["id"], document_id="canvas:bad-origin")
        invalid_origin["coordinate_system"]["origin"] = "center"
        with self.assertRaisesRegex(ValueError, "coordinate_system"):
            self.ledger.save_canvas_document(
                "multi-file",
                expected_revision=0,
                client_request_id="canvas-bad-origin",
                document=invalid_origin,
            )

        embedded = canvas_document(self.asset["id"], document_id="canvas:embedded")
        embedded["layers"][0]["source"]["proxy_ref"] = "data:image/png;base64,AA=="
        with self.assertRaisesRegex(ValueError, "proxy_ref"):
            self.ledger.save_canvas_document(
                "multi-file",
                expected_revision=0,
                client_request_id="canvas-embedded",
                document=embedded,
            )

        missing = canvas_document("ast_missing", document_id="canvas:missing")
        with self.assertRaises(KeyError):
            self.ledger.save_canvas_document(
                "multi-file",
                expected_revision=0,
                client_request_id="canvas-missing",
                document=missing,
            )

        saved = self.ledger.save_canvas_document(
            "single",
            expected_revision=0,
            client_request_id="canvas-proxy",
            document=canvas_document(self.asset["id"], document_id="canvas:proxy"),
        )
        proxy = saved["proxies"][0]
        self.assertEqual(proxy["source_id"], self.asset["id"])
        self.assertEqual(proxy["authoritative_source"], "assets.id")
        self.assertTrue(proxy["rebuildable"])
        self.assertFalse(proxy["cache_is_authoritative"])
        self.assertEqual(
            proxy["url"],
            f"/api/assets/{self.asset['id']}/thumbnail?size=512",
        )
        references = self.ledger.asset_reference_summary(self.asset["id"])
        self.assertEqual(
            references["references"]["canvas_versions"],
            [saved["version"]["id"]],
        )
        self.assertIn("canvas_versions", references["blockers"])

    def test_result_source_mutation_allows_empty_workspace_source_set(self) -> None:
        first = self.ledger.save_canvas_document(
            "single",
            expected_revision=0,
            client_request_id="canvas-source-mutation-base",
            document=canvas_document(self.asset["id"]),
        )
        result = self.ledger.add_asset(
            self.asset["session_id"],
            "result_main",
            parent_asset_id=self.asset["id"],
            path=str(self.root / "local-result.png"),
            name="local-result.png",
            mime="image/png",
            width=4096,
            height=4096,
            sha256="b" * 64,
        )
        changed = copy.deepcopy(first["document"])
        layer = changed["layers"][0]
        before_source = copy.deepcopy(layer["source"])
        after_source = {
            "kind": "result",
            "id": result["id"],
            "proxy_ref": "proxy:thumbnail:512",
            "original_pixel_width": 4096,
            "original_pixel_height": 4096,
        }
        layer["source"] = copy.deepcopy(after_source)
        changed["source_asset_ids"] = []
        changed["operations"] = [{
            "id": "operation:local-source-swap",
            "command_id": "command:local-edit-compose",
            "input_layer_ids": ["layer:source"],
            "output_layer_id": "layer:source",
            "roi_id": "roi:local-source-swap",
            "mask_id": "maskver:local-source-swap",
            "product_profile_id": None,
            "mutation": {
                "target_layer_id": "layer:source",
                "before": {
                    "source": before_source,
                    "transform": copy.deepcopy(layer["transform"]),
                    "z_index": 0,
                    "visible": True,
                    "locked": False,
                },
                "after": {
                    "source": after_source,
                    "transform": copy.deepcopy(layer["transform"]),
                    "z_index": 0,
                    "visible": True,
                    "locked": False,
                },
            },
            "cost": {
                "mode": "free",
                "confirmed_call_count": 0,
                "user_confirmation_required": False,
                "automatic_paid_retry": False,
            },
            "status": "succeeded",
            "created_at": "2026-09-02T01:00:00Z",
        }]
        changed["undo_cursor"] = 0
        saved = self.ledger.save_canvas_document(
            "single",
            expected_revision=1,
            client_request_id="canvas-source-mutation-result",
            document=changed,
        )
        self.assertEqual(saved["document"]["source_asset_ids"], [])
        self.assertEqual(
            saved["document"]["layers"][0]["source"]["id"], result["id"]
        )

    def test_job_snapshot_binds_the_exact_canvas_version_and_command(self) -> None:
        saved = self.ledger.save_canvas_document(
            "single",
            expected_revision=0,
            client_request_id="canvas-job-save",
            document=canvas_document(self.asset["id"], document_id="canvas:job"),
        )
        job, created = self.ledger.create_job(
            "single",
            [self.asset["id"]],
            engine_key="mock-cloud",
            parameters={"batch": 1},
            idempotency_key="canvas-job",
            command_id="command:existing-generate-single",
            canvas_document_id="canvas:job",
            expected_canvas_revision=1,
            canvas_operation_id="operation:generate-1",
        )
        self.assertTrue(created)
        self.assertEqual(job["snapshot"]["command_id"], "command:existing-generate-single")
        self.assertEqual(
            job["snapshot"]["canvas_document_version_id"],
            saved["version"]["id"],
        )
        self.assertEqual(job["snapshot"]["canvas_operation_id"], "operation:generate-1")


class CanvasMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "atelier.sqlite3"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_v3_upgrade_creates_current_schema_and_a_queryable_v3_backup(self) -> None:
        create_v3_database(self.db_path)
        ledger = AtelierLedger(self.db_path)
        self.assertEqual(ledger.stats()["schema_version"], 7)
        self.assertIsNotNone(ledger.last_migration_backup)
        backup = ledger.last_migration_backup
        assert backup is not None
        connection = sqlite3.connect(backup)
        try:
            version = int(connection.execute(
                "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
            ).fetchone()[0])
            self.assertEqual(version, 3)
        finally:
            connection.close()

    def test_incomplete_v4_with_v3_marker_is_refused_without_mutation(self) -> None:
        AtelierLedger(self.db_path)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("DROP TRIGGER trg_canvas_versions_no_update")
            connection.execute(
                "UPDATE ledger_meta SET value = '3' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(PartialSchemaError, "trg_canvas_versions_no_update"):
            AtelierLedger(self.db_path)

        connection = sqlite3.connect(self.db_path)
        try:
            version = int(connection.execute(
                "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
            ).fetchone()[0])
            self.assertEqual(version, 3)
        finally:
            connection.close()

    def test_v4_migration_failure_rolls_back_all_canvas_objects(self) -> None:
        create_v3_database(self.db_path)

        class FailingV4Ledger(AtelierLedger):
            @staticmethod
            def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
                connection.execute("CREATE TABLE should_rollback_v4(id TEXT PRIMARY KEY)")
                raise RuntimeError("injected v4 migration failure")

        with self.assertRaises(LedgerSchemaError):
            FailingV4Ledger(self.db_path)

        connection = sqlite3.connect(self.db_path)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertNotIn("should_rollback_v4", tables)
            self.assertNotIn("canvas_documents", tables)
            version = int(connection.execute(
                "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
            ).fetchone()[0])
            self.assertEqual(version, 3)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
