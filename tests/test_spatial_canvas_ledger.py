from __future__ import annotations

import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path

from python.atelier_ledger import (
    AtelierLedger,
    IdempotencyConflictError,
    PartialSchemaError,
    SCHEMA_VERSION,
    SpatialCanvasRevisionConflictError,
    SpatialSceneCorruptedError,
)


def spatial_scene(asset_id: str | None = None) -> dict:
    custom_data = {
        "asset_id": asset_id,
        "result_id": None,
        "task_id": None,
        "product_profile_version_id": None,
        "lineage_parent_id": None,
    }
    return {
        "schema_version": 1,
        "elements": [
            {
                "id": "frame-main",
                "type": "frame",
                "x": 100,
                "y": 120,
                "width": 900,
                "height": 700,
                "angle": 0,
                "isDeleted": False,
                "locked": True,
                "groupIds": ["group-main"],
                "customData": custom_data,
            }
        ],
        "app_state": {
            "viewBackgroundColor": "#d4d0cb",
            "currentItemRoughness": 0,
            "currentItemStrokeStyle": "solid",
            "currentItemFillStyle": "solid",
            "gridSize": 20,
            "gridStep": 5,
            "gridModeEnabled": False,
            "zoom": {"value": 0.8},
            "scrollX": -240,
            "scrollY": -180,
        },
        "files": {},
    }


def create_v7_database(path: Path) -> None:
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
        AtelierLedger._migrate_v3_to_v4(connection)
        AtelierLedger._write_schema_version(connection, 4)
        AtelierLedger._migrate_v4_to_v5(connection)
        AtelierLedger._write_schema_version(connection, 5)
        AtelierLedger._migrate_v5_to_v6(connection)
        AtelierLedger._write_schema_version(connection, 6)
        AtelierLedger._migrate_v6_to_v7(connection)
        AtelierLedger._write_schema_version(connection, 7)
        connection.commit()
    finally:
        connection.close()


