from __future__ import annotations

import io
import os
import sqlite3
import tempfile
import time
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
            server.CONFIG_PATH,
            server._RUNTIME_OUTPUT_ROOT,
        )
        server.LEDGER = self.ledger
        server.ASSET_STORE = self.store
        server.ASSET_DIR = self.asset_dir
        server.OUTPUT_DIR = self.output_dir
        server.CONFIG_PATH = self.root / "config.json"
        server._RUNTIME_OUTPUT_ROOT = self.output_dir
        server.save_config({
            "output_root": str(self.output_dir),
            "known_output_roots": [str(self.output_dir)],
        })
        self.client = TestClient(server.app)

    def tearDown(self) -> None:
        self.client.close()
        (
            server.LEDGER,
            server.ASSET_STORE,
            server.ASSET_DIR,
            server.OUTPUT_DIR,
            server.CONFIG_PATH,
            server._RUNTIME_OUTPUT_ROOT,
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

    def test_output_root_settings_reject_program_and_unavailable_paths(self) -> None:
        program_root = Path(server.__file__).resolve().parents[1]
        protected = self.client.post(
            "/api/settings",
            json={"output_root": str(program_root)},
        )
        self.assertEqual(protected.status_code, 400, protected.text)
        self.assertEqual(protected.json()["detail"]["code"], "OUTPUT_ROOT_PROTECTED")

        unavailable = self.client.post(
            "/api/settings",
            json={"output_root": str(self.root / "missing-delivery")},
        )
        self.assertEqual(unavailable.status_code, 400, unavailable.text)
        self.assertEqual(unavailable.json()["detail"]["code"], "OUTPUT_ROOT_UNAVAILABLE")

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

        for path, method in (
            ("/api/jobs", "POST"),
            ("/api/workspaces/single/draft", "PUT"),
            ("/api/collections/product/assets/ast_example", "DELETE"),
        ):
            with self.subTest(method=method):
                preflight = self.client.options(
                    path,
                    headers={
                        "Origin": "http://localhost:1420",
                        "Access-Control-Request-Method": method,
                        "Access-Control-Request-Headers": "content-type",
                    },
                )
                self.assertEqual(preflight.status_code, 200, preflight.text)
                self.assertEqual(
                    preflight.headers.get("access-control-allow-origin"),
                    "http://localhost:1420",
                )
                self.assertIn(
                    method,
                    preflight.headers.get("access-control-allow-methods", ""),
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

    def test_folder_source_import_is_flat_durable_and_plans_delivery_inside_source(self) -> None:
        source_folder = self.root / "待处理商品"
        source_folder.mkdir()
        (source_folder / "商品甲.png").write_bytes(png_bytes((220, 80, 40)))
        (source_folder / "商品乙.png").write_bytes(png_bytes((40, 120, 210)))
        (source_folder / "说明.txt").write_text("not an image", encoding="utf-8")
        nested = source_folder / "子目录"
        nested.mkdir()
        (nested / "不递归.png").write_bytes(png_bytes((30, 30, 30)))

        response = self.client.post(
            "/api/folder-sources/import",
            json={"folder_path": f'"{source_folder}"'},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        batch = payload["folder_batch"]
        self.assertEqual(payload["count"], 2)
        self.assertEqual(batch["detected_count"], 2)
        self.assertEqual(batch["imported_count"], 2)
        self.assertEqual(Path(batch["source_folder"]), source_folder.resolve())
        delivery = Path(batch["delivery_root"])
        self.assertEqual(delivery.parent, source_folder.resolve())
        self.assertTrue(delivery.name.startswith(server.FOLDER_DELIVERY_PREFIX))
        self.assertFalse(delivery.exists(), "delivery folder is created by completed jobs, not scanning")
        self.assertEqual(set(batch["source_names"].values()), {"商品甲.png", "商品乙.png"})

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

    def test_scoped_assets_trash_restore_and_duplicate_membership(self) -> None:
        data = png_bytes((90, 140, 30))
        imported = self.client.post(
            "/api/assets/import?collection=cutout",
            files={"file": ("cutout.png", data, "image/png")},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        asset_id = imported.json()["id"]

        self.assertEqual(
            self.client.get("/api/collections/product/assets").json()["total"], 0
        )
        cutout = self.client.get("/api/collections/cutout/assets").json()
        self.assertEqual(cutout["total"], 1)
        self.assertEqual(cutout["assets"][0]["id"], asset_id)

        duplicate = self.client.post(
            "/api/assets/import?collection=group",
            files={"file": ("same.png", data, "image/png")},
        )
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertEqual(duplicate.json()["id"], asset_id)
        self.assertEqual(
            self.client.get("/api/collections/group/assets").json()["total"], 1
        )

        removed = self.client.delete(f"/api/collections/cutout/assets/{asset_id}")
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json()["asset"]["membership"]["status"], "trashed")
        self.assertEqual(
            self.client.get("/api/collections/cutout/assets").json()["total"], 0
        )
        trash = self.client.get("/api/trash?collection=cutout").json()
        self.assertEqual(trash["count"], 1)
        self.assertEqual(trash["collections"]["cutout"][0]["id"], asset_id)

        restored = self.client.post(
            f"/api/collections/cutout/assets/{asset_id}/restore"
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["asset"]["membership"]["status"], "active")

    def test_collection_order_and_guarded_permanent_purge(self) -> None:
        first = self.client.post(
            "/api/assets/import",
            files={"file": ("first.png", png_bytes((10, 20, 30)), "image/png")},
        ).json()
        second = self.client.post(
            "/api/assets/import",
            files={"file": ("second.png", png_bytes((40, 50, 60)), "image/png")},
        ).json()
        reordered = self.client.put(
            "/api/collections/product/order",
            json={"asset_ids": [second["id"], first["id"]]},
        )
        self.assertEqual(reordered.status_code, 200, reordered.text)
        self.assertEqual(
            [asset["id"] for asset in reordered.json()["assets"]],
            [second["id"], first["id"]],
        )
        incomplete_order = self.client.put(
            "/api/collections/product/order",
            json={"asset_ids": [first["id"]]},
        )
        self.assertEqual(incomplete_order.status_code, 400, incomplete_order.text)

        active_refs = self.client.get(
            f"/api/assets/{first['id']}/references"
        ).json()
        self.assertFalse(active_refs["purge_allowed"])
        self.assertIn("active_membership", active_refs["blockers"])
        self.client.delete(f"/api/collections/product/assets/{first['id']}")
        retention_block = self.client.delete(
            f"/api/trash/assets/{first['id']}",
            params={"confirm_asset_id": first["id"]},
        )
        self.assertEqual(retention_block.status_code, 409, retention_block.text)
        self.assertIn(
            "retention_period",
            retention_block.json()["detail"]["summary"]["blockers"],
        )

        original_retention = server.TRASH_RETENTION_DAYS
        server.TRASH_RETENTION_DAYS = 0
        try:
            mismatch = self.client.delete(
                f"/api/trash/assets/{first['id']}",
                params={"confirm_asset_id": "wrong"},
            )
            self.assertEqual(mismatch.status_code, 400, mismatch.text)
            source_path = Path(
                self.ledger.get_workspace_asset(first["id"])["blob"]["storage_path"]
            )
            purged = self.client.delete(
                f"/api/trash/assets/{first['id']}",
                params={"confirm_asset_id": first["id"]},
            )
            self.assertEqual(purged.status_code, 200, purged.text)
            self.assertTrue(purged.json()["purged"])
            self.assertTrue(purged.json()["file_deleted"])
            self.assertFalse(source_path.exists())
            self.assertEqual(self.client.get(f"/api/assets/{first['id']}").status_code, 404)

            self.ledger.create_job(
                "single",
                [second["id"]],
                engine_key="mock-cloud",
                idempotency_key="protected-purge-job",
            )
            self.client.delete(f"/api/collections/product/assets/{second['id']}")
            protected = self.client.delete(
                f"/api/trash/assets/{second['id']}",
                params={"confirm_asset_id": second["id"]},
            )
            self.assertEqual(protected.status_code, 409, protected.text)
            protected_summary = protected.json()["detail"]["summary"]
            self.assertIn("jobs", protected_summary["blockers"])
            self.assertIn("job_snapshots", protected_summary["blockers"])
        finally:
            server.TRASH_RETENTION_DAYS = original_retention

    def test_scoped_asset_pagination_handles_two_hundred_rows(self) -> None:
        for index in range(200):
            self.ledger.register_workspace_asset(
                sha256=f"{index + 1:064x}",
                storage_path=str(self.asset_dir / f"{index + 1:064x}.png"),
                mime="image/png",
                size_bytes=64,
                width=8,
                height=8,
                name=f"asset-{index + 1:03d}.png",
            )
        started = time.perf_counter()
        page = self.client.get(
            "/api/collections/product/assets", params={"limit": 50, "offset": 75}
        )
        full = self.client.get(
            "/api/collections/product/assets", params={"limit": 200, "offset": 0}
        )
        elapsed = time.perf_counter() - started
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(page.json()["count"], 50)
        self.assertEqual(page.json()["total"], 200)
        self.assertEqual(page.json()["offset"], 75)
        self.assertEqual(full.status_code, 200, full.text)
        self.assertEqual(full.json()["count"], 200)
        self.assertLess(elapsed, 2.0)

    def test_workspace_drafts_share_assets_without_sharing_state(self) -> None:
        product = self.client.post(
            "/api/assets/import",
            files={"file": ("product.png", png_bytes(), "image/png")},
        ).json()
        cutout = self.client.post(
            "/api/assets/import?collection=cutout",
            files={"file": ("cutout.png", png_bytes((20, 50, 180)), "image/png")},
        ).json()

        single = self.client.get("/api/workspaces/single").json()
        multi = self.client.get("/api/workspaces/multi-file").json()
        self.assertEqual(single["collection"], "product")
        self.assertEqual(multi["collection"], "product")
        self.assertEqual([asset["id"] for asset in single["assets"]], [product["id"]])
        self.assertEqual(single["draft"]["selected_asset_ids"], [])
        self.assertEqual(multi["draft"]["selected_asset_ids"], [])

        saved = self.client.put(
            "/api/workspaces/single/draft",
            json={
                "expected_revision": 1,
                "selected_asset_ids": [product["id"]],
                "brief": {"goal": "保留包装文字"},
                "parameters": {"batch": 1},
                "ui_state": {"zoom": 1.2},
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["draft"]["revision"], 2)
        self.assertEqual(
            self.client.get("/api/workspaces/multi-file").json()["draft"]["revision"], 1
        )

        stale = self.client.put(
            "/api/workspaces/single/draft",
            json={"expected_revision": 1, "selected_asset_ids": [product["id"]]},
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["code"], "DRAFT_REVISION_CONFLICT")
        self.assertEqual(stale.json()["detail"]["current"]["revision"], 2)

        cross_domain = self.client.put(
            "/api/workspaces/single/draft",
            json={"expected_revision": 2, "selected_asset_ids": [cutout["id"]]},
        )
        self.assertEqual(cross_domain.status_code, 400, cross_domain.text)
        self.assertEqual(cross_domain.json()["detail"]["code"], "INVALID_DRAFT")

    def test_trace_and_review_writes_are_idempotent_and_lineage_checked(self) -> None:
        source = self.client.post(
            "/api/assets/import",
            files={"file": ("source.png", png_bytes(), "image/png")},
        ).json()
        job, _ = self.ledger.create_job(
            "single",
            [source["id"]],
            engine_key="mock-cloud",
            parameters={"model": "offline-model"},
            idempotency_key="workspace-api-job",
        )
        item_id = job["items"][0]["id"]
        generation_id = job["items"][0]["generation_id"]
        self.ledger.claim_job_item(item_id)
        result_path = self.output_dir / "result.png"
        result_path.write_bytes(png_bytes((240, 180, 20)))
        result_asset_id = self.ledger.commit_generation_results(
            generation_id,
            source["id"],
            [{"path": str(result_path), "name": "result.png", "role": "result_main"}],
            job_item_id=item_id,
        )[0]

        trace_payload = {
            "client_request_id": "trace-request-1",
            "stage": "prompt.compile",
            "status": "completed",
            "job_item_id": item_id,
            "generation_id": generation_id,
            "user_input": {"brief": "只保留主体"},
            "applied_knowledge": ["K-1"],
            "ignored_fields": [],
            "model": "offline-model",
        }
        first_trace = self.client.post(
            f"/api/jobs/{job['id']}/traces", json=trace_payload
        )
        replay_trace = self.client.post(
            f"/api/jobs/{job['id']}/traces", json=trace_payload
        )
        self.assertEqual(first_trace.status_code, 200, first_trace.text)
        self.assertEqual(replay_trace.status_code, 200, replay_trace.text)
        self.assertEqual(first_trace.json()["trace"]["id"], replay_trace.json()["trace"]["id"])
        conflict_payload = dict(trace_payload)
        conflict_payload["status"] = "failed"
        conflict = self.client.post(
            f"/api/jobs/{job['id']}/traces", json=conflict_payload
        )
        self.assertEqual(conflict.status_code, 409, conflict.text)

        review_payload = {
            "client_request_id": "review-request-1",
            "result_asset_id": result_asset_id,
            "generation_id": generation_id,
            "decision": "adjust",
            "reason_codes": ["主体"],
            "note": "只保留两个汉堡",
            "learning_action": "record",
        }
        first_review = self.client.post(
            f"/api/jobs/{job['id']}/reviews", json=review_payload
        )
        replay_review = self.client.post(
            f"/api/jobs/{job['id']}/reviews", json=review_payload
        )
        self.assertEqual(first_review.status_code, 200, first_review.text)
        self.assertEqual(replay_review.status_code, 200, replay_review.text)
        self.assertEqual(
            first_review.json()["review"]["id"], replay_review.json()["review"]["id"]
        )
        self.assertEqual(
            self.client.get(f"/api/jobs/{job['id']}/traces").json()["count"], 1
        )
        self.assertEqual(
            self.client.get(f"/api/jobs/{job['id']}/reviews").json()["count"], 1
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
