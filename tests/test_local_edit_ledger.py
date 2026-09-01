from __future__ import annotations

import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from python.atelier_ledger import (
    AtelierLedger,
    CanvasRevisionConflictError,
    IdempotencyConflictError,
    LedgerSchemaError,
    PartialSchemaError,
    SCHEMA_VERSION,
)
from python.local_edit_contract import image_fingerprint
from tests.test_canvas_ledger import canvas_document


def create_v5_database(path: Path) -> None:
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
        connection.commit()
    finally:
        connection.close()


def mask_definition(*, x: int = 120) -> dict:
    return {
        "schema_version": 1,
        "coordinate_space": "source-pixel",
        "width": 4096,
        "height": 4096,
        "base": "empty",
        "strokes": [{
            "mode": "include",
            "radius": 24,
            "points": [{"x": x, "y": 160}, {"x": x + 80, "y": 240}],
        }],
        "feather_radius": 0,
    }


class LocalEditLedgerTests(unittest.TestCase):
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
        self.canvas = self.ledger.save_canvas_document(
            "single",
            expected_revision=0,
            client_request_id="local-edit-canvas-1",
            document=canvas_document(self.asset["id"], document_id="canvas:local-edit"),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_roi(self, *, request_id: str = "roi-request-1", x: int = 64) -> dict:
        return self.ledger.create_canvas_roi(
            canvas_document_id="canvas:local-edit",
            expected_canvas_revision=1,
            source_layer_id="layer:source",
            coordinate_space="source-pixel",
            rect={"x": x, "y": 80, "width": 1024, "height": 1200},
            purpose="inpaint",
            client_request_id=request_id,
        )

    def create_mask(self, roi_id: str, *, request_id: str = "mask-request-1") -> dict:
        pixel_mask = Image.new("L", (4096, 4096), 0)
        pixel_mask.putpixel((120, 160), 255)
        return self.ledger.save_canvas_mask(
            roi_id=roi_id,
            expected_revision=0,
            definition=mask_definition(),
            pixel_sha256=image_fingerprint(pixel_mask),
            client_request_id=request_id,
        )

    def local_contract(self, roi: dict, mask: dict) -> dict:
        return {
            "schema_version": 1,
            "operation_id": "operation:local-edit-ledger-1",
            "mode": "inpaint",
            "source_canvas_version_id": self.canvas["version"]["id"],
            "source_layer_id": "layer:source",
            "source_sha256": "A" * 64,
            "source_size": {"width": 4096, "height": 4096},
            "roi": {
                "id": roi["id"],
                "coordinate_space": "source-pixel",
                "rect": copy.deepcopy(roi["rect"]),
            },
            "mask": {
                "id": mask["version"]["id"],
                "roi_id": roi["id"],
                "width": 4096,
                "height": 4096,
                "sha256": mask["version"]["pixel_sha256"],
            },
            "strict_pixel_protection": True,
            "cost": {
                "mode": "free",
                "confirmed_call_count": 0,
                "user_confirmation_required": False,
                "user_confirmed": False,
                "automatic_paid_retry": False,
            },
        }

    def test_schema_v6_has_immutable_roi_mask_and_edit_spec_contracts(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 6)
        connection = sqlite3.connect(self.db_path)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertTrue({
                "canvas_rois",
                "canvas_masks",
                "canvas_mask_versions",
                "local_edit_specs",
            }.issubset(tables))
            snapshot_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(job_snapshots)")
            }
            trace_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(execution_traces)")
            }
            self.assertIn("local_edit_spec_id", snapshot_columns)
            self.assertIn("local_edit_spec_id", trace_columns)
            triggers = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                )
            }
            self.assertTrue({
                "trg_canvas_rois_no_update",
                "trg_canvas_rois_no_delete",
                "trg_canvas_mask_versions_no_update",
                "trg_canvas_mask_versions_no_delete",
                "trg_local_edit_specs_no_update",
                "trg_local_edit_specs_no_delete",
            }.issubset(triggers))
        finally:
            connection.close()

    def test_roi_mask_and_spec_are_idempotent_versioned_and_replayable(self) -> None:
        roi = self.create_roi()
        replayed_roi = self.create_roi()
        self.assertFalse(roi["replayed"])
        self.assertTrue(replayed_roi["replayed"])
        self.assertEqual(roi["id"], replayed_roi["id"])

        with self.assertRaises(IdempotencyConflictError):
            self.create_roi(x=65)

        mask_v1 = self.create_mask(roi["id"])
        replayed_mask = self.create_mask(roi["id"])
        self.assertFalse(mask_v1["replayed"])
        self.assertTrue(replayed_mask["replayed"])
        self.assertEqual(mask_v1["version"]["id"], replayed_mask["version"]["id"])

        changed_definition = mask_definition(x=240)
        pixel_mask_v2 = Image.new("L", (4096, 4096), 0)
        pixel_mask_v2.putpixel((240, 160), 255)
        mask_v2 = self.ledger.save_canvas_mask(
            roi_id=roi["id"],
            expected_revision=1,
            definition=changed_definition,
            pixel_sha256=image_fingerprint(pixel_mask_v2),
            client_request_id="mask-request-2",
        )
        self.assertEqual(mask_v2["current_revision"], 2)
        self.assertEqual(mask_v2["version"]["parent_version_id"], mask_v1["version"]["id"])
        self.assertEqual(
            [item["revision"] for item in self.ledger.list_canvas_mask_versions(mask_v1["id"])],
            [2, 1],
        )

        spec = self.ledger.create_local_edit_spec(
            contract=self.local_contract(roi, mask_v1),
            client_request_id="local-edit-spec-1",
        )
        replayed_spec = self.ledger.create_local_edit_spec(
            contract=self.local_contract(roi, mask_v1),
            client_request_id="local-edit-spec-1",
        )
        self.assertFalse(spec["replayed"])
        self.assertTrue(replayed_spec["replayed"])
        self.assertEqual(spec["id"], replayed_spec["id"])
        self.assertEqual(spec["mask_version_id"], mask_v1["version"]["id"])
        self.assertNotEqual(spec["mask_version_id"], mask_v2["version"]["id"])
        self.assertEqual(self.ledger.get_local_edit_spec(spec["id"])["contract"], spec["contract"])

        connection = sqlite3.connect(self.db_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE canvas_mask_versions SET definition_json = '{}' WHERE id = ?",
                    (mask_v1["version"]["id"],),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM local_edit_specs WHERE id = ?",
                    (spec["id"],),
                )
        finally:
            connection.close()

    def test_stale_canvas_and_cross_roi_mask_are_rejected(self) -> None:
        with self.assertRaises(CanvasRevisionConflictError):
            self.ledger.create_canvas_roi(
                canvas_document_id="canvas:local-edit",
                expected_canvas_revision=0,
                source_layer_id="layer:source",
                coordinate_space="source-pixel",
                rect={"x": 64, "y": 80, "width": 1024, "height": 1200},
                purpose="inpaint",
                client_request_id="roi-stale",
            )
        with self.assertRaises(KeyError):
            self.ledger.create_canvas_roi(
                canvas_document_id="canvas:local-edit",
                expected_canvas_revision=1,
                source_layer_id="layer:missing",
                coordinate_space="source-pixel",
                rect={"x": 64, "y": 80, "width": 1024, "height": 1200},
                purpose="inpaint",
                client_request_id="roi-missing-layer",
            )

        first_roi = self.create_roi()
        second_roi = self.create_roi(request_id="roi-request-2", x=1200)
        mask = self.create_mask(first_roi["id"])
        contract = self.local_contract(second_roi, mask)
        contract["mask"]["roi_id"] = second_roi["id"]
        with self.assertRaisesRegex(ValueError, "same ROI"):
            self.ledger.create_local_edit_spec(
                contract=contract,
                client_request_id="local-edit-cross-roi",
            )

        references = self.ledger.asset_reference_summary(self.asset["id"])
        self.assertIn(self.canvas["version"]["id"], references["references"]["canvas_versions"])
        self.assertIn("canvas_versions", references["blockers"])

    def test_jobs_and_traces_bind_one_exact_local_edit_spec(self) -> None:
        roi = self.create_roi()
        mask = self.create_mask(roi["id"])
        spec = self.ledger.create_local_edit_spec(
            contract=self.local_contract(roi, mask),
            client_request_id="local-edit-job-spec-1",
        )
        job, created = self.ledger.create_job(
            "single",
            [self.asset["id"]],
            engine_key="local",
            parameters={"batch": 1},
            idempotency_key="local-edit-bound-job",
            command_id="command:existing-generate-single",
            canvas_document_id="canvas:local-edit",
            expected_canvas_revision=1,
            canvas_operation_id=spec["operation_id"],
            local_edit_spec_id=spec["id"],
        )
        self.assertTrue(created)
        self.assertEqual(job["snapshot"]["local_edit_spec_id"], spec["id"])
        self.assertEqual(
            job["snapshot"]["canvas_document_version_id"],
            spec["canvas_document_version_id"],
        )
        trace = self.ledger.record_execution_trace(
            "local-edit-bound-trace",
            job_id=job["id"],
            stage="local-edit.compose",
            status="completed",
        )
        self.assertEqual(trace["local_edit_spec_id"], spec["id"])

        replayed, replay_created = self.ledger.create_job(
            "single",
            [self.asset["id"]],
            engine_key="local",
            parameters={"batch": 1},
            idempotency_key="local-edit-bound-job",
            command_id="command:existing-generate-single",
            canvas_document_id="canvas:local-edit",
            expected_canvas_revision=1,
            canvas_operation_id=spec["operation_id"],
            local_edit_spec_id=spec["id"],
        )
        self.assertFalse(replay_created)
        self.assertEqual(replayed["id"], job["id"])

        with self.assertRaises(IdempotencyConflictError):
            self.ledger.create_job(
                "single",
                [self.asset["id"]],
                engine_key="local",
                parameters={"batch": 1},
                idempotency_key="local-edit-bound-job",
                command_id="command:existing-generate-single",
                canvas_document_id="canvas:local-edit",
                expected_canvas_revision=1,
                canvas_operation_id=spec["operation_id"],
            )

        with self.assertRaisesRegex(ValueError, "operation"):
            self.ledger.create_job(
                "single",
                [self.asset["id"]],
                engine_key="local",
                command_id="command:existing-generate-single",
                canvas_document_id="canvas:local-edit",
                expected_canvas_revision=1,
                canvas_operation_id="operation:wrong-local-edit",
                local_edit_spec_id=spec["id"],
            )

    def test_source_fingerprint_and_mask_definition_bounds_are_rejected(self) -> None:
        roi = self.create_roi()
        mask = self.create_mask(roi["id"])
        contract = self.local_contract(roi, mask)
        contract["source_sha256"] = "B" * 64
        with self.assertRaisesRegex(ValueError, "source fingerprint"):
            self.ledger.create_local_edit_spec(
                contract=contract,
                client_request_id="local-edit-wrong-source-sha",
            )

        unknown_field = mask_definition()
        unknown_field["embedded_base64"] = "forbidden"
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            self.ledger.save_canvas_mask(
                roi_id=roi["id"],
                expected_revision=1,
                definition=unknown_field,
                pixel_sha256=mask["version"]["pixel_sha256"],
                client_request_id="mask-unknown-field",
            )

        outside = mask_definition()
        outside["strokes"][0]["points"][0]["x"] = 4096
        with self.assertRaisesRegex(ValueError, "out of range"):
            self.ledger.save_canvas_mask(
                roi_id=roi["id"],
                expected_revision=1,
                definition=outside,
                pixel_sha256=mask["version"]["pixel_sha256"],
                client_request_id="mask-outside-bounds",
            )


class LocalEditMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "atelier.sqlite3"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_v5_upgrade_creates_v6_and_a_queryable_v5_backup(self) -> None:
        create_v5_database(self.db_path)
        ledger = AtelierLedger(self.db_path)
        self.assertEqual(ledger.stats()["schema_version"], 6)
        self.assertIsNotNone(ledger.last_migration_backup)
        backup = ledger.last_migration_backup
        assert backup is not None
        connection = sqlite3.connect(backup)
        try:
            version = int(connection.execute(
                "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
            ).fetchone()[0])
            self.assertEqual(version, 5)
        finally:
            connection.close()

    def test_incomplete_v6_with_v5_marker_is_refused_without_mutation(self) -> None:
        create_v5_database(self.db_path)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("CREATE TABLE canvas_rois(id TEXT PRIMARY KEY)")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(PartialSchemaError, "canvas_rois"):
            AtelierLedger(self.db_path)

        connection = sqlite3.connect(self.db_path)
        try:
            version = int(connection.execute(
                "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
            ).fetchone()[0])
            self.assertEqual(version, 5)
            columns = [
                row[1] for row in connection.execute("PRAGMA table_info(canvas_rois)")
            ]
            self.assertEqual(columns, ["id"])
        finally:
            connection.close()

    def test_complete_v6_with_stale_v5_marker_is_repaired_without_reapplying(self) -> None:
        create_v5_database(self.db_path)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            AtelierLedger._migrate_v5_to_v6(connection)
            connection.commit()
        finally:
            connection.close()

        repaired = AtelierLedger(self.db_path)
        self.assertEqual(repaired.stats()["schema_version"], 6)
        self.assertEqual(
            repaired.last_schema_repair,
            "recovered complete v6 schema with stale v5 metadata",
        )
        self.assertIsNotNone(repaired.last_migration_backup)

    def test_v6_migration_failure_rolls_back_all_local_edit_objects(self) -> None:
        create_v5_database(self.db_path)

        class FailingV6Ledger(AtelierLedger):
            @staticmethod
            def _migrate_v5_to_v6(connection: sqlite3.Connection) -> None:
                connection.execute("CREATE TABLE should_rollback_v6(id TEXT PRIMARY KEY)")
                raise RuntimeError("injected v6 migration failure")

        with self.assertRaises(LedgerSchemaError):
            FailingV6Ledger(self.db_path)

        connection = sqlite3.connect(self.db_path)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertNotIn("should_rollback_v6", tables)
            self.assertNotIn("canvas_masks", tables)
            version = int(connection.execute(
                "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
            ).fetchone()[0])
            self.assertEqual(version, 5)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
