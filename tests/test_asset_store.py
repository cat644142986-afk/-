from __future__ import annotations

import io
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from PIL import Image

from python.asset_store import AssetAccessError, AssetStore, AssetValidationError
from python.atelier_ledger import AtelierLedger


def image_bytes(
    image_format: str = "PNG",
    *,
    size: tuple[int, int] = (24, 18),
    color: tuple[int, int, int] = (210, 90, 40),
) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, image_format)
    return buffer.getvalue()


class AssetStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "atelier.sqlite3"
        self.asset_root = self.root / "assets"
        self.ledger = AtelierLedger(self.db_path)
        self.store = AssetStore(self.asset_root, self.ledger)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def physical_files(self) -> list[Path]:
        return sorted(
            path for path in self.asset_root.rglob("*")
            if path.is_file() and not path.name.startswith(".")
        )

    def test_import_twenty_images_persists_database_and_content_addressed_files(self) -> None:
        imported = []
        for index in range(20):
            imported.append(self.store.import_bytes(
                image_bytes(color=(index, 80, 160)),
                f"product-{index:02d}.png",
            ))

        self.assertEqual(len(imported), 20)
        self.assertEqual(len({asset["id"] for asset in imported}), 20)
        self.assertEqual(len(self.ledger.list_workspace_assets()), 20)
        self.assertEqual(len(self.physical_files()), 20)
        for asset in imported:
            path = Path(asset["path"])
            self.assertTrue(path.is_file())
            self.assertNotEqual(asset["path"], "")
            self.assertEqual(path.parent.name, asset["sha256"][:2])
            self.assertEqual(path.stem, asset["sha256"])
            self.assertEqual(asset["blob_id"], asset["blob"]["id"])

    def test_duplicate_imports_share_one_blob_one_asset_and_one_physical_file(self) -> None:
        data = image_bytes()
        assets = [self.store.import_bytes(data, "same.png") for _ in range(3)]

        self.assertEqual({asset["id"] for asset in assets}, {assets[0]["id"]})
        self.assertEqual(len(self.physical_files()), 1)
        self.assertEqual(len(self.ledger.list_workspace_assets()), 1)
        self.assertEqual(self.ledger.stats()["counts"]["asset_blobs"], 1)
        self.assertEqual(self.ledger.stats()["counts"]["workspace_assets"], 1)
        self.assertEqual(self.ledger.stats()["counts"]["sessions"], 0)
        self.assertEqual(self.ledger.list_sessions(), [])

    def test_concurrent_duplicate_imports_are_idempotent(self) -> None:
        data = image_bytes(size=(64, 64))
        with ThreadPoolExecutor(max_workers=6) as pool:
            assets = list(pool.map(
                lambda _: self.store.import_bytes(data, "concurrent.png"),
                range(12),
            ))

        self.assertEqual(len({asset["id"] for asset in assets}), 1)
        self.assertEqual(len(self.physical_files()), 1)
        self.assertEqual(self.ledger.stats()["counts"]["asset_blobs"], 1)
        self.assertEqual(self.store._resolved_root, self.asset_root.resolve(strict=True))

    @unittest.skipUnless(os.name == "nt", "Windows namespace behavior")
    def test_windows_extended_namespace_has_the_same_containment_identity(self) -> None:
        regular = self.store._resolved_root
        namespaced = Path("\\\\?\\" + str(regular))

        self.assertEqual(
            self.store._comparison_key(namespaced),
            self.store._comparison_root,
        )
        self.assertTrue(self.store._is_within_root(namespaced, strict=True))

    def test_assets_survive_reopening_ledger_and_store(self) -> None:
        imported = self.store.import_bytes(image_bytes("JPEG"), "restart.jpeg")

        reopened_ledger = AtelierLedger(self.db_path)
        reopened_store = AssetStore(self.asset_root, reopened_ledger)
        recovered = reopened_ledger.list_workspace_assets()

        self.assertEqual([asset["id"] for asset in recovered], [imported["id"]])
        asset, path = reopened_store.resolve_asset_path(imported["id"])
        self.assertEqual(asset["sha256"], imported["sha256"])
        self.assertTrue(path.is_file())
        self.assertGreater(len(reopened_store.thumbnail_bytes(imported["id"])), 0)

    def test_invalid_spoofed_oversized_and_overpixel_inputs_leave_no_dirty_data(self) -> None:
        cases = [
            (self.store, b"not an image", "broken.png", "INVALID_IMAGE"),
            (self.store, image_bytes("PNG"), "spoofed.jpg", "EXTENSION_MISMATCH"),
            (self.store, b"", "empty.png", "EMPTY_FILE"),
            (AssetStore(self.asset_root, self.ledger, max_file_bytes=16), image_bytes(), "large.png", "FILE_TOO_LARGE"),
            (AssetStore(self.asset_root, self.ledger, max_pixels=10), image_bytes(size=(4, 4)), "pixels.png", "PIXEL_LIMIT_EXCEEDED"),
        ]
        for store, data, name, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(AssetValidationError) as caught:
                    store.import_bytes(data, name)
                self.assertEqual(caught.exception.code, code)

        self.assertEqual(self.ledger.list_workspace_assets(), [])
        self.assertEqual(self.ledger.stats()["counts"]["asset_blobs"], 0)
        self.assertEqual(self.physical_files(), [])
        self.assertEqual(list(self.asset_root.rglob("*.tmp")), [])

    def test_atomic_rename_failure_leaves_no_file_or_database_row(self) -> None:
        with mock.patch("python.asset_store.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.store.import_bytes(image_bytes(), "disk.png")

        self.assertEqual(self.ledger.list_workspace_assets(), [])
        self.assertEqual(self.ledger.stats()["counts"]["asset_blobs"], 0)
        self.assertEqual(self.physical_files(), [])
        self.assertEqual(list(self.asset_root.rglob("*.tmp")), [])

    def test_database_failure_removes_newly_committed_physical_file(self) -> None:
        with mock.patch.object(
            self.ledger,
            "register_workspace_asset",
            side_effect=sqlite3.OperationalError("database failure"),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.import_bytes(image_bytes(), "database.png")

        self.assertEqual(self.ledger.list_workspace_assets(), [])
        self.assertEqual(self.ledger.stats()["counts"]["asset_blobs"], 0)
        self.assertEqual(self.physical_files(), [])

    def test_total_database_failure_still_removes_owned_physical_file(self) -> None:
        with mock.patch.object(
            self.ledger,
            "register_workspace_asset",
            side_effect=sqlite3.OperationalError("database failure"),
        ), mock.patch.object(
            self.ledger,
            "has_asset_blob",
            side_effect=sqlite3.OperationalError("database unavailable"),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.import_bytes(image_bytes(), "database-down.png")

        self.assertEqual(self.physical_files(), [])

    def test_tampered_storage_path_outside_root_is_rejected(self) -> None:
        data = image_bytes()
        imported = self.store.import_bytes(data, "safe.png")
        outside = self.root / "outside.png"
        outside.write_bytes(data)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE asset_blobs SET storage_path = ? WHERE id = ?",
                (str(outside), imported["blob_id"]),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(AssetAccessError) as caught:
            self.store.resolve_asset_path(imported["id"])
        self.assertEqual(caught.exception.code, "ASSET_ACCESS_DENIED")

    def test_missing_path_outside_root_is_access_denied_before_not_found(self) -> None:
        imported = self.store.import_bytes(image_bytes(), "safe.png")
        outside = self.root / "outside" / "missing.png"
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE asset_blobs SET storage_path = ? WHERE id = ?",
                (str(outside), imported["blob_id"]),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(AssetAccessError) as caught:
            self.store.resolve_asset_path(imported["id"])
        self.assertEqual(caught.exception.code, "ASSET_ACCESS_DENIED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
