from __future__ import annotations

import copy
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from PIL import Image


# Importing server creates its module-level singleton state. Keep that import
# isolated even when this file is collected before the other API tests.
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
from python.atelier_ledger import AtelierLedger  # noqa: E402
from python.job_engine import JobExecutionError  # noqa: E402


TERMINAL_STATUSES = {"completed", "partial", "failed", "canceled"}


def png_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (28, 20), color).save(buffer, "PNG")
    return buffer.getvalue()


class OfflineKnowledge:
    """Small deterministic replacement for prompt enrichment in API tests."""

    @staticmethod
    def status() -> dict:
        return {"available": False, "document_count": 0, "rule_count": 0}

    @staticmethod
    def enrich_prompt(prompt, negative_prompt, _context):  # type: ignore[no-untyped-def]
        return {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "sources": [],
        }


class DurableJobApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "atelier.sqlite3"
        self.asset_dir = self.root / "assets"
        self.output_dir = self.root / "output"
        self.output_dir.mkdir(parents=True)
        (self.output_dir / "_tmp").mkdir()

        self.ledger = AtelierLedger(self.db_path)
        self.store = AssetStore(self.asset_dir, self.ledger)
        self.original_globals = {
            "LEDGER": server.LEDGER,
            "ASSET_STORE": server.ASSET_STORE,
            "ASSET_DIR": server.ASSET_DIR,
            "OUTPUT_DIR": server.OUTPUT_DIR,
            "CONFIG_PATH": server.CONFIG_PATH,
            "_RUNTIME_OUTPUT_ROOT": server._RUNTIME_OUTPUT_ROOT,
            "KNOWLEDGE": server.KNOWLEDGE,
            "JOB_ENGINE": server.JOB_ENGINE,
        }
        server.LEDGER = self.ledger
        server.ASSET_STORE = self.store
        server.ASSET_DIR = self.asset_dir
        server.OUTPUT_DIR = self.output_dir
        server.CONFIG_PATH = self.root / "config.json"
        server._RUNTIME_OUTPUT_ROOT = self.output_dir
        server.KNOWLEDGE = OfflineKnowledge()
        server.JOB_ENGINE = None
        server.save_config({
            "output_root": str(self.output_dir),
            "known_output_roots": [str(self.output_dir)],
        })

        self.vlm_products = [
            {
                "bbox": [0, 0, 1000, 1000],
                "name": "离线样品",
                "ptype": "food",
                "has_container": False,
                "cutoff": False,
                "angle_hint": "front",
            }
        ]
        self.ai_calls: list[str] = []
        self.ai_lock = threading.Lock()
        self.fail_prompt_once = ""
        self.failed_prompt = False
        self.force_square_output = False
        self.ai_started: threading.Event | None = None
        self.ai_release: threading.Event | None = None
        self.remove_calls = 0
        self.real_ai_i2i = server.ai_i2i
        self.real_vlm_detect_products = server.vlm_detect_products
        self.real_remove_bg_hd = server.remove_bg_hd

        self.patches = [
            mock.patch.object(
                server.requests.sessions.Session,
                "request",
                side_effect=AssertionError("offline job tests must never access the network"),
            ),
            mock.patch.object(server, "vlm_detect_products", side_effect=self._fake_vlm),
            mock.patch.object(server, "ai_i2i", side_effect=self._fake_ai_i2i),
            mock.patch.object(server, "remove_bg_hd", side_effect=self._fake_remove_bg),
        ]
        (
            self.network_request,
            self.vlm_mock,
            self.ai_mock,
            self.remove_mock,
        ) = [patcher.start() for patcher in self.patches]

    def tearDown(self) -> None:
        if self.ai_release is not None:
            self.ai_release.set()
        if server.JOB_ENGINE is not None:
            server.JOB_ENGINE.stop()
            server.JOB_ENGINE = None
        for patcher in reversed(self.patches):
            patcher.stop()
        for name, value in self.original_globals.items():
            setattr(server, name, value)
        self.temp_dir.cleanup()

    def _fake_vlm(self, _image_path: str, _task_id: str = "?") -> dict:
        products = copy.deepcopy(self.vlm_products)
        return {
            "products": products,
            "count": len(products),
            "scene": "multi" if len(products) > 1 else "single",
        }

    def _fake_ai_i2i(
        self,
        prompt,
        ref_img,
        _model_key,
        negative_prompt=None,
        size="2048x2048",
        stage="?",
        tid_ref="?",
        on_submitted=None,
        output_spec=None,
    ):
        del negative_prompt, size, tid_ref
        prompt_text = str(prompt)
        with self.ai_lock:
            self.ai_calls.append(prompt_text)
            should_fail = (
                bool(self.fail_prompt_once)
                and self.fail_prompt_once in prompt_text
                and not self.failed_prompt
            )
            if should_fail:
                self.failed_prompt = True
        if self.ai_started is not None:
            self.ai_started.set()
        if self.ai_release is not None and not self.ai_release.wait(timeout=10):
            raise TimeoutError("offline AI release gate timed out")
        if on_submitted is not None:
            on_submitted(f"offline-{stage}-{len(self.ai_calls)}")
        if should_fail:
            raise JobExecutionError("OFFLINE_INJECTED_FAILURE", "injected offline failure")
        if output_spec and output_spec.get("strict_aspect"):
            if self.force_square_output:
                return Image.new("RGB", (240, 240), (80, 140, 210))
            ratio = float(output_spec.get("effective_ratio_value") or 1.0)
            if ratio >= 1:
                result_size = (max(1, round(240 * ratio)), 240)
            else:
                result_size = (240, max(1, round(240 / ratio)))
            return Image.new("RGB", result_size, (80, 140, 210))
        if isinstance(ref_img, Image.Image):
            return ref_img.convert("RGB").copy()
        return Image.new("RGB", (28, 20), (80, 140, 210))

    def _fake_remove_bg(self, image: Image.Image) -> Image.Image:
        self.remove_calls += 1
        return image.convert("RGBA")

    @contextmanager
    def live_client(self):
        with TestClient(server.app) as client:
            self.assertIsNotNone(server.JOB_ENGINE)
            self.assertTrue(server.JOB_ENGINE.is_running)
            yield client

    @staticmethod
    def import_asset(
        client: TestClient,
        name: str,
        color: tuple[int, int, int],
    ) -> dict:
        response = client.post(
            "/api/assets/import",
            files={"file": (name, png_bytes(color), "image/png")},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        return response.json()

    @staticmethod
    def create_job(client: TestClient, payload: dict) -> dict:
        response = client.post("/api/jobs", json=payload)
        if response.status_code != 200:
            raise AssertionError(response.text)
        return response.json()

    @staticmethod
    def wait_for_job(job_id: str, timeout: float = 15) -> dict:
        engine = server.JOB_ENGINE
        if engine is None:
            raise AssertionError("job engine is not running")
        return engine.wait_for_job(job_id, timeout=timeout)

    def assert_result_lineage(
        self,
        client: TestClient,
        job: dict,
        expected_per_source: dict[str, int],
    ) -> None:
        seen_per_source = {source_id: 0 for source_id in expected_per_source}
        for item in job["items"]:
            source_id = item["source_asset_id"]
            self.assertIn(source_id, expected_per_source)
            for asset_id in item["result_asset_ids"]:
                stored = server.LEDGER.get_asset(asset_id)
                self.assertEqual(stored["parent_asset_id"], source_id)
                self.assertEqual(stored["metadata"]["generation_id"], item["generation_id"])
                self.assertEqual(stored["metadata"]["job_item_id"], item["id"])
                public = client.get(f"/api/assets/{asset_id}")
                self.assertEqual(public.status_code, 200, public.text)
                public_asset = public.json()
                content = client.get(public_asset["content_url"])
                self.assertEqual(content.status_code, 200, content.text)
                self.assertGreater(len(content.content), 20)
                with Image.open(io.BytesIO(content.content)) as image:
                    image.verify()
                seen_per_source[source_id] += 1
        self.assertEqual(seen_per_source, expected_per_source)

    def test_job_runtime_endpoint_reports_real_executor_ownership(self) -> None:
        with self.live_client() as client:
            response = client.get("/api/jobs/runtime")
            self.assertEqual(response.status_code, 200, response.text)
            runtime = response.json()
            self.assertTrue(runtime["running"])
            self.assertIn("leader", runtime)
            self.assertEqual(runtime["in_flight"], 0)
            self.assertEqual(runtime["resource_in_use"], {})
            self.assertEqual(runtime["unreconciled_workers"], [])

    def test_single_job_api_outputs_lineage_urls_and_legacy_progress_from_db(self) -> None:
        with self.live_client() as client:
            source = self.import_asset(client, "single.png", (220, 80, 40))
            created = self.create_job(
                client,
                {
                    "mode": "single",
                    "source_asset_ids": [source["id"]],
                    "parameters": {"batch": 2},
                    "client_request_id": "single-offline-1",
                },
            )
            self.assertTrue(created["created"])
            final = self.wait_for_job(created["job"]["id"])

            self.assertEqual(final["status"], "completed")
            self.assertEqual(len(final["items"]), 1)
            self.assertEqual(len(final["items"][0]["result_asset_ids"]), 4)
            self.assertEqual(final["parameters"]["model"], "gpt-image-2")
            self.assertEqual(final["items"][0]["attempts"][0]["model"], "gpt-image-2")
            self.assert_result_lineage(client, final, {source["id"]: 4})

            get_job = client.get(f"/api/jobs/{final['id']}")
            self.assertEqual(get_job.status_code, 200)
            self.assertEqual(get_job.json()["job"]["status"], "completed")
            listing = client.get("/api/jobs")
            self.assertEqual(listing.status_code, 200)
            self.assertIn(final["id"], {job["id"] for job in listing.json()["jobs"]})

            # The compatibility route must derive state and result URLs from
            # the durable job record even though no legacy tracker entry exists.
            self.assertFalse(server.tracker.get(final["id"]))
            progress = client.get(f"/api/progress/{final['id']}")
            self.assertEqual(progress.status_code, 200)
            body = progress.json()
            self.assertEqual(body["status"], "completed")
            self.assertEqual(body["progress"], 1)
            self.assertEqual(len(body["results"]["main"]), 2)
            self.assertEqual(len(body["results"]["cutout"]), 2)
            self.assertEqual(body["job"]["id"], final["id"])
            traces = client.get(f"/api/jobs/{final['id']}/traces")
            self.assertEqual(traces.status_code, 200, traces.text)
            trace_items = traces.json()["traces"]
            self.assertTrue(any(
                item["stage"] == "prompt.primary"
                and item["compiled_prompt"]
                and item["status"] == "completed"
                for item in trace_items
            ))
            self.assertTrue(any(
                item["stage"] == "result.publish"
                and len(item["output"]["result_asset_ids"]) == 4
                for item in trace_items
            ))
            self.network_request.assert_not_called()

    def test_explicit_output_spec_survives_job_snapshot_and_both_cloud_stages(self) -> None:
        with self.live_client() as client:
            source = self.import_asset(client, "portrait-spec.png", (90, 150, 210))
            created = self.create_job(
                client,
                {
                    "mode": "single",
                    "source_asset_ids": [source["id"]],
                    "parameters": {
                        "batch": 1,
                        "product_name": "竖版商品",
                        "model": "gpt-image-2",
                        "output_ratio": "9:16",
                        "output_resolution": "4k",
                    },
                    "client_request_id": "explicit-output-spec",
                },
            )
            final = self.wait_for_job(created["job"]["id"])

            self.assertEqual(final["status"], "completed")
            self.assertEqual(final["parameters"]["output_ratio"], "9:16")
            self.assertEqual(final["parameters"]["output_resolution"], "4k")
            self.assertTrue(final["parameters"]["output_spec_explicit"])
            traces = client.get(f"/api/jobs/{final['id']}/traces").json()["traces"]
            output_spec = next(item for item in traces if item["stage"] == "output.spec")
            self.assertEqual(output_spec["output"]["effective_ratio"], "9:16")
            self.assertEqual(output_spec["output"]["provider_params"]["size"], "2160x3840")
            provider_results = [
                item for item in traces if item["stage"].startswith("provider.image.")
            ]
            self.assertEqual(len(provider_results), 2)
            self.assertTrue(all(item["output"]["aspect_matches"] for item in provider_results))
            main_asset = next(
                server.LEDGER.get_asset(asset_id)
                for asset_id in final["items"][0]["result_asset_ids"]
                if server.LEDGER.get_asset(asset_id)["role"] == "result_main"
            )
            self.assertAlmostEqual(main_asset["width"] / main_asset["height"], 9 / 16, delta=0.01)
            self.network_request.assert_not_called()

    def test_wrong_provider_aspect_stops_before_paid_refine_stage(self) -> None:
        self.force_square_output = True
        with self.live_client() as client:
            source = self.import_asset(client, "wrong-aspect.png", (210, 120, 70))
            created = self.create_job(
                client,
                {
                    "mode": "single",
                    "source_asset_ids": [source["id"]],
                    "parameters": {
                        "batch": 1,
                        "product_name": "横版商品",
                        "output_ratio": "16:9",
                        "output_resolution": "4k",
                    },
                    "client_request_id": "wrong-provider-aspect",
                    "max_attempts": 1,
                },
            )
            final = self.wait_for_job(created["job"]["id"])

            self.assertEqual(final["status"], "failed")
            self.assertEqual(final["items"][0]["error_code"], "OUTPUT_ASPECT_MISMATCH")
            self.assertEqual(len(self.ai_calls), 1)
            traces = client.get(f"/api/jobs/{final['id']}/traces").json()["traces"]
            failed = next(item for item in traces if item["stage"] == "provider.image.1-1")
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["output"]["actual_ratio"], "1:1")
            self.network_request.assert_not_called()

    def test_output_root_is_frozen_per_job_and_old_results_remain_readable(self) -> None:
        first_root = self.root / "delivery-a"
        second_root = self.root / "delivery-b"
        first_root.mkdir()
        second_root.mkdir()
        self.ai_started = threading.Event()
        self.ai_release = threading.Event()
        with self.live_client() as client:
            selected = client.post("/api/settings", json={"output_root": str(first_root)})
            self.assertEqual(selected.status_code, 200, selected.text)
            source = self.import_asset(client, "frozen-root.png", (210, 110, 50))
            created = self.create_job(client, {
                "mode": "single",
                "source_asset_ids": [source["id"]],
                "parameters": {"batch": 1},
                "client_request_id": "frozen-output-root",
            })
            self.assertEqual(created["job"]["parameters"]["output_root"], str(first_root.resolve()))
            self.assertTrue(self.ai_started.wait(timeout=10))

            switched = client.post("/api/settings", json={"output_root": str(second_root)})
            self.assertEqual(switched.status_code, 200, switched.text)
            self.ai_release.set()
            final = self.wait_for_job(created["job"]["id"])

            self.assertEqual(final["status"], "completed")
            self.assertEqual(final["parameters"]["output_root"], str(first_root.resolve()))
            for asset_id in final["items"][0]["result_asset_ids"]:
                stored = server.LEDGER.get_asset(asset_id)
                result_path = Path(stored["path"])
                self.assertTrue(result_path.is_relative_to(first_root.resolve()))
                self.assertEqual(stored["metadata"]["output_root"], str(first_root.resolve()))
                content = client.get(f"/api/assets/{asset_id}/content")
                self.assertEqual(content.status_code, 200, content.text)
            self.assertFalse(any(second_root.rglob("*.jpg")))
            self.assertFalse(any(second_root.rglob("*.png")))

    def test_unavailable_snapshot_root_fails_in_chinese_before_model_work(self) -> None:
        delivery_root = self.root / "detached-delivery"
        delivery_root.mkdir()
        client = TestClient(server.app)
        try:
            selected = client.post("/api/settings", json={"output_root": str(delivery_root)})
            self.assertEqual(selected.status_code, 200, selected.text)
            source = self.import_asset(client, "detached.png", (80, 130, 190))
            created = self.create_job(client, {
                "mode": "single",
                "source_asset_ids": [source["id"]],
                "parameters": {"batch": 1},
                "client_request_id": "detached-output-root",
                "max_attempts": 1,
            })
        finally:
            client.close()
        shutil.rmtree(delivery_root)

        with self.live_client():
            final = self.wait_for_job(created["job"]["id"])
        item = final["items"][0]
        self.assertEqual(final["status"], "failed")
        self.assertEqual(item["error_code"], "OUTPUT_ROOT_UNAVAILABLE")
        self.assertIn("磁盘", item["error_message"])
        self.assertEqual(self.ai_calls, [])
        self.network_request.assert_not_called()

    def test_group_split_is_one_item_with_per_product_outputs_and_lineage(self) -> None:
        self.vlm_products = [
            {
                "bbox": [0, 0, 480, 1000],
                "name": "产品甲",
                "ptype": "food",
                "has_container": True,
                "cutoff": False,
                "angle_hint": "45top",
            },
            {
                "bbox": [520, 0, 1000, 1000],
                "name": "产品乙",
                "ptype": "packaging",
                "has_container": False,
                "cutoff": True,
                "angle_hint": "front",
            },
        ]
        with self.live_client() as client:
            source = self.import_asset(client, "group.png", (70, 170, 90))
            created = self.create_job(
                client,
                {
                    "mode": "group-split",
                    "source_asset_ids": [source["id"]],
                    "parameters": {"refine": False},
                },
            )
            final = self.wait_for_job(created["job"]["id"])

            self.assertEqual(final["status"], "completed")
            self.assertEqual(final["total_items"], 1)
            self.assertEqual(len(final["items"]), 1)
            self.assertEqual(len(final["items"][0]["result_asset_ids"]), 4)
            attempt = final["items"][0]["attempts"][0]
            self.assertEqual(
                attempt["model"], "gemini-3.1-flash-image-preview"
            )
            self.assertEqual(attempt["metadata"]["detected_products"], 2)
            self.assert_result_lineage(client, final, {source["id"]: 4})
            self.assertEqual(self.ai_mock.call_count, 2)
            self.network_request.assert_not_called()

    def test_group_split_rejects_unbounded_or_malformed_detection_before_cloud(self) -> None:
        with self.live_client() as client:
            source = self.import_asset(client, "group-limit.png", (40, 90, 140))
            self.vlm_products = [{
                "bbox": [0, 0, 1000, 1000],
                "name": f"产品{index}",
                "ptype": "food",
                "has_container": False,
                "cutoff": False,
                "angle_hint": "front",
            } for index in range(server.MAX_GROUP_PRODUCTS + 1)]
            too_many = self.create_job(client, {
                "mode": "group-split",
                "source_asset_ids": [source["id"]],
                "parameters": {"refine": False},
            })
            too_many_final = self.wait_for_job(too_many["job"]["id"])
            self.assertEqual(too_many_final["status"], "failed")
            self.assertEqual(
                too_many_final["items"][0]["error_code"],
                "TOO_MANY_PRODUCTS_DETECTED",
            )

            self.vlm_products = [
                {
                    "bbox": [0, 0, 500, 1000],
                    "name": "合法产品",
                    "ptype": "food",
                    "has_container": False,
                    "cutoff": False,
                },
                {
                    "bbox": [500, 0, "not-a-number", 1000],
                    "name": "异常产品",
                    "ptype": "food",
                    "has_container": False,
                    "cutoff": False,
                },
            ]
            malformed = self.create_job(client, {
                "mode": "group-split",
                "source_asset_ids": [source["id"]],
                "parameters": {"refine": False},
            })
            malformed_final = self.wait_for_job(malformed["job"]["id"])
            self.assertEqual(malformed_final["status"], "failed")
            self.assertEqual(
                malformed_final["items"][0]["error_code"],
                "INVALID_PRODUCT_DETECTION",
            )
            self.assertEqual(self.ai_mock.call_count, 0)
            self.assertEqual(self.remove_mock.call_count, 0)
            self.network_request.assert_not_called()

    def test_group_split_detection_failure_stops_before_paid_generation(self) -> None:
        self.vlm_mock.side_effect = self.real_vlm_detect_products
        with mock.patch.object(
            server,
            "api_request",
            side_effect=RuntimeError("offline VLM unavailable"),
        ):
            with self.live_client() as client:
                source = self.import_asset(client, "group-vlm-failure.png", (25, 75, 125))
                created = self.create_job(client, {
                    "mode": "group-split",
                    "source_asset_ids": [source["id"]],
                    "parameters": {"refine": False},
                    "max_attempts": 1,
                })
                final = self.wait_for_job(created["job"]["id"])

                self.assertEqual(final["status"], "failed")
                self.assertEqual(
                    final["items"][0]["error_code"],
                    "PRODUCT_DETECTION_FAILED",
                )
                self.ai_mock.assert_not_called()
                self.remove_mock.assert_not_called()
                self.network_request.assert_not_called()

    def test_group_split_rejects_detection_count_mismatch_before_cloud(self) -> None:
        self.vlm_mock.side_effect = lambda *_args, **_kwargs: {
            "products": copy.deepcopy(self.vlm_products),
            "count": len(self.vlm_products) + 1,
            "scene": "multi",
        }
        with self.live_client() as client:
            source = self.import_asset(client, "group-count-mismatch.png", (35, 85, 135))
            created = self.create_job(client, {
                "mode": "group-split",
                "source_asset_ids": [source["id"]],
                "parameters": {"refine": False},
                "max_attempts": 1,
            })
            final = self.wait_for_job(created["job"]["id"])

            self.assertEqual(final["status"], "failed")
            self.assertEqual(
                final["items"][0]["error_code"],
                "INVALID_PRODUCT_DETECTION",
            )
            self.ai_mock.assert_not_called()
            self.remove_mock.assert_not_called()
            self.network_request.assert_not_called()

    def test_group_split_refine_requires_real_boolean(self) -> None:
        client = TestClient(server.app)
        try:
            source = self.import_asset(client, "group-refine.png", (50, 100, 150))
            response = client.post("/api/jobs", json={
                "mode": "group-split",
                "source_asset_ids": [source["id"]],
                "parameters": {"refine": "false"},
            })
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(
                response.json()["detail"]["code"], "INVALID_JOB_REQUEST"
            )
            self.assertEqual(server.LEDGER.list_jobs(), [])
            self.ai_mock.assert_not_called()
            self.network_request.assert_not_called()
        finally:
            client.close()

    def test_ai_download_temp_names_are_unique_even_in_same_millisecond(self) -> None:
        destinations: list[Path] = []

        def fake_download(_url: str, destination: Path) -> str:
            destination = Path(destination)
            destinations.append(destination)
            Image.new("RGB", (8, 6), (90, 120, 150)).save(destination, "JPEG")
            return str(destination)

        with mock.patch.object(
            server, "submit_generate", return_value="offline-remote-task"
        ), mock.patch.object(
            server, "poll_task", return_value="https://offline.invalid/result.jpg"
        ), mock.patch.object(
            server, "download_result", side_effect=fake_download
        ), mock.patch.object(server.time, "time", return_value=1234.567):
            first = self.real_ai_i2i(
                "offline prompt", Image.new("RGB", (8, 6)), "offline-model", stage="1-1"
            )
            second = self.real_ai_i2i(
                "offline prompt", Image.new("RGB", (8, 6)), "offline-model", stage="1-1"
            )

        self.assertEqual(first.size, (8, 6))
        self.assertEqual(second.size, (8, 6))
        self.assertEqual(len(destinations), 2)
        self.assertNotEqual(destinations[0], destinations[1])
        self.assertTrue(all(not path.exists() for path in destinations))
        self.network_request.assert_not_called()

    def test_multi_file_accepts_twenty_sources_but_keeps_output_budget(self) -> None:
        twenty = [f"asset-{index}" for index in range(20)]
        server._validate_job_request("multi-file", twenty, {"variations": 1})
        with self.assertRaisesRegex(ValueError, "at most 20 source assets"):
            server._validate_job_request("multi-file", twenty + ["asset-20"], {"variations": 1})
        with self.assertRaisesRegex(ValueError, "at most 24 generated variations"):
            server._validate_job_request("multi-file", twenty, {"variations": 2})

    def test_folder_multi_file_job_auto_classifies_delivery_without_moving_sources(self) -> None:
        source_folder = self.root / "整夹来源"
        source_folder.mkdir()
        first_path = source_folder / "商品甲.png"
        second_path = source_folder / "商品乙.png"
        first_path.write_bytes(png_bytes((210, 70, 40)))
        second_path.write_bytes(png_bytes((40, 130, 210)))

        with self.live_client() as client:
            imported = client.post(
                "/api/folder-sources/import",
                json={"folder_path": str(source_folder)},
            )
            self.assertEqual(imported.status_code, 200, imported.text)
            folder_batch = imported.json()["folder_batch"]
            created = self.create_job(
                client,
                {
                    "mode": "multi-file",
                    "source_asset_ids": folder_batch["asset_ids"],
                    "parameters": {
                        "variations": 1,
                        "model": "offline-model",
                        "folder_delivery": {
                            "batch_id": folder_batch["batch_id"],
                            "source_folder": folder_batch["source_folder"],
                            "delivery_root": folder_batch["delivery_root"],
                            "source_names": folder_batch["source_names"],
                            "part_index": 1,
                            "part_count": 1,
                        },
                    },
                    "requested_concurrency": 2,
                },
            )
            final = self.wait_for_job(created["job"]["id"])

        self.assertEqual(final["status"], "completed")
        delivery_root = Path(folder_batch["delivery_root"])
        main_files = list((delivery_root / "01_商业主图").glob("*.jpg"))
        cutout_files = list((delivery_root / "02_透明PNG").glob("*.png"))
        self.assertEqual(len(main_files), 2)
        self.assertEqual(len(cutout_files), 2)
        manifest = json.loads((delivery_root / "处理记录.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["batch_id"], folder_batch["batch_id"])
        self.assertEqual(len(manifest["items"]), 2)
        self.assertTrue(first_path.exists())
        self.assertTrue(second_path.exists())
        self.network_request.assert_not_called()

    def test_cutout_batch_has_one_item_and_one_output_per_source(self) -> None:
        with self.live_client() as client:
            sources = [
                self.import_asset(client, f"cutout-{index}.png", (20 + index, 90, 160))
                for index in range(3)
            ]
            created = self.create_job(
                client,
                {
                    "mode": "cutout-batch",
                    "source_asset_ids": [source["id"] for source in sources],
                    "parameters": {"brief": {"goal": "只保留两个汉堡"}},
                    "requested_concurrency": 3,
                },
            )
            final = self.wait_for_job(created["job"]["id"])

            self.assertEqual(final["status"], "completed")
            self.assertEqual(final["total_items"], 3)
            self.assertEqual(len(final["items"]), 3)
            self.assertEqual(
                final["parameters"]["model"], "local-rembg/birefnet-general"
            )
            self.assertTrue(all(
                item["attempts"][0]["model"] == "local-rembg/birefnet-general"
                for item in final["items"]
            ))
            self.assertTrue(
                all(len(item["result_asset_ids"]) == 1 for item in final["items"])
            )
            self.assert_result_lineage(
                client,
                final,
                {source["id"]: 1 for source in sources},
            )
            self.assertEqual(self.remove_mock.call_count, 3)
            self.assertEqual(self.ai_mock.call_count, 0)
            traces = client.get(f"/api/jobs/{final['id']}/traces").json()["traces"]
            cutout_traces = [
                item for item in traces if item["stage"] == "cutout.segment"
            ]
            self.assertEqual(len(cutout_traces), 3)
            self.assertTrue(all(
                item["ignored_fields"] == ["brief"]
                and item["output"]["selection_prompt_supported"] is False
                and item["model"] == "local-rembg/birefnet-general"
                for item in cutout_traces
            ))
            self.network_request.assert_not_called()

    def test_semantic_cutout_requires_confirmation_then_executes_manual_regions(self) -> None:
        with self.live_client() as client:
            source = self.import_asset(client, "two-burgers.png", (210, 80, 35))
            preview = client.post(
                "/api/semantic-cutout/preview",
                json={
                    "asset_id": source["id"],
                    "query": "汉堡",
                    "target_count": 1,
                    "regions": [],
                },
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()["preview"]["status"], "needs_manual_grounding")
            self.assertFalse(preview.json()["preview"]["automatic_grounding_available"])

            rejected = client.post(
                "/api/jobs",
                json={
                    "mode": "cutout-batch",
                    "source_asset_ids": [source["id"]],
                    "parameters": {
                        "cutout_selection": {
                            "strategy": "semantic",
                            "query": "汉堡",
                            "target_count": 1,
                            "sources": {},
                        }
                    },
                    "client_request_id": "semantic-without-confirmation",
                },
            )
            self.assertEqual(rejected.status_code, 400, rejected.text)
            self.assertEqual(rejected.json()["detail"]["stage"], "selection")
            self.assertEqual(
                rejected.json()["detail"]["code"], "SEMANTIC_CONFIRMATION_REQUIRED"
            )

            confirmed = client.post(
                "/api/semantic-cutout/confirm",
                json={
                    "asset_id": source["id"],
                    "query": "汉堡",
                    "target_count": 1,
                    "regions": [
                        {"id": "burger-1", "bbox": [0.1, 0.1, 0.4, 0.8]}
                    ],
                },
            )
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            selection = confirmed.json()["selection"]
            source_plan = selection["sources"][source["id"]]
            self.assertEqual(source_plan["status"], "confirmed")
            self.assertTrue(source_plan["digest"].startswith("sha256:"))

            created = self.create_job(
                client,
                {
                    "mode": "cutout-batch",
                    "source_asset_ids": [source["id"]],
                    "parameters": {
                        "brief": {"user_request": "只保留一个汉堡"},
                        "cutout_selection": selection,
                    },
                },
            )
            final = self.wait_for_job(created["job"]["id"])
            self.assertEqual(final["status"], "completed")
            self.assertEqual(final["parameters"]["cutout_selection"], selection)
            traces = client.get(f"/api/jobs/{final['id']}/traces").json()["traces"]
            segment = next(item for item in traces if item["stage"] == "cutout.segment")
            self.assertEqual(segment["ignored_fields"], [])
            self.assertEqual(segment["parameters"]["strategy"], "semantic")
            self.assertEqual(segment["parameters"]["selection_method"], "manual-box")
            self.assertEqual(segment["parameters"]["alpha_mode"], "native-soft")
            self.assertIs(segment["parameters"]["post_process_mask"], False)
            self.assertFalse(segment["output"]["text_grounding_supported"])
            self.assertTrue(segment["output"]["manual_grounding_confirmed"])
            self.assertEqual(segment["output"]["selected_region_count"], 1)
            self.assertEqual(self.remove_mock.call_count, 1)
            self.assertEqual(self.ai_mock.call_count, 0)
            self.network_request.assert_not_called()

    def test_local_cutout_preserves_birefnet_native_soft_alpha(self) -> None:
        image = Image.new("RGB", (12, 8), (210, 80, 35))
        expected = image.convert("RGBA")
        session = object()
        with (
            mock.patch.object(server, "_get_bgsession", return_value=session),
            mock.patch("rembg.remove", return_value=expected) as remove,
        ):
            actual = self.real_remove_bg_hd(image)

        self.assertIs(actual, expected)
        _, kwargs = remove.call_args
        self.assertIs(kwargs["session"], session)
        self.assertIs(kwargs["alpha_matting"], False)
        self.assertIs(kwargs["post_process_mask"], False)

    def test_tight_crop_uses_alpha_when_transparent_pixels_retain_rgb(self) -> None:
        alpha = Image.new("L", (100, 80), 0)
        alpha.paste(255, (30, 20, 70, 60))
        image = Image.new("RGBA", alpha.size, (210, 80, 35, 0))
        image.putalpha(alpha)

        cropped = server.tight_crop_alpha(image, pad_pct=0)

        self.assertEqual(cropped.size, (40, 40))
        self.assertEqual(cropped.getchannel("A").getbbox(), (0, 0, 40, 40))

    def test_semantic_mask_preview_and_corrections_are_local_and_durable(self) -> None:
        with self.live_client() as client:
            source = self.import_asset(client, "mask-correction.png", (210, 80, 35))
            payload = {
                "asset_id": source["id"],
                "query": "汉堡",
                "target_count": 1,
                "regions": [{"id": "burger-1", "bbox": [0.05, 0.05, 0.9, 0.9]}],
                "mask_edits": [{
                    "mode": "exclude",
                    "points": [[0.2, 0.2], [0.25, 0.25]],
                    "radius": 0.03,
                }],
            }
            preview = client.post("/api/semantic-cutout/mask-preview", json=payload)
            self.assertEqual(preview.status_code, 200, preview.text)
            mask_preview = preview.json()["mask_preview"]
            self.assertTrue(mask_preview["data_url"].startswith("data:image/png;base64,"))
            self.assertEqual(mask_preview["edit_count"], 1)

            confirmed = client.post("/api/semantic-cutout/confirm", json=payload)
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            selection = confirmed.json()["selection"]
            source_plan = selection["sources"][source["id"]]
            self.assertEqual(len(source_plan["mask_edits"]), 1)

            created = self.create_job(
                client,
                {
                    "mode": "cutout-batch",
                    "source_asset_ids": [source["id"]],
                    "parameters": {"cutout_selection": selection},
                },
            )
            final = self.wait_for_job(created["job"]["id"])
            self.assertEqual(final["status"], "completed")
            traces = client.get(f"/api/jobs/{final['id']}/traces").json()["traces"]
            segment = next(item for item in traces if item["stage"] == "cutout.segment")
            self.assertEqual(segment["parameters"]["mask_edit_count"], 1)
            self.assertEqual(segment["output"]["mask_edit_count"], 1)
            self.assertEqual(self.remove_mock.call_count, 2)
            self.assertEqual(self.ai_mock.call_count, 0)
            self.network_request.assert_not_called()

    def test_semantic_cutout_preview_exposes_editable_candidates_but_never_confirms_them(self) -> None:
        with self.live_client() as client:
            source = self.import_asset(client, "candidate-burgers.png", (210, 80, 35))
            grounding = {
                "status": "candidates",
                "adapter_id": "fixture-grounding",
                "available": True,
                "attempted": True,
                "candidates": [
                    {
                        "id": "candidate-1",
                        "label": "burger",
                        "bbox": [0.05, 0.1, 0.35, 0.7],
                        "origin": "automatic",
                        "confidence": 0.88,
                    },
                    {
                        "id": "candidate-2",
                        "label": "burger",
                        "bbox": [0.55, 0.12, 0.35, 0.68],
                        "origin": "automatic",
                        "confidence": 0.81,
                    },
                ],
                "confidence_threshold": 0.4,
                "elapsed_ms": 18.2,
                "reason": "",
                "message": "本地模型找到 2 个候选，请逐个检查后确认",
            }
            with mock.patch.object(
                server,
                "ground_semantic_candidates",
                return_value=grounding,
            ) as ground_mock:
                preview = client.post(
                    "/api/semantic-cutout/preview",
                    json={
                        "asset_id": source["id"],
                        "query": "汉堡",
                        "target_count": 2,
                        "regions": [],
                    },
                )

            self.assertEqual(preview.status_code, 200, preview.text)
            payload = preview.json()["preview"]
            self.assertEqual(payload["status"], "needs_confirmation")
            self.assertEqual(payload["candidate_status"], "candidates")
            self.assertTrue(payload["automatic_grounding_available"])
            self.assertTrue(payload["requires_confirmation"])
            self.assertEqual(payload["regions"], grounding["candidates"])
            ground_mock.assert_called_once()
            self.assertEqual(ground_mock.call_args.args[1], "hamburger")
            self.assertEqual(ground_mock.call_args.args[2], 2)
            self.assertEqual(payload["query"], "汉堡")
            self.assertEqual(payload["model_query"], "hamburger")
            self.assertEqual(payload["query_mapping"]["status"], "mapped_exact")

            rejected = client.post(
                "/api/jobs",
                json={
                    "mode": "cutout-batch",
                    "source_asset_ids": [source["id"]],
                    "parameters": {
                        "cutout_selection": {
                            "strategy": "semantic",
                            "query": "汉堡",
                            "target_count": 2,
                            "sources": {},
                        }
                    },
                    "client_request_id": "automatic-candidates-not-confirmed",
                },
            )
            self.assertEqual(rejected.status_code, 400, rejected.text)
            self.assertEqual(
                rejected.json()["detail"]["code"],
                "SEMANTIC_CONFIRMATION_REQUIRED",
            )

            confirmed = client.post(
                "/api/semantic-cutout/confirm",
                json={
                    "asset_id": source["id"],
                    "query": "汉堡",
                    "model_query": "hamburger",
                    "target_count": 2,
                    "regions": grounding["candidates"],
                },
            )
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            selection = confirmed.json()["selection"]
            source_plan = selection["sources"][source["id"]]
            self.assertEqual(selection["model_query"], "hamburger")
            self.assertEqual(source_plan["method"], "model-candidate-confirmed")

            created = self.create_job(
                client,
                {
                    "mode": "cutout-batch",
                    "source_asset_ids": [source["id"]],
                    "parameters": {"cutout_selection": selection},
                },
            )
            final = self.wait_for_job(created["job"]["id"])
            self.assertEqual(final["status"], "completed")
            traces = client.get(f"/api/jobs/{final['id']}/traces").json()["traces"]
            segment = next(item for item in traces if item["stage"] == "cutout.segment")
            self.assertEqual(segment["parameters"]["model_query"], "hamburger")
            self.assertTrue(segment["output"]["text_grounding_supported"])
            self.assertTrue(segment["output"]["human_confirmation_required"])
            self.network_request.assert_not_called()

    def test_unknown_chinese_query_does_not_fake_automatic_grounding(self) -> None:
        with self.live_client() as client:
            source = self.import_asset(client, "unknown-object.png", (140, 90, 60))
            with mock.patch.object(
                server,
                "ground_semantic_candidates",
                side_effect=AssertionError("unmapped query must not reach the English model"),
            ) as ground_mock:
                preview = client.post(
                    "/api/semantic-cutout/preview",
                    json={
                        "asset_id": source["id"],
                        "query": "火星纪念摆件",
                        "target_count": 1,
                        "regions": [],
                    },
                )
            self.assertEqual(preview.status_code, 200, preview.text)
            payload = preview.json()["preview"]
            self.assertEqual(payload["candidate_status"], "query_unmapped")
            self.assertEqual(payload["query_mapping"]["status"], "unmapped")
            self.assertEqual(payload["regions"], [])
            self.assertFalse(payload["automatic_grounding_available"])
            self.assertIn("英文识别词", payload["message"])
            ground_mock.assert_not_called()
            self.network_request.assert_not_called()

    def test_semantic_cutout_low_confidence_keeps_manual_confirmation_recovery(self) -> None:
        with self.live_client() as client:
            source = self.import_asset(client, "weak-candidate.png", (180, 100, 40))
            with mock.patch.object(
                server,
                "ground_semantic_candidates",
                return_value={
                    "status": "low_confidence",
                    "adapter_id": "fixture-grounding",
                    "available": True,
                    "attempted": True,
                    "candidates": [],
                    "weak_candidate_count": 1,
                    "confidence_threshold": 0.75,
                    "elapsed_ms": 20.1,
                    "reason": "",
                    "message": "本地模型结果置信度不足，已停止自动预填；请手动框选目标",
                },
            ):
                preview = client.post(
                    "/api/semantic-cutout/preview",
                    json={
                        "asset_id": source["id"],
                        "query": "汉堡",
                        "target_count": 2,
                        "regions": [],
                    },
                )
            payload = preview.json()["preview"]
            self.assertEqual(payload["status"], "needs_manual_grounding")
            self.assertEqual(payload["candidate_status"], "low_confidence")
            self.assertEqual(payload["regions"], [])
            self.assertIn("停止自动预填", payload["message"])
            self.network_request.assert_not_called()

    def test_semantic_cutout_review_suggestions_require_explicit_adoption(self) -> None:
        with self.live_client() as client:
            source = self.import_asset(client, "review-candidate.png", (180, 100, 40))
            suggestion = {
                "id": "candidate-1",
                "label": "burger",
                "bbox": [0.1, 0.1, 0.7, 0.7],
                "origin": "automatic-review",
                "confidence": 0.66,
            }
            with mock.patch.object(
                server,
                "ground_semantic_candidates",
                return_value={
                    "status": "low_confidence",
                    "adapter_id": "fixture-grounding",
                    "available": True,
                    "attempted": True,
                    "candidates": [],
                    "review_candidates": [suggestion],
                    "review_candidate_count": 1,
                    "weak_candidate_count": 0,
                    "confidence_threshold": 0.75,
                    "review_confidence_threshold": 0.6,
                    "elapsed_ms": 20.1,
                    "reason": "",
                    "message": "找到 1 个待确认建议，尚未自动选中；请逐个采用或手动框选",
                },
            ):
                preview = client.post(
                    "/api/semantic-cutout/preview",
                    json={
                        "asset_id": source["id"],
                        "query": "汉堡",
                        "target_count": 1,
                        "regions": [],
                    },
                )
            payload = preview.json()["preview"]
            self.assertEqual(payload["status"], "needs_review")
            self.assertEqual(payload["regions"], [])
            self.assertEqual(payload["suggested_regions"], [suggestion])
            self.assertTrue(payload["requires_confirmation"])
            self.network_request.assert_not_called()

    def test_semantic_cutout_restored_regions_skip_optional_model_inference(self) -> None:
        with self.live_client() as client:
            source = self.import_asset(client, "restored-region.png", (120, 90, 40))
            with mock.patch.object(
                server,
                "ground_semantic_candidates",
                side_effect=AssertionError("restored manual regions must not invoke grounding"),
            ) as ground_mock:
                preview = client.post(
                    "/api/semantic-cutout/preview",
                    json={
                        "asset_id": source["id"],
                        "query": "包装盒",
                        "target_count": 1,
                        "regions": [{
                            "id": "target-1",
                            "label": "包装盒",
                            "bbox": [0.1, 0.1, 0.8, 0.8],
                        }],
                    },
                )
            self.assertEqual(preview.status_code, 200, preview.text)
            payload = preview.json()["preview"]
            self.assertEqual(payload["candidate_status"], "manual_regions")
            self.assertFalse(payload["automatic_grounding_available"])
            self.assertEqual(payload["status"], "needs_confirmation")
            ground_mock.assert_not_called()
            self.network_request.assert_not_called()

    def test_multi_file_partial_failure_retry_only_failed_item_then_completes(self) -> None:
        self.fail_prompt_once = "fail-source"
        with self.live_client() as client:
            ok_source = self.import_asset(client, "ok-source.png", (40, 110, 180))
            fail_source = self.import_asset(client, "fail-source.png", (190, 50, 80))
            created = self.create_job(
                client,
                {
                    "mode": "multi-file",
                    "source_asset_ids": [ok_source["id"], fail_source["id"]],
                    "parameters": {"variations": 1, "model": "offline-model"},
                    "requested_concurrency": 2,
                    "max_attempts": 1,
                },
            )
            partial = self.wait_for_job(created["job"]["id"])

            self.assertEqual(partial["status"], "partial")
            self.assertEqual(partial["total_items"], 2)
            self.assertEqual(partial["completed_items"], 1)
            self.assertEqual(partial["failed_items"], 1)
            failed_item = next(item for item in partial["items"] if item["status"] == "failed")
            completed_item = next(
                item for item in partial["items"] if item["status"] == "completed"
            )
            self.assertEqual(failed_item["source_asset_id"], fail_source["id"])
            self.assertEqual(failed_item["error_code"], "OFFLINE_INJECTED_FAILURE")
            self.assertEqual(failed_item["result_asset_ids"], [])
            self.assertEqual(len(completed_item["result_asset_ids"]), 2)

            retry = client.post(
                f"/api/jobs/{partial['id']}/retry",
                json={"item_ids": [failed_item["id"]]},
            )
            self.assertEqual(retry.status_code, 200, retry.text)
            self.assertEqual(retry.json()["job"]["retried_item_ids"], [failed_item["id"]])
            final = self.wait_for_job(partial["id"])

            self.assertEqual(final["status"], "completed")
            retried = next(item for item in final["items"] if item["id"] == failed_item["id"])
            untouched = next(item for item in final["items"] if item["id"] == completed_item["id"])
            self.assertEqual(retried["attempt_count"], 2)
            self.assertEqual(len(retried["attempts"]), 2)
            self.assertEqual([attempt["status"] for attempt in retried["attempts"]], ["failed", "completed"])
            self.assertEqual(untouched["attempt_count"], 1)
            self.assertEqual(len(untouched["attempts"]), 1)
            self.assert_result_lineage(
                client,
                final,
                {ok_source["id"]: 2, fail_source["id"]: 2},
            )
            self.network_request.assert_not_called()

    def test_cancel_endpoint_cancels_running_adapter_without_publishing_results(self) -> None:
        self.ai_started = threading.Event()
        self.ai_release = threading.Event()
        with self.live_client() as client:
            source = self.import_asset(client, "cancel.png", (120, 60, 200))
            created = self.create_job(
                client,
                {
                    "mode": "single",
                    "source_asset_ids": [source["id"]],
                    "parameters": {"batch": 1, "product_name": "cancel-test"},
                },
            )
            job_id = created["job"]["id"]
            self.assertTrue(self.ai_started.wait(timeout=10))
            canceled = client.post(f"/api/jobs/{job_id}/cancel")
            self.assertEqual(canceled.status_code, 200, canceled.text)
            self.assertEqual(canceled.json()["job"]["status"], "canceling")
            self.ai_release.set()
            final = self.wait_for_job(job_id)

            self.assertEqual(final["status"], "canceled")
            self.assertEqual(final["items"][0]["status"], "canceled")
            self.assertEqual(final["items"][0]["result_asset_ids"], [])
            self.assertEqual(final["items"][0]["attempts"][0]["status"], "canceled")
            self.network_request.assert_not_called()

    def test_create_get_idempotency_replay_and_conflict(self) -> None:
        # No lifespan here: the API transaction must be useful even while the
        # executor is offline, and duplicate requests must not enqueue twice.
        client = TestClient(server.app)
        try:
            source = self.import_asset(client, "idempotent.png", (90, 90, 210))
            payload = {
                "mode": "single",
                "source_asset_ids": [source["id"]],
                "parameters": {"batch": 1, "product_name": "idempotent"},
                "client_request_id": "client-request-42",
            }
            first = client.post("/api/jobs", json=payload)
            replay = client.post("/api/jobs", json=payload)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertTrue(first.json()["created"])
            self.assertFalse(replay.json()["created"])
            self.assertEqual(first.json()["job"]["id"], replay.json()["job"]["id"])
            self.assertEqual(first.json()["job"]["status"], "queued")

            changed = copy.deepcopy(payload)
            changed["parameters"]["batch"] = 2
            conflict = client.post("/api/jobs", json=changed)
            self.assertEqual(conflict.status_code, 409, conflict.text)
            self.assertEqual(conflict.json()["detail"]["code"], "IDEMPOTENCY_CONFLICT")
            self.assertEqual(client.get("/api/jobs").json()["count"], 1)
            self.network_request.assert_not_called()
        finally:
            client.close()

    def test_pause_and_resume_endpoints_keep_queued_item_unclaimed(self) -> None:
        with self.live_client() as client:
            assert server.JOB_ENGINE is not None
            server.JOB_ENGINE.stop()
            source = self.import_asset(client, "paused.png", (31, 71, 121))
            created = self.create_job(client, {
                "mode": "cutout-batch",
                "source_asset_ids": [source["id"]],
                "parameters": {},
            })
            job_id = created["job"]["id"]
            item_id = created["job"]["items"][0]["id"]

            paused = client.post(f"/api/jobs/{job_id}/pause")
            self.assertEqual(paused.status_code, 200, paused.text)
            self.assertEqual(paused.json()["job"]["status"], "paused")
            self.assertIsNone(server.LEDGER.claim_job_item(item_id))

            resumed = client.post(f"/api/jobs/{job_id}/resume")
            self.assertEqual(resumed.status_code, 200, resumed.text)
            self.assertEqual(resumed.json()["job"]["status"], "queued")
            self.assertEqual(resumed.json()["job"]["items"][0]["attempt_count"], 0)

    def test_queued_source_ids_survive_ledger_and_asset_store_restart(self) -> None:
        queued_client = TestClient(server.app)
        try:
            source = self.import_asset(queued_client, "restart.png", (30, 160, 130))
            payload = {
                "mode": "single",
                "source_asset_ids": [source["id"]],
                "parameters": {"batch": 1, "product_name": "restart-source"},
                "client_request_id": "restart-job-1",
            }
            queued_response = queued_client.post("/api/jobs", json=payload)
            self.assertEqual(queued_response.status_code, 200, queued_response.text)
            queued = queued_response.json()["job"]
            self.assertEqual(queued["status"], "queued")
            self.assertEqual(queued["items"][0]["source_asset_id"], source["id"])
        finally:
            queued_client.close()

        # Recreate every persistent service object from disk. The original
        # upload bytes and request object are no longer available.
        server.LEDGER = AtelierLedger(self.db_path)
        server.ASSET_STORE = AssetStore(self.asset_dir, server.LEDGER)
        self.ledger = server.LEDGER
        self.store = server.ASSET_STORE
        with self.live_client() as restarted_client:
            final = self.wait_for_job(queued["id"])
            self.assertEqual(final["status"], "completed")
            self.assertEqual(final["items"][0]["source_asset_id"], source["id"])
            self.assert_result_lineage(restarted_client, final, {source["id"]: 2})
            self.network_request.assert_not_called()

    def test_immediate_adjustment_creates_one_pass_immutable_result_version(self) -> None:
        with self.live_client() as client:
            source = self.import_asset(client, "adjust-source.png", (180, 70, 40))
            parent_created = self.create_job(client, {
                "mode": "single",
                "source_asset_ids": [source["id"]],
                "parameters": {
                    "batch": 1,
                    "product_name": "测试商品",
                    "model": "gpt-image-2",
                    "output_ratio": "original",
                    "output_resolution": "2k",
                },
                "client_request_id": "adjust-parent-job",
            })
            parent = self.wait_for_job(parent_created["job"]["id"])
            parent_item = parent["items"][0]
            parent_main = next(
                asset_id for asset_id in parent_item["result_asset_ids"]
                if self.ledger.get_asset(asset_id)["role"] == "result_main"
            )
            parent_result_ids = list(parent_item["result_asset_ids"])
            payload = {
                "client_request_id": "adjust-result-request-1",
                "result_asset_id": parent_main,
                "generation_id": parent_item["generation_id"],
                "reason_codes": ["包装文字"],
                "note": "只修复包装正面的文字边缘，其他内容保持不变",
            }

            submitted = client.post(
                f"/api/jobs/{parent['id']}/adjustments", json=payload
            )
            self.assertEqual(submitted.status_code, 200, submitted.text)
            body = submitted.json()
            self.assertTrue(body["created"])
            self.assertEqual(body["lineage"]["version"], 2)
            derived_id = body["job"]["id"]
            derived = self.wait_for_job(derived_id)

            replay = client.post(
                f"/api/jobs/{parent['id']}/adjustments", json=payload
            )
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertFalse(replay.json()["created"])
            self.assertEqual(replay.json()["job"]["id"], derived_id)

            self.assertEqual(derived["status"], "completed")
            self.assertEqual(derived["title"], "结果调整 · V2")
            self.assertEqual(derived["parameters"]["batch"], 1)
            self.assertEqual(derived["parameters"]["variations"], 1)
            self.assertEqual(derived["parameters"]["output_ratio"], "original")
            self.assertNotIn("folder_delivery", derived["parameters"])
            self.assertEqual(
                derived["parameters"]["adjustment"]["parent_result_asset_id"],
                parent_main,
            )
            self.assertEqual(len(derived["items"]), 1)
            self.assertEqual(len(derived["items"][0]["result_asset_ids"]), 1)
            adjusted_asset = self.ledger.get_asset(
                derived["items"][0]["result_asset_ids"][0]
            )
            self.assertEqual(adjusted_asset["role"], "result_main")
            self.assertEqual(adjusted_asset["parent_asset_id"], parent_main)
            generation = self.ledger.get_generation(
                derived["items"][0]["generation_id"]
            )
            self.assertEqual(
                generation["parent_generation_id"], parent_item["generation_id"]
            )
            self.assertEqual(
                self.ledger.get_job(parent["id"])["items"][0]["result_asset_ids"],
                parent_result_ids,
            )
            self.assertEqual(len(self.ai_calls), 3)
            self.assertIn("只修复包装正面的文字边缘", self.ai_calls[-1])
            self.assertEqual(self.remove_calls, 1)
            reviews = client.get(f"/api/jobs/{parent['id']}/reviews").json()["reviews"]
            self.assertEqual(len(reviews), 1)
            self.assertEqual(reviews[0]["derived_job_id"], derived_id)
            self.assertEqual(
                reviews[0]["learning_receipt"]["status"], "adjustment_completed"
            )
            self.network_request.assert_not_called()

    def test_legacy_upload_routes_persist_inputs_and_never_spawn_daemon_threads(self) -> None:
        client = TestClient(server.app)
        try:
            with mock.patch.object(
                server, "run_single_task", side_effect=AssertionError("legacy runner used")
            ) as single_runner, mock.patch.object(
                server, "run_multi_task", side_effect=AssertionError("legacy runner used")
            ) as group_runner, mock.patch.object(
                server, "run_multi_file_task", side_effect=AssertionError("legacy runner used")
            ) as multi_runner, mock.patch.object(
                server, "run_cutout_batch_task", side_effect=AssertionError("legacy runner used")
            ) as cutout_runner:
                single = client.post(
                    "/api/single",
                    files={"file": ("single.png", png_bytes((10, 20, 30)), "image/png")},
                    data={"product_name": "offline"},
                )
                group = client.post(
                    "/api/group-split",
                    files={"file": ("group.png", png_bytes((40, 50, 60)), "image/png")},
                )
                multi = client.post(
                    "/api/multi-file",
                    files=[
                        ("files", ("one.png", png_bytes((70, 80, 90)), "image/png")),
                        ("files", ("two.png", png_bytes((90, 80, 70)), "image/png")),
                    ],
                    data={"variations": "1"},
                )
                cutout = client.post(
                    "/api/cutout-batch",
                    files=[
                        ("files", ("cut.png", png_bytes((100, 110, 120)), "image/png")),
                    ],
                )
                retired = client.post(
                    "/api/batch-folder",
                    data={"folder_path": str(self.root)},
                )

            for response in (single, group, multi, cutout):
                self.assertEqual(response.status_code, 200, response.text)
                job = server.LEDGER.get_job(response.json()["job_id"])
                self.assertEqual(job["status"], "queued")
                self.assertTrue(all(item["source_asset_id"] for item in job["items"]))
            self.assertEqual(retired.status_code, 410)
            self.assertEqual(retired.json()["detail"]["code"], "BATCH_FOLDER_RETIRED")
            single_runner.assert_not_called()
            group_runner.assert_not_called()
            multi_runner.assert_not_called()
            cutout_runner.assert_not_called()
            self.assertEqual(server.LEDGER.stats()["counts"]["workspace_assets"], 5)
            self.network_request.assert_not_called()
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