class SpatialCanvasLedgerTests(unittest.TestCase):
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

    def test_schema_v8_has_immutable_spatial_scene_objects(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 8)
        connection = sqlite3.connect(self.db_path)
        try:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertTrue({
                "spatial_canvas_documents",
                "spatial_canvas_scene_versions",
                "spatial_scene_references",
            }.issubset(tables))
            triggers = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                )
            }
            self.assertTrue({
                "trg_spatial_scene_versions_no_update",
                "trg_spatial_scene_versions_no_delete",
                "trg_spatial_scene_references_no_update",
                "trg_spatial_scene_references_no_delete",
            }.issubset(triggers))
        finally:
            connection.close()

    def test_create_list_open_and_rename_are_durable_and_idempotent(self) -> None:
        created = self.ledger.create_spatial_canvas(
            name="  商品   主图方案  ", client_request_id="spatial-create-1"
        )
        replay = self.ledger.create_spatial_canvas(
            name="商品 主图方案", client_request_id="spatial-create-1"
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(created["id"], replay["id"])
        self.assertEqual(created["name"], "商品 主图方案")
        self.assertEqual(created["current_revision"], 1)
        self.assertNotIn("scene", self.ledger.list_spatial_canvases()[0])

        renamed = self.ledger.rename_spatial_canvas(created["id"], "白底图对比")
        self.assertEqual(renamed["name"], "白底图对比")
        opened = self.ledger.open_spatial_canvas(created["id"])
        self.assertEqual(opened["scene"]["elements"], [])
        self.assertEqual(self.ledger.list_spatial_canvases()[0]["id"], created["id"])

        restarted = AtelierLedger(self.db_path)
        restored = restarted.get_spatial_canvas(created["id"])
        self.assertEqual(restored["name"], "白底图对比")
        self.assertEqual(restored["current_version_id"], created["current_version_id"])

    def test_create_with_initial_scene_is_atomic_idempotent_and_reference_safe(self) -> None:
        scene = spatial_scene(self.asset["id"])
        created = self.ledger.create_spatial_canvas(
            name="并发冲突副本",
            client_request_id="spatial-conflict-copy-1",
            scene=scene,
        )
        replay = self.ledger.create_spatial_canvas(
            name="并发冲突副本",
            client_request_id="spatial-conflict-copy-1",
            scene=copy.deepcopy(scene),
        )
        self.assertEqual(created["current_revision"], 1)
        self.assertEqual(created["scene"]["elements"][0]["customData"]["asset_id"], self.asset["id"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["id"], created["id"])
        self.assertEqual(len(self.ledger.list_spatial_canvases()), 1)
        references = self.ledger.asset_reference_summary(self.asset["id"])
        self.assertEqual(
            references["references"]["spatial_scene_versions"],
            [created["current_version_id"]],
        )

        changed = copy.deepcopy(scene)
        changed["elements"][0]["x"] = 777
        with self.assertRaises(IdempotencyConflictError):
            self.ledger.create_spatial_canvas(
                name="并发冲突副本",
                client_request_id="spatial-conflict-copy-1",
                scene=changed,
            )

        dangling = spatial_scene("ast_missing")
        with self.assertRaises(KeyError):
            self.ledger.create_spatial_canvas(
                name="无效冲突副本",
                client_request_id="spatial-conflict-copy-dangling",
                scene=dangling,
            )
        self.assertEqual(len(self.ledger.list_spatial_canvases()), 1)
        connection = sqlite3.connect(self.db_path)
        try:
            empty_count = connection.execute(
                "SELECT COUNT(*) FROM spatial_canvas_documents WHERE create_request_id = ?",
                ("spatial-conflict-copy-dangling",),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(empty_count, 0)

    def test_scene_versions_are_replayable_deduplicated_and_optimistically_locked(self) -> None:
        canvas = self.ledger.create_spatial_canvas(
            name="版本测试", client_request_id="spatial-version-create"
        )
        scene = spatial_scene(self.asset["id"])
        saved = self.ledger.save_spatial_canvas_scene(
            canvas["id"],
            expected_revision=1,
            client_request_id="spatial-save-1",
            scene=scene,
        )
        replay = self.ledger.save_spatial_canvas_scene(
            canvas["id"],
            expected_revision=1,
            client_request_id="spatial-save-1",
            scene=scene,
        )
        self.assertFalse(saved["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(saved["current_revision"], 2)
        self.assertEqual(saved["current_version_id"], replay["current_version_id"])

        unchanged = self.ledger.save_spatial_canvas_scene(
            canvas["id"],
            expected_revision=2,
            client_request_id="spatial-save-unchanged",
            scene={
                **scene,
                "elements": [{**scene["elements"][0], "boundElements": []}],
            },
        )
        self.assertTrue(unchanged["unchanged"])
        self.assertEqual(unchanged["current_revision"], 2)

        moved = copy.deepcopy(scene)
        moved["elements"][0]["x"] = 600
        second = self.ledger.save_spatial_canvas_scene(
            canvas["id"],
            expected_revision=2,
            client_request_id="spatial-save-2",
            scene=moved,
        )
        self.assertEqual(second["current_revision"], 3)
        first_version = self.ledger.get_spatial_canvas_version(saved["current_version_id"])
        self.assertEqual(first_version["scene"]["elements"][0]["x"], 100)

        with self.assertRaises(SpatialCanvasRevisionConflictError) as conflict:
            self.ledger.save_spatial_canvas_scene(
                canvas["id"],
                expected_revision=2,
                client_request_id="spatial-save-stale",
                scene=moved,
            )
        self.assertEqual(conflict.exception.current["current_revision"], 3)

        conflicting = copy.deepcopy(scene)
        conflicting["elements"][0]["y"] = 999
        with self.assertRaises(IdempotencyConflictError):
            self.ledger.save_spatial_canvas_scene(
                canvas["id"],
                expected_revision=1,
                client_request_id="spatial-save-1",
                scene=conflicting,
            )

    def test_scene_contract_rejects_file_bytes_paths_and_dangling_assets(self) -> None:
        canvas = self.ledger.create_spatial_canvas(
            name="安全合同", client_request_id="spatial-safe-create"
        )
        cases = []
        embedded = spatial_scene()
        embedded["files"] = {"file-1": {"dataURL": "data:image/png;base64,AAAA"}}
        cases.append(embedded)
        absolute = spatial_scene()
        absolute["elements"][0]["link"] = "C:\\Users\\name\\source.png"
        cases.append(absolute)
        nested = spatial_scene()
        nested["elements"][0]["customData"]["result_id"] = "data:video/mp4;base64,AAAA"
        cases.append(nested)
        for index, candidate in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.ledger.save_spatial_canvas_scene(
                    canvas["id"],
                    expected_revision=1,
                    client_request_id=f"spatial-unsafe-{index}",
                    scene=candidate,
                )

        dangling = spatial_scene("ast_missing")
        with self.assertRaises(KeyError):
            self.ledger.save_spatial_canvas_scene(
                canvas["id"],
                expected_revision=1,
                client_request_id="spatial-dangling",
                scene=dangling,
            )
        dangling_lineage = spatial_scene()
        dangling_lineage["elements"][0]["customData"]["lineage_parent_id"] = "ast_missing"
        with self.assertRaises(KeyError):
            self.ledger.save_spatial_canvas_scene(
                canvas["id"],
                expected_revision=1,
                client_request_id="spatial-dangling-lineage",
                scene=dangling_lineage,
            )

    def test_scene_contract_accepts_excalidraw_nanoid_prefixes(self) -> None:
        canvas = self.ledger.create_spatial_canvas(
            name="NanoID 合同", client_request_id="spatial-nanoid-create"
        )
        scene = spatial_scene()
        first = scene["elements"][0]
        first["id"] = "_excalidraw-element"
        second = copy.deepcopy(first)
        second["id"] = "-excalidraw-element"
        second["x"] = 1200
        scene["elements"].append(second)
        saved = self.ledger.save_spatial_canvas_scene(
            canvas["id"],
            expected_revision=1,
            client_request_id="spatial-nanoid-save",
            scene=scene,
        )
        self.assertEqual(
            [element["id"] for element in saved["scene"]["elements"]],
            ["_excalidraw-element", "-excalidraw-element"],
        )

    def test_restart_restores_view_frame_group_lock_and_business_references(self) -> None:
        canvas = self.ledger.create_spatial_canvas(
            name="恢复测试", client_request_id="spatial-restore-create"
        )
        saved = self.ledger.save_spatial_canvas_scene(
            canvas["id"],
            expected_revision=1,
            client_request_id="spatial-restore-save",
            scene=spatial_scene(self.asset["id"]),
        )
        restarted = AtelierLedger(self.db_path)
        restored = restarted.open_spatial_canvas(canvas["id"])
        element = restored["scene"]["elements"][0]
        self.assertEqual(restored["current_version_id"], saved["current_version_id"])
        self.assertEqual(restored["scene"]["app_state"]["zoom"]["value"], 0.8)
        self.assertEqual(restored["scene"]["app_state"]["scrollX"], -240)
        self.assertEqual(element["type"], "frame")
        self.assertEqual(element["groupIds"], ["group-main"])
        self.assertTrue(element["locked"])
        self.assertEqual(element["customData"]["asset_id"], self.asset["id"])

        references = restarted.asset_reference_summary(self.asset["id"])
        self.assertEqual(
            references["references"]["spatial_scene_versions"],
            [saved["current_version_id"]],
        )
        self.assertIn("spatial_scene_versions", references["blockers"])

    def test_corrupted_scene_checksum_is_refused(self) -> None:
        canvas = self.ledger.create_spatial_canvas(
            name="损坏测试", client_request_id="spatial-corrupt-create"
        )
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("DROP TRIGGER trg_spatial_scene_versions_no_update")
            connection.execute(
                "UPDATE spatial_canvas_scene_versions SET scene_json = '{}' WHERE id = ?",
                (canvas["current_version_id"],),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(SpatialSceneCorruptedError):
            self.ledger.get_spatial_canvas(canvas["id"])

    def test_list_uses_lightweight_thumbnail_and_rejects_thumbnail_corruption(self) -> None:
        canvas = self.ledger.create_spatial_canvas(
            name="轻量列表", client_request_id="spatial-list-create"
        )
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("DROP TRIGGER trg_spatial_scene_versions_no_update")
            connection.execute(
                "UPDATE spatial_canvas_scene_versions SET scene_json = '{}' WHERE id = ?",
                (canvas["current_version_id"],),
            )
            connection.commit()
        finally:
            connection.close()
        listing = self.ledger.list_spatial_canvases()
        self.assertEqual(listing[0]["id"], canvas["id"])
        self.assertNotIn("scene", listing[0])
        with self.assertRaises(SpatialSceneCorruptedError):
            self.ledger.get_spatial_canvas(canvas["id"])

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE spatial_canvas_scene_versions SET thumbnail_json = '{}' WHERE id = ?",
                (canvas["current_version_id"],),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(SpatialSceneCorruptedError):
            self.ledger.list_spatial_canvases()


class SpatialCanvasMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "atelier.sqlite3"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_v7_upgrade_is_recoverable_and_restart_does_not_duplicate_backup(self) -> None:
        create_v7_database(self.db_path)
        ledger = AtelierLedger(self.db_path)
        self.assertEqual(ledger.stats()["schema_version"], 8)
        backup = ledger.last_migration_backup
        self.assertIsNotNone(backup)
        assert backup is not None
        connection = sqlite3.connect(backup)
        try:
            version = int(connection.execute(
                "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
            ).fetchone()[0])
            self.assertEqual(version, 7)
        finally:
            connection.close()
        first_backups = list(self.root.glob("*.backup-v7-*.sqlite3"))
        restarted = AtelierLedger(self.db_path)
        self.assertIsNone(restarted.last_migration_backup)
        self.assertEqual(list(self.root.glob("*.backup-v7-*.sqlite3")), first_backups)

    def test_complete_v8_with_stale_v7_marker_repairs_metadata(self) -> None:
        AtelierLedger(self.db_path)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE ledger_meta SET value = '7' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()
        repaired = AtelierLedger(self.db_path)
        self.assertEqual(repaired.stats()["schema_version"], 8)
        self.assertIn("recovered complete v8 schema", repaired.last_schema_repair)

    def test_partial_v8_with_v7_marker_is_refused_without_mutation(self) -> None:
        AtelierLedger(self.db_path)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("DROP TRIGGER trg_spatial_scene_versions_no_update")
            connection.execute(
                "UPDATE ledger_meta SET value = '7' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(PartialSchemaError, "trg_spatial_scene_versions_no_update"):
            AtelierLedger(self.db_path)
        connection = sqlite3.connect(self.db_path)
        try:
            version = int(connection.execute(
                "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
            ).fetchone()[0])
            self.assertEqual(version, 7)
        finally:
            connection.close()

    def test_v8_migration_failure_rolls_back_all_spatial_objects(self) -> None:
        create_v7_database(self.db_path)

        class FailingV8Ledger(AtelierLedger):
            @staticmethod
            def _migrate_v7_to_v8(connection: sqlite3.Connection) -> None:
                connection.execute("CREATE TABLE should_rollback_v8(id TEXT PRIMARY KEY)")
                raise RuntimeError("injected v8 migration failure")

        with self.assertRaisesRegex(Exception, "schema v8"):
            FailingV8Ledger(self.db_path)
        connection = sqlite3.connect(self.db_path)
        try:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertNotIn("should_rollback_v8", tables)
            self.assertNotIn("spatial_canvas_documents", tables)
            version = int(connection.execute(
                "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
            ).fetchone()[0])
            self.assertEqual(version, 7)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
