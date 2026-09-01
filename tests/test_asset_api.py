from __future__ import annotations

import io
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

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
            server._RUNTIME_GROUNDING_RUNTIME_ROOT,
            server._RUNTIME_GROUNDING_MODEL_ROOT,
            server._GROUNDING_ADAPTER_KEY,
            server._GROUNDING_ADAPTER,
        )
        server.LEDGER = self.ledger
        server.ASSET_STORE = self.store
        server.ASSET_DIR = self.asset_dir
        server.OUTPUT_DIR = self.output_dir
        server.CONFIG_PATH = self.root / "config.json"
        server._RUNTIME_OUTPUT_ROOT = self.output_dir
        server._RUNTIME_GROUNDING_RUNTIME_ROOT = ""
        server._RUNTIME_GROUNDING_MODEL_ROOT = ""
        server._GROUNDING_ADAPTER_KEY = None
        server._GROUNDING_ADAPTER = None
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
            server._RUNTIME_GROUNDING_RUNTIME_ROOT,
            server._RUNTIME_GROUNDING_MODEL_ROOT,
            server._GROUNDING_ADAPTER_KEY,
            server._GROUNDING_ADAPTER,
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

    def test_grounding_pack_settings_are_optional_and_invalid_roots_are_rejected(self) -> None:
        settings = self.client.get("/api/settings")
        self.assertEqual(settings.status_code, 200, settings.text)
        pack = settings.json()["grounding_pack"]
        self.assertFalse(pack["available"])
        self.assertEqual(pack["runtime"]["status"], "not_configured")
        self.assertNotIn("manifest", pack["runtime"])

        invalid_runtime = self.root / "not-a-runtime-pack"
        invalid_runtime.mkdir()
        rejected = self.client.post(
            "/api/settings",
            json={"grounding_runtime_root": str(invalid_runtime)},
        )
        self.assertEqual(rejected.status_code, 400, rejected.text)
        self.assertEqual(rejected.json()["detail"]["code"], "INVALID_GROUNDING_PACK")

        missing = self.client.post("/api/grounding-pack/verify")
        self.assertEqual(missing.status_code, 400, missing.text)
        self.assertEqual(
            missing.json()["detail"]["code"],
            "GROUNDING_PACK_NOT_CONFIGURED",
        )

    def test_grounding_pack_probe_runs_only_after_both_paths_are_configured(self) -> None:
        server._RUNTIME_GROUNDING_RUNTIME_ROOT = str(self.root / "runtime")
        server._RUNTIME_GROUNDING_MODEL_ROOT = str(self.root / "model")
        expected = {
            "available": True,
            "verified": True,
            "message": "本地智能选物扩展已完整验证，运行环境可用",
        }
        with mock.patch.object(server, "probe_grounding_pack", return_value=expected) as probe:
            response = self.client.post("/api/grounding-pack/verify")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), expected)
        probe.assert_called_once_with(
            server._RUNTIME_GROUNDING_RUNTIME_ROOT,
            server._RUNTIME_GROUNDING_MODEL_ROOT,
            server.GROUNDING_MODEL_MANIFEST_PATH,
        )

    def test_changing_or_disabling_grounding_pack_closes_the_old_worker(self) -> None:
        old_adapter = mock.Mock()
        server._GROUNDING_ADAPTER = old_adapter
        server._GROUNDING_ADAPTER_KEY = ("old-runtime", "old-model")

        server._dispose_grounding_adapter_if_changed("", "")

        old_adapter.close.assert_called_once_with()
        self.assertIsNone(server._GROUNDING_ADAPTER)
        self.assertIsNone(server._GROUNDING_ADAPTER_KEY)

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

    def test_optional_dev_origin_accepts_only_explicit_loopback_http_origins(self) -> None:
        allowed = server.trusted_local_origins("http://localhost:1421/")
        self.assertIn("http://localhost:1421", allowed)
        self.assertIn("http://localhost:1420", allowed)

        for candidate in (
            "https://localhost:1421",
            "http://attacker.example:1421",
            "http://localhost:1421/path",
            "http://user@localhost:1421",
            "http://localhost",
        ):
            with self.subTest(candidate=candidate):
                self.assertNotIn(candidate.rstrip("/"), server.trusted_local_origins(candidate))

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
        self.assertIn("retention_remaining_days", active_refs)
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
            first_review.json()["review"]["feedback_id"],
            replay_review.json()["review"]["feedback_id"],
        )
        self.assertEqual(
            first_review.json()["review"]["learning_receipt"]["status"],
            "accumulating",
        )
        self.assertEqual(
            first_review.json()["review"]["learning_receipt"]["independent_sessions"],
            1,
        )
        self.assertEqual(first_review.json()["review"]["learning_receipt"]["threshold"], 3)
        self.assertEqual(
            self.client.get(f"/api/jobs/{job['id']}/traces").json()["count"], 1
        )
        self.assertEqual(
            self.client.get(f"/api/jobs/{job['id']}/reviews").json()["count"], 1
        )
        self.assertEqual(self.ledger.stats()["counts"]["feedback"], 1)

    def test_workspace_completion_is_atomic_idempotent_and_clears_only_task_state(self) -> None:
        source = self.client.post(
            "/api/assets/import",
            files={"file": ("source.png", png_bytes(), "image/png")},
        ).json()
        job, _ = self.ledger.create_job(
            "single",
            [source["id"]],
            engine_key="mock-cloud",
            parameters={"model": "offline-model"},
            idempotency_key="workspace-completion-job",
        )
        item_id = job["items"][0]["id"]
        generation_id = job["items"][0]["generation_id"]
        self.ledger.claim_job_item(item_id)
        result_path = self.output_dir / "completion-result.png"
        result_path.write_bytes(png_bytes((30, 170, 80)))
        result_asset_id = self.ledger.commit_generation_results(
            generation_id,
            source["id"],
            [{"path": str(result_path), "name": result_path.name, "role": "result_main"}],
            job_item_id=item_id,
        )[0]
        active = self.ledger.save_workflow_draft(
            "single",
            expected_revision=1,
            selected_asset_ids=[source["id"]],
            brief={"user_request": "task-only brief"},
            intent={"packaging_text": True},
            parameters={"model": "offline-model", "fidelity": 70},
            active_job_id=job["id"],
            current_generation_id=generation_id,
            current_result_asset_id=result_asset_id,
            compare_state={"position": 0.4},
            ui_state={"result_tab": "main"},
            mask_state={"asset_id": source["id"]},
        )
        payload = {
            "expected_revision": active["revision"],
            "client_request_id": "complete-current-workspace-1",
            "job_id": job["id"],
            "result_asset_id": result_asset_id,
        }

        completed = self.client.post("/api/workspaces/single/complete", json=payload)
        replayed = self.client.post("/api/workspaces/single/complete", json=payload)

        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(replayed.status_code, 200, replayed.text)
        self.assertFalse(completed.json()["replayed"])
        self.assertTrue(replayed.json()["replayed"])
        draft = completed.json()["draft"]
        self.assertEqual(draft["selected_asset_ids"], [])
        self.assertEqual(draft["brief"], {})
        self.assertEqual(draft["intent"], {})
        self.assertIsNone(draft["active_job_id"])
        self.assertIsNone(draft["current_generation_id"])
        self.assertIsNone(draft["current_result_asset_id"])
        self.assertEqual(draft["compare_state"], {})
        self.assertEqual(draft["ui_state"], {})
        self.assertEqual(draft["mask_state"], {})
        self.assertEqual(draft["parameters"], {"model": "offline-model", "fidelity": 70})
        self.assertEqual(
            self.ledger.get_job(job["id"])["items"][0]["result_asset_ids"],
            [result_asset_id],
        )
        connection = sqlite3.connect(self.ledger.db_path)
        try:
            event_count = connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type = 'workspace.completed'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(event_count, 1)

    def test_canvas_api_and_command_api_share_the_durable_job_contract(self) -> None:
        source = self.client.post(
            "/api/assets/import?collection=cutout",
            files={"file": ("canvas-source.png", png_bytes(), "image/png")},
        ).json()
        document = {
            "id": "canvas:api-cutout",
            "schema_version": 1,
            "coordinate_system": {
                "unit": "canvas-pixel",
                "origin": "top-left",
                "x_axis": "right",
                "y_axis": "down",
            },
            "revision": 0,
            "active_artboard_id": "artboard:api-main",
            "source_asset_ids": [source["id"]],
            "artboards": [{
                "id": "artboard:api-main",
                "name": "API 画板",
                "rect": {"x": 0, "y": 0, "width": 24, "height": 18},
                "export": {
                    "pixel_width": 24,
                    "pixel_height": 18,
                    "color_space": "srgb",
                },
            }],
            "layers": [{
                "id": "layer:api-source",
                "artboard_id": "artboard:api-main",
                "source": {
                    "kind": "asset",
                    "id": source["id"],
                    "proxy_ref": "proxy:thumbnail:512",
                    "original_pixel_width": 24,
                    "original_pixel_height": 18,
                },
                "transform": {
                    "x": 0,
                    "y": 0,
                    "scale_x": 1,
                    "scale_y": 1,
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
        saved = self.client.put(
            "/api/workspaces/cutout-batch/canvas",
            json={
                "expected_revision": 0,
                "client_request_id": "api-canvas-save-1",
                "document": document,
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["document"]["revision"], 1)
        current = self.client.get("/api/workspaces/cutout-batch/canvas")
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(
            current.json()["version"]["id"], saved.json()["version"]["id"]
        )
        self.assertNotIn("storage_path", str(current.json()))
        workspace = self.client.get("/api/workspaces/cutout-batch")
        self.assertEqual(workspace.status_code, 200, workspace.text)
        self.assertEqual(
            workspace.json()["canvas"]["version"]["id"],
            saved.json()["version"]["id"],
        )

        registry = self.client.get("/api/commands")
        self.assertEqual(registry.status_code, 200, registry.text)
        self.assertEqual(registry.json()["contract_version"], "canvas-command-v1")

        parameters = {"cutout_selection": {"strategy": "foreground"}}
        canvas_job = self.client.post(
            "/api/commands/command:existing-remove-background/execute",
            json={
                "client_request_id": "api-canvas-command-1",
                "source_asset_ids": [source["id"]],
                "parameters": parameters,
                "canvas_document_id": document["id"],
                "expected_canvas_revision": 1,
                "canvas_operation_id": "operation:api-cutout",
            },
        )
        quick_job = self.client.post("/api/jobs", json={
            "mode": "cutout-batch",
            "source_asset_ids": [source["id"]],
            "parameters": parameters,
            "client_request_id": "api-quick-command-1",
        })
        self.assertEqual(canvas_job.status_code, 200, canvas_job.text)
        self.assertEqual(quick_job.status_code, 200, quick_job.text)
        canvas_snapshot = canvas_job.json()["job"]["snapshot"]
        quick_snapshot = quick_job.json()["job"]["snapshot"]
        self.assertEqual(
            canvas_snapshot["command_id"], quick_snapshot["command_id"]
        )
        self.assertEqual(
            canvas_snapshot["command_id"],
            "command:existing-remove-background",
        )
        self.assertEqual(
            canvas_snapshot["canvas_document_version_id"],
            saved.json()["version"]["id"],
        )
        self.assertIsNone(quick_snapshot["canvas_document_version_id"])

        trace = self.client.post(
            f"/api/jobs/{canvas_job.json()['job']['id']}/traces",
            json={
                "client_request_id": "api-canvas-command-trace-1",
                "stage": "command.accepted",
                "status": "completed",
                "user_input": {},
            },
        )
        self.assertEqual(trace.status_code, 200, trace.text)
        self.assertEqual(
            trace.json()["trace"]["command_id"],
            "command:existing-remove-background",
        )
        self.assertEqual(
            trace.json()["trace"]["canvas_document_version_id"],
            saved.json()["version"]["id"],
        )
        self.assertEqual(
            trace.json()["trace"]["canvas_operation_id"],
            "operation:api-cutout",
        )

        stale = self.client.post(
            "/api/commands/command:existing-remove-background/execute",
            json={
                "client_request_id": "api-canvas-command-stale",
                "source_asset_ids": [source["id"]],
                "parameters": parameters,
                "canvas_document_id": document["id"],
                "expected_canvas_revision": 0,
                "canvas_operation_id": "operation:stale",
            },
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(
            stale.json()["detail"]["code"], "CANVAS_REVISION_CONFLICT"
        )

    def test_canvas_export_uses_original_pixels_and_protects_revision_and_paths(self) -> None:
        original = Image.new("RGB", (800, 600), (220, 40, 30))
        original.paste((20, 190, 80), (400, 0, 800, 300))
        original.paste((30, 80, 220), (0, 300, 400, 600))
        original.paste((240, 210, 30), (400, 300, 800, 600))
        original_buffer = io.BytesIO()
        original.save(original_buffer, "PNG")
        source = self.client.post(
            "/api/assets/import?collection=cutout",
            files={"file": ("original-800x600.png", original_buffer.getvalue(), "image/png")},
        ).json()
        document = {
            "id": "canvas:export-cutout",
            "schema_version": 1,
            "coordinate_system": {
                "unit": "canvas-pixel",
                "origin": "top-left",
                "x_axis": "right",
                "y_axis": "down",
            },
            "revision": 0,
            "active_artboard_id": "artboard:export-main",
            "source_asset_ids": [source["id"]],
            "artboards": [{
                "id": "artboard:export-main",
                "name": "原图导出画板",
                "rect": {"x": 0, "y": 0, "width": 800, "height": 600},
                "export": {
                    "pixel_width": 800,
                    "pixel_height": 600,
                    "color_space": "srgb",
                },
            }],
            "layers": [{
                "id": "layer:export-source",
                "artboard_id": "artboard:export-main",
                "source": {
                    "kind": "asset",
                    "id": source["id"],
                    "proxy_ref": "proxy:thumbnail:512",
                    "original_pixel_width": 800,
                    "original_pixel_height": 600,
                },
                "transform": {
                    "x": 0,
                    "y": 0,
                    "scale_x": 1,
                    "scale_y": 1,
                    "rotation_degrees": 0,
                    "opacity": 1,
                },
                "z_index": 0,
                "visible": True,
                "locked": False,
            }],
            "operations": [],
            "undo_cursor": -1,
            "created_at": "2026-09-02T00:00:00Z",
            "updated_at": "2026-09-02T00:00:00Z",
        }
        saved = self.client.put(
            "/api/workspaces/cutout-batch/canvas",
            json={
                "expected_revision": 0,
                "client_request_id": "api-canvas-export-save-1",
                "document": document,
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)

        with mock.patch.object(
            self.store,
            "thumbnail_bytes",
            side_effect=AssertionError("Fabric proxy must not be used for export"),
        ), mock.patch.object(
            self.store,
            "resolve_asset_path",
            wraps=self.store.resolve_asset_path,
        ) as resolve_original:
            exported = self.client.post(
                "/api/workspaces/cutout-batch/canvas/export",
                json={
                    "expected_revision": 1,
                    "artboard_id": "artboard:export-main",
                    "format": "png",
                },
                headers={"Origin": "http://localhost:1420"},
            )

        self.assertEqual(exported.status_code, 200, exported.text)
        resolve_original.assert_called_once_with(source["id"])
        self.assertEqual(exported.headers["content-type"], "image/png")
        self.assertEqual(exported.headers["x-canvas-source"], "original-assets")
        self.assertEqual(exported.headers["x-canvas-revision"], "1")
        self.assertEqual(exported.headers["x-canvas-pixel-width"], "800")
        self.assertEqual(exported.headers["x-canvas-pixel-height"], "600")
        self.assertNotIn(str(self.root), str(exported.headers))
        exposed = exported.headers.get("access-control-expose-headers", "").lower()
        self.assertIn("content-disposition", exposed)
        self.assertIn("x-canvas-source", exposed)
        with Image.open(io.BytesIO(exported.content)) as image:
            self.assertEqual(image.size, (800, 600))
            self.assertEqual(image.getpixel((100, 100)), (220, 40, 30, 255))
            self.assertEqual(image.getpixel((700, 100)), (20, 190, 80, 255))
            self.assertEqual(image.getpixel((100, 500)), (30, 80, 220, 255))
            self.assertEqual(image.getpixel((700, 500)), (240, 210, 30, 255))

        stale = self.client.post(
            "/api/workspaces/cutout-batch/canvas/export",
            json={"expected_revision": 0, "format": "png"},
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["code"], "CANVAS_REVISION_CONFLICT")

        _, original_path = self.store.resolve_asset_path(source["id"])
        original_path.unlink()
        missing = self.client.post(
            "/api/workspaces/cutout-batch/canvas/export",
            json={"expected_revision": 1, "format": "png"},
        )
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(missing.json()["detail"]["code"], "ASSET_FILE_MISSING")
        self.assertNotIn(str(self.root), missing.text)

        cors = self.client.options(
            "/api/workspaces/cutout-batch/canvas/export",
            headers={
                "Origin": "http://localhost:1420",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        self.assertEqual(cors.status_code, 200, cors.text)
        self.assertIn("POST", cors.headers.get("access-control-allow-methods", ""))

    def test_suggest_review_returns_the_actual_pending_suggestion_and_stable_receipt(self) -> None:
        def create_reviewable_job(index: int):
            source = self.client.post(
                "/api/assets/import",
                files={"file": (f"source-{index}.png", png_bytes((200, 80 + index, 40)), "image/png")},
            ).json()
            job, _ = self.ledger.create_job(
                "single",
                [source["id"]],
                engine_key="mock-cloud",
                parameters={"model": "offline-model"},
                idempotency_key=f"suggest-review-job-{index}",
            )
            item = job["items"][0]
            self.ledger.claim_job_item(item["id"])
            result_path = self.output_dir / f"suggest-result-{index}.png"
            result_path.write_bytes(png_bytes((40, 120, 160 + index)))
            result_id = self.ledger.commit_generation_results(
                item["generation_id"],
                source["id"],
                [{"path": str(result_path), "name": result_path.name, "role": "result_main"}],
                job_item_id=item["id"],
            )[0]
            return job, item["generation_id"], result_id

        first_job, first_generation, first_result = create_reviewable_job(1)
        second_job, second_generation, second_result = create_reviewable_job(2)
        first = self.client.post(
            f"/api/jobs/{first_job['id']}/reviews",
            json={
                "client_request_id": "specific-evidence-1",
                "result_asset_id": first_result,
                "generation_id": first_generation,
                "decision": "adjust",
                "note": "包装文字变形",
                "learning_action": "record",
            },
        )
        second_payload = {
            "client_request_id": "specific-evidence-2",
            "result_asset_id": second_result,
            "generation_id": second_generation,
            "decision": "adjust",
            "note": "包装文字变形",
            "learning_action": "suggest",
        }
        second = self.client.post(
            f"/api/jobs/{second_job['id']}/reviews", json=second_payload
        )
        replay = self.client.post(
            f"/api/jobs/{second_job['id']}/reviews", json=second_payload
        )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(first.json()["review"]["learning_receipt"]["status"], "accumulating")
        receipt = second.json()["review"]["learning_receipt"]
        self.assertEqual(receipt["status"], "pending")
        self.assertEqual(receipt["independent_sessions"], 2)
        self.assertEqual(receipt["threshold"], 2)
        self.assertTrue(receipt["suggestion_id"])
        self.assertEqual(
            replay.json()["review"]["learning_receipt"]["suggestion_id"],
            receipt["suggestion_id"],
        )
        self.assertEqual(self.ledger.stats()["counts"]["feedback"], 2)
        self.assertEqual(self.ledger.stats()["counts"]["result_reviews"], 2)
        self.assertEqual(self.ledger.stats()["pending_memory"], 1)
        pending = self.client.get("/api/memory/suggestions?status=pending")
        self.assertEqual(pending.status_code, 200, pending.text)
        suggestion = pending.json()[0]
        self.assertEqual(suggestion["id"], receipt["suggestion_id"])
        self.assertEqual(
            {source["job_id"] for source in suggestion["source_results"]},
            {first_job["id"], second_job["id"]},
        )
        self.assertEqual(
            {source["result_asset_id"] for source in suggestion["source_results"]},
            {first_result, second_result},
        )
        self.assertTrue(all(source["review_id"] for source in suggestion["source_results"]))
        self.assertTrue(all("path" not in source for source in suggestion["source_results"]))
        direct = self.client.get(
            f"/api/memory/suggestions/{receipt['suggestion_id']}"
        )
        self.assertEqual(direct.status_code, 200, direct.text)
        self.assertEqual(direct.json()["source_results"], suggestion["source_results"])

    def test_memory_governance_api_exposes_history_and_rejects_stale_edits(self) -> None:
        suggestion = self.ledger.add_memory_suggestion(
            "designer",
            "composition.spacing",
            {
                "value": "generous",
                "label": "偏好留白",
                "directive": "同类任务优先保留克制留白",
                "distinct_sessions": 2,
                "contradiction_count": 0,
            },
            scope_id="default",
            evidence=[{"feedback_id": "missing-old-feedback"}],
            confidence=0.8,
        )
        edited = self.client.patch(
            f"/api/memory/suggestions/{suggestion['id']}",
            json={
                "action": "edit",
                "expected_revision": 1,
                "label": "食品主图留白",
                "directive": "食品主图优先保留安全留白，不让主体顶边",
            },
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()["governance"]["revision"], 2)
        self.assertEqual(edited.json()["governance"]["history_count"], 1)

        stale = self.client.patch(
            f"/api/memory/suggestions/{suggestion['id']}",
            json={"action": "reject", "expected_revision": 1},
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["code"], "MEMORY_REVISION_CONFLICT")
        self.assertEqual(
            stale.json()["detail"]["current"]["governance"]["revision"], 2
        )

        approved = self.client.patch(
            f"/api/memory/suggestions/{suggestion['id']}",
            json={"action": "approve", "expected_revision": 2},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["status"], "approved")
        disabled = self.client.patch(
            f"/api/memory/suggestions/{suggestion['id']}",
            json={"action": "disable", "expected_revision": 3},
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertEqual(disabled.json()["status"], "disabled")
        all_suggestions = self.client.get("/api/memory/suggestions?status=all")
        self.assertEqual(all_suggestions.status_code, 200, all_suggestions.text)
        self.assertIn(
            suggestion["id"],
            {item["id"] for item in all_suggestions.json()},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
