from __future__ import annotations

import copy
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from python.asset_store import AssetStore
from python.atelier_ledger import (
    AtelierLedger,
    IdempotencyConflictError,
    LedgerSchemaError,
    PartialSchemaError,
    ProductProfileRevisionConflictError,
    SCHEMA_VERSION,
)


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), (230, 110, 70)).save(buffer, "PNG")
    return buffer.getvalue()


def product_profile(reference_id: str, *, revision: int = 0) -> dict:
    return {
        "id": "profile:test-transparent-bottle",
        "schema_version": 1,
        "sku": "TEST-BOTTLE-500",
        "name": "测试透明瓶 500 ml",
        "revision": revision,
        "category": "beverage",
        "specification": {
            "display": "500 ml × 1 瓶",
            "net_content": "500 ml",
            "unit_count": 1,
            "attributes": [{"key": "flavor", "value": "citrus"}],
        },
        "components": [
            {
                "id": "component:bottle",
                "name": "透明瓶身",
                "role": "core",
                "policy": "must_preserve",
                "quantity": 1,
            },
            {
                "id": "component:label",
                "name": "包装标签",
                "role": "label",
                "policy": "forbid_modify",
                "quantity": 1,
            },
        ],
        "materials": [
            {
                "component_id": "component:bottle",
                "material": "PET",
                "finish": "glossy",
                "transparent": True,
            }
        ],
        "brand_colors": [{"name": "品牌珊瑚色", "value": "#E86E4B"}],
        "packaging_texts": [
            {
                "id": "text:brand",
                "component_id": "component:label",
                "content": "TEST BRAND",
                "policy": "exact_preserve",
            }
        ],
        "logos": [
            {
                "id": "logo:primary",
                "component_id": "component:label",
                "name": "主 Logo",
                "policy": "exact_preserve",
            }
        ],
        "platform_specs": [
            {
                "platform": "marketplace",
                "role": "main-image",
                "pixel_width": 1024,
                "pixel_height": 1024,
                "format": "png",
                "safe_area_percent": 5,
            }
        ],
        "selection_mode": "full_composition",
        "approved_reference_ids": [reference_id],
        "created_at": "2026-09-02T00:00:00Z",
        "updated_at": "2026-09-02T00:00:00Z",
    }


def create_v4_database(path: Path) -> None:
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
        connection.commit()
    finally:
        connection.close()


class ProductProfileLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "atelier.sqlite3"
        self.ledger = AtelierLedger(self.db_path)
        self.asset_store = AssetStore(self.root / "assets", self.ledger)
        self.reference = self.asset_store.import_bytes(
            png_bytes(), "approved-reference.png"
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_current_schema_has_immutable_profile_versions_and_job_bindings(self) -> None:
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
                "product_profiles",
                "product_profile_versions",
                "product_profile_version_assets",
            }.issubset(tables))
            self.assertIn(
                "product_profile_version_id",
                {row[1] for row in connection.execute("PRAGMA table_info(job_snapshots)")},
            )
            self.assertIn(
                "product_profile_version_id",
                {row[1] for row in connection.execute("PRAGMA table_info(execution_traces)")},
            )
            triggers = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                )
            }
            self.assertTrue({
                "trg_product_profile_versions_no_update",
                "trg_product_profile_versions_no_delete",
                "trg_product_profile_assets_no_update",
                "trg_product_profile_assets_no_delete",
            }.issubset(triggers))
        finally:
            connection.close()

    def test_versions_are_immutable_replayable_and_optimistically_locked(self) -> None:
        initial = product_profile(self.reference["id"])
        first = self.ledger.save_product_profile(
            expected_revision=0,
            client_request_id="profile-save-1",
            profile=initial,
        )
        replay = self.ledger.save_product_profile(
            expected_revision=0,
            client_request_id="profile-save-1",
            profile=initial,
        )
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["version"]["id"], replay["version"]["id"])
        self.assertEqual(first["profile"]["revision"], 1)

        changed = copy.deepcopy(first["profile"])
        changed["name"] = "测试透明瓶 500 ml 新包装"
        second = self.ledger.save_product_profile(
            expected_revision=1,
            client_request_id="profile-save-2",
            profile=changed,
        )
        self.assertEqual(second["profile"]["revision"], 2)
        self.assertEqual(
            second["version"]["parent_version_id"], first["version"]["id"]
        )
        historical = self.ledger.get_product_profile_version(first["version"]["id"])
        self.assertEqual(historical["profile"]["name"], "测试透明瓶 500 ml")

        with self.assertRaises(ProductProfileRevisionConflictError):
            self.ledger.save_product_profile(
                expected_revision=1,
                client_request_id="profile-save-stale",
                profile=changed,
            )
        conflicting = copy.deepcopy(initial)
        conflicting["category"] = "different"
        with self.assertRaises(IdempotencyConflictError):
            self.ledger.save_product_profile(
                expected_revision=0,
                client_request_id="profile-save-1",
                profile=conflicting,
            )

        connection = sqlite3.connect(self.db_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE product_profile_versions SET profile_json = '{}' WHERE id = ?",
                    (first["version"]["id"],),
                )
        finally:
            connection.close()

    def test_approved_references_are_validated_and_protected_from_purge(self) -> None:
        missing = product_profile("ast_missing")
        with self.assertRaises(KeyError):
            self.ledger.save_product_profile(
                expected_revision=0,
                client_request_id="profile-missing-reference",
                profile=missing,
            )

        saved = self.ledger.save_product_profile(
            expected_revision=0,
            client_request_id="profile-reference-protection",
            profile=product_profile(self.reference["id"]),
        )
        summary = self.ledger.asset_reference_summary(
            self.reference["id"], retention_days=0
        )
        self.assertEqual(
            summary["references"]["product_profile_versions"],
            [saved["version"]["id"]],
        )
        self.assertIn("product_profile_versions", summary["blockers"])
        self.assertFalse(summary["purge_allowed"])

    def test_jobs_and_traces_bind_one_exact_profile_version(self) -> None:
        first = self.ledger.save_product_profile(
            expected_revision=0,
            client_request_id="profile-job-v1",
            profile=product_profile(self.reference["id"]),
        )
        job, created = self.ledger.create_job(
            "single",
            [self.reference["id"]],
            engine_key="mock-cloud",
            parameters={"batch": 1},
            idempotency_key="profile-bound-job",
            product_profile_id=first["id"],
            expected_product_profile_revision=1,
        )
        self.assertTrue(created)
        self.assertEqual(
            job["snapshot"]["product_profile_version_id"], first["version"]["id"]
        )
        trace = self.ledger.record_execution_trace(
            "profile-bound-trace",
            job_id=job["id"],
            stage="profile.constraints",
            status="completed",
        )
        self.assertEqual(
            trace["product_profile_version_id"], first["version"]["id"]
        )

        changed = copy.deepcopy(first["profile"])
        changed["selection_mode"] = "core_only"
        second = self.ledger.save_product_profile(
            expected_revision=1,
            client_request_id="profile-job-v2",
            profile=changed,
        )
        reopened = self.ledger.get_job(job["id"])
        self.assertEqual(
            reopened["snapshot"]["product_profile_version_id"], first["version"]["id"]
        )
        self.assertNotEqual(
            reopened["snapshot"]["product_profile_version_id"], second["version"]["id"]
        )
        with self.assertRaises(ProductProfileRevisionConflictError):
            self.ledger.create_job(
                "single",
                [self.reference["id"]],
                engine_key="mock-cloud",
                parameters={"batch": 1},
                product_profile_id=first["id"],
                expected_product_profile_revision=1,
            )

    def test_profile_list_returns_only_current_versions(self) -> None:
        saved = self.ledger.save_product_profile(
            expected_revision=0,
            client_request_id="profile-list",
            profile=product_profile(self.reference["id"]),
        )
        profiles = self.ledger.list_product_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["version"]["id"], saved["version"]["id"])
        self.assertNotIn("request_fingerprint", profiles[0]["version"])


class ProductProfileMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "atelier.sqlite3"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_v4_upgrade_creates_current_schema_and_a_queryable_v4_backup(self) -> None:
        create_v4_database(self.db_path)
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
            self.assertEqual(version, 4)
        finally:
            connection.close()

    def test_incomplete_v5_with_v4_marker_is_refused_without_mutation(self) -> None:
        AtelierLedger(self.db_path)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("DROP TRIGGER trg_product_profile_versions_no_update")
            connection.execute(
                "UPDATE ledger_meta SET value = '4' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(
            PartialSchemaError, "trg_product_profile_versions_no_update"
        ):
            AtelierLedger(self.db_path)

    def test_complete_v5_with_stale_v4_marker_is_repaired_safely(self) -> None:
        AtelierLedger(self.db_path)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE ledger_meta SET value = '4' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()
        repaired = AtelierLedger(self.db_path)
        self.assertEqual(repaired.stats()["schema_version"], 7)
        self.assertEqual(
            repaired.last_schema_repair,
            "recovered complete v5 schema with stale v4 metadata; "
            "recovered complete v6 schema with stale v5 metadata; "
            "recovered complete v7 schema with stale v6 metadata",
        )

    def test_v5_migration_failure_rolls_back_all_profile_objects(self) -> None:
        create_v4_database(self.db_path)

        class FailingV5Ledger(AtelierLedger):
            @staticmethod
            def _migrate_v4_to_v5(connection: sqlite3.Connection) -> None:
                connection.execute("CREATE TABLE should_rollback_v5(id TEXT PRIMARY KEY)")
                raise RuntimeError("injected v5 migration failure")

        with self.assertRaises(LedgerSchemaError):
            FailingV5Ledger(self.db_path)
        connection = sqlite3.connect(self.db_path)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertNotIn("should_rollback_v5", tables)
            self.assertNotIn("product_profiles", tables)
            version = int(connection.execute(
                "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
            ).fetchone()[0])
            self.assertEqual(version, 4)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
