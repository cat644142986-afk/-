from __future__ import annotations

import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image


# Importing server initializes its local singleton state. Force that import into
# a process-lifetime temporary directory before any application module is read.
MODULE_DATA_DIR = tempfile.TemporaryDirectory()
os.environ["PRODUCT_ATELIER_DATA_DIR"] = MODULE_DATA_DIR.name
os.environ["PRODUCT_ATELIER_LEGACY_CONFIG"] = str(
    Path(MODULE_DATA_DIR.name) / "no-legacy-config.json"
)
os.environ["PRODUCT_ATELIER_KNOWLEDGE_BASE"] = str(
    Path(MODULE_DATA_DIR.name) / "no-knowledge-vault"
)

from python import server  # noqa: E402
from python.asset_store import AssetStore  # noqa: E402
from python.atelier_ledger import AtelierLedger, SCHEMA_VERSION  # noqa: E402


def png_bytes(color: tuple[int, int, int] = (220, 100, 40)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (24, 18), color).save(buffer, "PNG")
    return buffer.getvalue()


class AssetApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output_dir = self.root / "output"
        self.output_dir.mkdir()
        self.asset_dir = self.root / "assets"
        self.ledger = AtelierLedger(self.root / "atelier.sqlite3")
        self.store = AssetStore(self.asset_dir, self.ledger)
        self.original_globals = (
            server.LEDGER,
            server.ASSET_STORE,
            server.ASSET_DIR,
            server.OUTPUT_DIR,
        )
        server.LEDGER = self.ledger
        server.ASSET_STORE = self.store
        server.ASSET_DIR = self.asset_dir
        server.OUTPUT_DIR = self.output_dir
        self.client = TestClient(server.app)

    def tearDown(self) -> None:
        self.client.close()
        (
            server.LEDGER,
            server.ASSET_STORE,
            server.ASSET_DIR,
            server.OUTPUT_DIR,
        ) = self.original_globals
        self.temp_dir.cleanup()

    def test_health_exposes_sidecar_contract_and_ledger_schema(self) -> None:
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(
            payload["service"]["contract_version"],
            server.SIDECAR_CONTRACT_VERSION,
        )
        self.assertFalse(payload["service"]["packaged"])
        self.assertEqual(payload["ledger"]["schema_version"], SCHEMA_VERSION)
        self.assertIsNone(payload["ledger"]["startup_repair"])

    def test_cors_rejects_untrusted_web_origin_for_reads_and_mutations(self) -> None:
        attacker_headers = {"Origin": "https://attacker.example"}

        listing = self.client.get("/api/assets", headers=attacker_headers)
        self.assertEqual(listing.status_code, 403, listing.text)
        self.assertEqual(listing.json()["detail"]["code"], "UNTRUSTED_ORIGIN")
        self.assertNotIn("access-control-allow-origin", listing.headers)

        # Multipart uploads are simple browser requests and can bypass CORS
        # preflight, so the origin guard must stop them before endpoint code.
        imported = self.client.post(
            "/api/assets/import",
            headers=attacker_headers,
            files={"file": ("blocked.png", png_bytes(), "image/png")},
        )
        self.assertEqual(imported.status_code, 403, imported.text)
        self.assertEqual(self.ledger.stats()["counts"]["workspace_assets"], 0)

        submitted = self.client.post(
            "/api/jobs",
            headers=attacker_headers,
            json={
                "mode": "single",
                "source_asset_ids": ["ast_not_allowed"],
                "parameters": {"model": "must-not-run"},
                "client_request_id": "attacker-request",
            },
        )
        self.assertEqual(submitted.status_code, 403, submitted.text)
        self.assertEqual(self.ledger.stats()["counts"]["jobs"], 0)

    def test_cors_allows_packaged_tauri_and_checked_in_dev_origins(self) -> None:
        for origin in (
            "tauri://localhost",
            "http://tauri.localhost",
            "https://tauri.localhost",
            "http://localhost:1420",
            "http://127.0.0.1:1420",
        ):
            with self.subTest(origin=origin):
                listing = self.client.get("/api/assets", headers={"Origin": origin})
                self.assertEqual(listing.status_code, 200, listing.text)
                self.assertEqual(
                    listing.headers.get("access-control-allow-origin"),
                    origin,
                )

        preflight = self.client.options(
            "/api/jobs",
            headers={
                "Origin": "http://localhost:1420",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        self.assertEqual(preflight.status_code, 200, preflight.text)
        self.assertEqual(
            preflight.headers.get("access-control-allow-origin"),
            "http://localhost:1420",
        )

        # The native Rust sidecar probe does not send an Origin header.
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200, health.text)

    def test_import_list_metadata_content_thumbnail_duplicate_and_restart(self) -> None:
        data = png_bytes()
        response = self.client.post(
            "/api/assets/import",
            files={"file": ("product.png", data, "image/png")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        imported = response.json()
        self.assertNotIn("path", imported)
        self.assertNotIn("blob", imported)
        self.assertEqual(imported["mime"], "image/png")
        self.assertEqual(imported["width"], 24)
        self.assertEqual(imported["height"], 18)

        duplicate = self.client.post(
            "/api/assets/import",
            files={"file": ("same-content.png", data, "image/png")},
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.json()["id"], imported["id"])

        listing = self.client.get("/api/assets").json()
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["assets"][0]["id"], imported["id"])
        metadata = self.client.get(f"/api/assets/{imported['id']}")
        self.assertEqual(metadata.status_code, 200)
        self.assertEqual(metadata.json()["sha256"], imported["sha256"])

        content = self.client.get(imported["content_url"])
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content.content, data)
        thumbnail = self.client.get(imported["thumbnail_url"])
        self.assertEqual(thumbnail.status_code, 200)
        self.assertEqual(thumbnail.headers["content-type"], "image/jpeg")
        with Image.open(io.BytesIO(thumbnail.content)) as image:
            self.assertLessEqual(max(image.size), 512)

        server.LEDGER = AtelierLedger(self.root / "atelier.sqlite3")
        server.ASSET_STORE = AssetStore(self.asset_dir, server.LEDGER)
        restarted_listing = self.client.get("/api/assets").json()
        self.assertEqual(restarted_listing["count"], 1)
        self.assertEqual(restarted_listing["assets"][0]["id"], imported["id"])

    def test_batch_import_twenty_images(self) -> None:
        files = [
            ("files", (f"product-{index}.png", png_bytes((index, 80, 160)), "image/png"))
            for index in range(20)
        ]
        response = self.client.post("/api/assets/import-batch", files=files)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["count"], 20)
        self.assertEqual(body["errors"], [])
        self.assertEqual(self.client.get("/api/assets").json()["count"], 20)

    def test_structured_validation_errors_do_not_create_assets(self) -> None:
        invalid = self.client.post(
            "/api/assets/import",
            files={"file": ("broken.png", b"not an image", "image/png")},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["detail"]["code"], "INVALID_IMAGE")

        spoofed = self.client.post(
            "/api/assets/import",
            files={"file": ("spoofed.jpg", png_bytes(), "image/jpeg")},
        )
        self.assertEqual(spoofed.status_code, 415)
        self.assertEqual(spoofed.json()["detail"]["code"], "EXTENSION_MISMATCH")
        self.assertEqual(self.client.get("/api/assets").json()["count"], 0)

    def test_asset_content_and_legacy_thumbnail_reject_paths_outside_whitelists(self) -> None:
        data = png_bytes()
        imported = self.client.post(
            "/api/assets/import",
            files={"file": ("safe.png", data, "image/png")},
        ).json()
        outside = self.root / "outside.png"
        outside.write_bytes(data)
        connection = sqlite3.connect(self.root / "atelier.sqlite3")
        try:
            blob_id = connection.execute(
                "SELECT blob_id FROM assets WHERE id = ?", (imported["id"],)
            ).fetchone()[0]
            connection.execute(
                "UPDATE asset_blobs SET storage_path = ? WHERE id = ?",
                (str(outside), blob_id),
            )
            connection.commit()
        finally:
            connection.close()

        denied_content = self.client.get(imported["content_url"])
        self.assertEqual(denied_content.status_code, 403)
        self.assertEqual(denied_content.json()["detail"]["code"], "ASSET_ACCESS_DENIED")

        prefix_attack_dir = self.root / "output_evil"
        prefix_attack_dir.mkdir()
        prefix_attack = prefix_attack_dir / "image.png"
        prefix_attack.write_bytes(data)
        denied_legacy = self.client.get("/api/thumbnail", params={"path": str(prefix_attack)})
        self.assertEqual(denied_legacy.status_code, 403)

        missing_outside = self.client.get(
            "/api/thumbnail", params={"path": str(self.root / "elsewhere" / "missing.png")}
        )
        self.assertEqual(missing_outside.status_code, 403)

        allowed_output = self.output_dir / "image.png"
        allowed_output.write_bytes(data)
        allowed_legacy = self.client.get("/api/thumbnail", params={"path": str(allowed_output)})
        self.assertEqual(allowed_legacy.status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
