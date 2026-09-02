from __future__ import annotations

import asyncio
import hashlib
import io
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from PIL import Image


# Importing server creates module-level singleton state. Keep the first import
# isolated, then replace every mutable runtime singleton in setUp.
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


def png_bytes(color: tuple[int, int, int], size: tuple[int, int] = (96, 64)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


class OfflineVideoJobApiTests(unittest.TestCase):
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
            "JOB_ENGINE": server.JOB_ENGINE,
        }
        server.LEDGER = self.ledger
        server.ASSET_STORE = self.store
        server.ASSET_DIR = self.asset_dir
        server.OUTPUT_DIR = self.output_dir
        server.CONFIG_PATH = self.root / "config.json"
        server._RUNTIME_OUTPUT_ROOT = self.output_dir
        server.JOB_ENGINE = None
        server.save_config({
            "output_root": str(self.output_dir),
            "known_output_roots": [str(self.output_dir)],
        })

        self.release_events: list[threading.Event] = []
        self.patches = [
            mock.patch.object(
                server,
                "api_request",
                side_effect=AssertionError("offline video tests must never call a provider"),
            ),
            mock.patch.object(
                server.requests.sessions.Session,
                "request",
                side_effect=AssertionError("offline video tests must never access the network"),
            ),
        ]
        self.provider_request, self.network_request = [
            patcher.start() for patcher in self.patches
        ]

    def tearDown(self) -> None:
        for event in self.release_events:
            event.set()
        if server.JOB_ENGINE is not None:
            server.JOB_ENGINE.stop()
            server.JOB_ENGINE = None
        for patcher in reversed(self.patches):
            patcher.stop()
        for name, value in self.original_globals.items():
            setattr(server, name, value)
        self.temp_dir.cleanup()

    @contextmanager
    def live_client(self):
        with TestClient(server.app) as client:
            self.assertIsNotNone(server.JOB_ENGINE)
            self.assertTrue(server.JOB_ENGINE.is_running)
            self.assertTrue(server.JOB_ENGINE.is_leader)
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

    def direct_asset(self, name: str, color: tuple[int, int, int]) -> dict:
        return self.store.import_bytes(png_bytes(color), name)

    def video_request(
        self,
        first_frame_id: str,
        last_frame_id: str = "",
        *,
        request_id: str,
        ratio: str = "16:9",
        duration: int = 5,
    ) -> dict:
        canvas = self.ledger.create_spatial_canvas(
            name="视频任务画布",
            client_request_id=f"{request_id}:spatial-canvas",
        )
        return {
            "client_request_id": request_id,
            "source_asset_ids": [first_frame_id],
            "spatial_canvas_id": canvas["id"],
            "parameters": {
                "contract_version": "image-to-video-v1",
                "prompt": "镜头缓慢推进，保持商品包装、文字与颜色稳定",
                "output_ratio": ratio,
                "duration_seconds": duration,
                "motion_intensity": 3,
                "first_frame_asset_id": first_frame_id,
                "last_frame_asset_id": last_frame_id or None,
                "provider": "offline-preview-v1",
                "provider_call_confirmed": False,
                "automatic_paid_retry": False,
                "output_root": str(self.output_dir),
            },
            "requested_concurrency": 1,
            # The command route must freeze this back to one. A failure requires
            # returning to the canvas and creating a newly confirmed request.
            "max_attempts": 4,
        }

    def create_video_job(
        self,
        client: TestClient,
        first_frame_id: str,
        last_frame_id: str = "",
        *,
        request_id: str,
        ratio: str = "16:9",
        duration: int = 5,
    ) -> dict:
        response = client.post(
            "/api/commands/command:image-to-video/execute",
            json=self.video_request(
                first_frame_id,
                last_frame_id,
                request_id=request_id,
                ratio=ratio,
                duration=duration,
            ),
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        return response.json()

    @staticmethod
    def wait_for_job(job_id: str, timeout: float = 15) -> dict:
        engine = server.JOB_ENGINE
        if engine is None:
            raise AssertionError("job engine is not running")
        return engine.wait_for_job(job_id, timeout=timeout)

    def assert_zero_network(self) -> None:
        self.provider_request.assert_not_called()
        self.network_request.assert_not_called()

    def test_offline_video_stream_download_range_thumbnail_and_progress_contract(self) -> None:
        with self.live_client() as client:
            first = self.import_asset(client, "first-frame.png", (210, 86, 48))
            last = self.import_asset(client, "last-frame.png", (48, 104, 210))
            created = self.create_video_job(
                client,
                first["id"],
                last["id"],
                request_id="video-api-complete",
            )
            final = self.wait_for_job(created["job"]["id"])

            self.assertEqual(final["status"], "completed")
            self.assertEqual(final["snapshot"]["command_id"], "command:image-to-video")
            self.assertEqual(
                final["parameters"]["spatial_canvas_id"],
                final["snapshot"]["parameters"]["spatial_canvas_id"],
            )
            self.assertEqual(final["items"][0]["max_attempts"], 1)
            self.assertEqual(final["items"][0]["attempt_count"], 1)
            self.assertEqual(final["items"][0]["attempts"][0]["status"], "completed")
            self.assertFalse(final["parameters"]["provider_call_confirmed"])
            self.assertFalse(final["parameters"]["automatic_paid_retry"])

            assets = [
                self.ledger.get_asset(asset_id)
                for asset_id in final["items"][0]["result_asset_ids"]
            ]
            self.assertEqual(len(assets), 2)
            by_role = {asset["role"]: asset for asset in assets}
            self.assertEqual(set(by_role), {"result_video", "result_video_cover"})
            video = by_role["result_video"]
            cover = by_role["result_video_cover"]
            self.assertEqual(video["metadata"]["cover_asset_id"], cover["id"])
            self.assertTrue(cover["metadata"]["auxiliary_result"])

            progress_response = client.get(f"/api/progress/{final['id']}")
            self.assertEqual(progress_response.status_code, 200, progress_response.text)
            progress = progress_response.json()
            self.assertEqual(progress["status"], "completed")
            self.assertEqual(progress["progress"], 1)
            self.assertEqual(progress["results"]["main"], [])
            self.assertEqual(progress["results"]["cutout"], [])
            self.assertEqual(
                [asset["id"] for asset in progress["results"]["video"]],
                [video["id"]],
            )
            exposed_result_ids = {
                asset["id"]
                for group in progress["results"].values()
                for asset in group
            }
            self.assertNotIn(cover["id"], exposed_result_ids)

            public_response = client.get(f"/api/assets/{video['id']}")
            self.assertEqual(public_response.status_code, 200, public_response.text)
            public = public_response.json()
            self.assertEqual(public["kind"], "video")
            self.assertEqual(public["mime"], "video/webm")
            self.assertEqual((public["width"], public["height"]), (320, 180))
            self.assertEqual(public["duration_seconds"], 5)
            self.assertEqual(public["size_bytes"], video["metadata"]["size_bytes"])
            self.assertEqual(public["cover_asset_id"], cover["id"])
            self.assertEqual(
                public["cover_url"],
                f"/api/assets/{cover['id']}/thumbnail",
            )

            thumbnail = client.get(public["thumbnail_url"], params={"size": 512})
            self.assertEqual(thumbnail.status_code, 200, thumbnail.text)
            self.assertEqual(thumbnail.headers["content-type"], "image/jpeg")
            self.assertEqual(thumbnail.headers["cache-control"], "private, max-age=3600")
            with Image.open(io.BytesIO(thumbnail.content)) as preview:
                self.assertEqual(preview.size, (320, 180))
                preview.verify()

            inline = client.get(public["stream_url"])
            self.assertEqual(inline.status_code, 200, inline.text)
            self.assertEqual(inline.headers["content-type"], "video/webm")
            self.assertTrue(inline.headers["content-disposition"].startswith("inline;"))
            self.assertEqual(hashlib.sha256(inline.content).hexdigest(), public["sha256"])
            self.assertEqual(len(inline.content), public["size_bytes"])

            download = client.get(public["download_url"])
            self.assertEqual(download.status_code, 200, download.text)
            self.assertTrue(download.headers["content-disposition"].startswith("attachment;"))
            self.assertEqual(hashlib.sha256(download.content).hexdigest(), public["sha256"])

            ranged = client.get(
                public["stream_url"],
                headers={"Range": "bytes=0-127"},
            )
            self.assertEqual(ranged.status_code, 206, ranged.text)
            self.assertEqual(ranged.headers["accept-ranges"], "bytes")
            self.assertEqual(
                ranged.headers["content-range"],
                f"bytes 0-127/{public['size_bytes']}",
            )
            self.assertEqual(ranged.content, inline.content[:128])

            traces = client.get(f"/api/jobs/{final['id']}/traces")
            self.assertEqual(traces.status_code, 200, traces.text)
            provider_trace = next(
                item
                for item in traces.json()["traces"]
                if item["stage"] == "provider.video.offline-preview"
            )
            self.assertEqual(provider_trace["status"], "completed")
            self.assertEqual(provider_trace["output"]["network_call_count"], 0)
            self.assertEqual(provider_trace["output"]["video_sha256"], public["sha256"])

            workspace = client.get("/api/workspaces/single")
            self.assertEqual(workspace.status_code, 200, workspace.text)
            projection = workspace.json()
            self.assertNotIn(final["id"], {job["id"] for job in projection["jobs"]})
            self.assertNotIn(final["id"], {job["id"] for job in projection["active_jobs"]})
            self.assertNotIn(
                video["id"],
                {asset["id"] for asset in projection["recent_results"]},
            )
            self.assert_zero_network()

    def test_video_command_requires_an_existing_durable_spatial_canvas(self) -> None:
        with self.live_client() as client:
            first = self.import_asset(client, "binding-first.png", (186, 82, 46))
            missing = self.video_request(
                first["id"],
                request_id="video-api-missing-canvas",
            )
            missing.pop("spatial_canvas_id")
            rejected = client.post(
                "/api/commands/command:image-to-video/execute",
                json=missing,
            )
            self.assertEqual(rejected.status_code, 400, rejected.text)
            self.assertEqual(
                rejected.json()["detail"]["code"],
                "VIDEO_CANVAS_BINDING_INVALID",
            )

            unknown = self.video_request(
                first["id"],
                request_id="video-api-unknown-canvas",
            )
            unknown["spatial_canvas_id"] = "spatial:missing"
            rejected = client.post(
                "/api/commands/command:image-to-video/execute",
                json=unknown,
            )
            self.assertEqual(rejected.status_code, 400, rejected.text)
            self.assertEqual(
                rejected.json()["detail"]["code"],
                "VIDEO_CANVAS_BINDING_INVALID",
            )
            self.assert_zero_network()

    def test_last_frame_snapshot_blocks_permanent_purge(self) -> None:
        with self.live_client() as client:
            first = self.import_asset(client, "purge-first.png", (190, 70, 45))
            last = self.import_asset(client, "purge-last.png", (32, 125, 198))
            created = self.create_video_job(
                client,
                first["id"],
                last["id"],
                request_id="video-api-last-frame-purge",
                ratio="4:3",
                duration=3,
            )
            final = self.wait_for_job(created["job"]["id"])
            self.assertEqual(final["status"], "completed")

            removed = client.delete(
                f"/api/collections/product/assets/{last['id']}"
            )
            self.assertEqual(removed.status_code, 200, removed.text)
            references = client.get(f"/api/assets/{last['id']}/references")
            self.assertEqual(references.status_code, 200, references.text)
            summary = references.json()
            self.assertFalse(summary["purge_allowed"])
            self.assertIn(final["id"], summary["references"]["job_snapshots"])

            purge = client.delete(
                f"/api/trash/assets/{last['id']}",
                params={"confirm_asset_id": last["id"]},
            )
            self.assertEqual(purge.status_code, 409, purge.text)
            self.assertEqual(purge.json()["detail"]["code"], "ASSET_PURGE_BLOCKED")
            self.assertIn(
                final["id"],
                purge.json()["detail"]["summary"]["references"]["job_snapshots"],
            )
            self.assert_zero_network()

    def test_auxiliary_video_cover_cannot_be_reused_as_a_frame(self) -> None:
        with self.live_client() as client:
            first = self.import_asset(client, "cover-source.png", (188, 78, 42))
            created = self.create_video_job(
                client,
                first["id"],
                request_id="video-api-cover-source",
                ratio="1:1",
                duration=3,
            )
            final = self.wait_for_job(created["job"]["id"])
            self.assertEqual(final["status"], "completed")
            cover_id = next(
                asset_id
                for asset_id in final["items"][0]["result_asset_ids"]
                if self.ledger.get_asset(asset_id)["role"] == "result_video_cover"
            )

            rejected = client.post(
                "/api/commands/command:image-to-video/execute",
                json=self.video_request(
                    first["id"],
                    cover_id,
                    request_id="video-api-reject-cover-frame",
                    ratio="1:1",
                    duration=3,
                ),
            )
            self.assertEqual(rejected.status_code, 400, rejected.text)
            self.assertEqual(rejected.json()["detail"]["code"], "VIDEO_FRAME_INVALID")
            self.assert_zero_network()

    def test_workspace_filters_video_commands_before_applying_job_limit(self) -> None:
        source = self.direct_asset("workspace-projection.png", (135, 84, 42))
        quick_job, created = self.ledger.create_job(
            "single",
            [source["id"]],
            engine_key="mock-cloud",
            parameters={"batch": 1},
            idempotency_key="workspace-quick-before-videos",
            max_attempts=1,
            command_id="command:existing-generate-single",
        )
        self.assertTrue(created)
        video_jobs = []
        for index in range(3):
            job, created = self.ledger.create_job(
                "single",
                [source["id"]],
                engine_key="local-video-preview",
                parameters={
                    "first_frame_asset_id": source["id"],
                    "automatic_paid_retry": False,
                },
                idempotency_key=f"workspace-video-{index}",
                max_attempts=1,
                command_id="command:image-to-video",
            )
            self.assertTrue(created)
            video_jobs.append(job)

        connection = self.ledger._connect()
        try:
            connection.execute(
                "UPDATE jobs SET created_at = ? WHERE id = ?",
                ("2026-09-02T01:00:00.000+00:00", quick_job["id"]),
            )
            for index, job in enumerate(video_jobs, start=1):
                connection.execute(
                    "UPDATE jobs SET created_at = ? WHERE id = ?",
                    (f"2026-09-02T01:00:0{index}.000+00:00", job["id"]),
                )
            connection.commit()
        finally:
            connection.close()

        projection = asyncio.run(
            server.get_workflow_workspace("single", asset_limit=20, job_limit=2)
        )
        self.assertEqual([job["id"] for job in projection["jobs"]], [quick_job["id"]])
        self.assertFalse({job["id"] for job in projection["jobs"]} & {
            job["id"] for job in video_jobs
        })

    def test_running_video_job_cancels_without_publishing_partial_results(self) -> None:
        started = threading.Event()
        release = threading.Event()
        self.release_events.append(release)
        real_fixture = server._offline_video_fixture

        def blocking_fixture(parameters):  # type: ignore[no-untyped-def]
            started.set()
            if not release.wait(10):
                raise AssertionError("timed out waiting to cancel video job")
            return real_fixture(parameters)

        with mock.patch.object(
            server,
            "_offline_video_fixture",
            side_effect=blocking_fixture,
        ):
            with self.live_client() as client:
                first = self.import_asset(client, "cancel-first.png", (205, 95, 42))
                created = self.create_video_job(
                    client,
                    first["id"],
                    request_id="video-api-cancel",
                )
                job_id = created["job"]["id"]
                self.assertTrue(started.wait(5), "video worker did not reach fixture stage")

                running = client.get(f"/api/progress/{job_id}")
                self.assertEqual(running.status_code, 200, running.text)
                self.assertEqual(running.json()["status"], "running")
                self.assertGreaterEqual(running.json()["progress"], 0.08)
                self.assertEqual(running.json()["results"]["video"], [])

                workspace = client.get("/api/workspaces/single")
                self.assertEqual(workspace.status_code, 200, workspace.text)
                projection = workspace.json()
                self.assertNotIn(job_id, {job["id"] for job in projection["jobs"]})
                self.assertNotIn(job_id, {job["id"] for job in projection["active_jobs"]})

                canceled = client.post(f"/api/jobs/{job_id}/cancel")
                self.assertEqual(canceled.status_code, 200, canceled.text)
                self.assertEqual(canceled.json()["job"]["status"], "canceling")
                release.set()
                final = self.wait_for_job(job_id)

                self.assertEqual(final["status"], "canceled")
                item = final["items"][0]
                self.assertEqual(item["status"], "canceled")
                self.assertEqual(item["result_asset_ids"], [])
                self.assertEqual(item["attempts"][0]["status"], "canceled")
                self.assertEqual(item["attempts"][0]["error_code"], "USER_CANCELED")
                self.assertEqual(self.ledger.stats()["counts"]["assets"], 1)
                self.assert_zero_network()

    def test_failed_video_job_requires_a_new_confirmed_request(self) -> None:
        real_fixture = server._offline_video_fixture
        fixture_calls = 0

        def fail_once(parameters):  # type: ignore[no-untyped-def]
            nonlocal fixture_calls
            fixture_calls += 1
            if fixture_calls == 1:
                raise JobExecutionError(
                    "VIDEO_OFFLINE_INJECTED_FAILURE",
                    "injected offline video failure",
                )
            return real_fixture(parameters)

        with mock.patch.object(server, "_offline_video_fixture", side_effect=fail_once):
            with self.live_client() as client:
                first = self.import_asset(client, "retry-first.png", (180, 82, 42))
                created = self.create_video_job(
                    client,
                    first["id"],
                    request_id="video-api-explicit-retry",
                    ratio="9:16",
                    duration=8,
                )
                job_id = created["job"]["id"]
                failed = self.wait_for_job(job_id)
                self.assertEqual(failed["status"], "failed")
                self.assertEqual(failed["items"][0]["attempt_count"], 1)
                self.assertEqual(failed["items"][0]["max_attempts"], 1)
                self.assertEqual(failed["items"][0]["result_asset_ids"], [])
                self.assertEqual(fixture_calls, 1)

                retry = client.post(f"/api/jobs/{job_id}/retry", json={})
                self.assertEqual(retry.status_code, 409, retry.text)
                self.assertEqual(
                    retry.json()["detail"]["code"],
                    "VIDEO_RETRY_REQUIRES_NEW_REQUEST",
                )
                unchanged = self.ledger.get_job(job_id)
                self.assertEqual(unchanged["status"], "failed")
                self.assertEqual(unchanged["items"][0]["attempt_count"], 1)

                replacement = self.create_video_job(
                    client,
                    first["id"],
                    request_id="video-api-reconfirmed-replacement",
                    ratio="9:16",
                    duration=8,
                )
                self.assertNotEqual(replacement["job"]["id"], job_id)
                final = self.wait_for_job(replacement["job"]["id"])
                self.assertEqual(final["status"], "completed")
                self.assertEqual(final["items"][0]["attempt_count"], 1)
                self.assertEqual(final["items"][0]["max_attempts"], 1)
                self.assertEqual(final["items"][0]["attempts"][0]["status"], "completed")
                self.assertEqual(len(final["items"][0]["result_asset_ids"]), 2)
                self.assertEqual(fixture_calls, 2)
                self.assert_zero_network()

    def test_startup_recovery_fails_exhausted_video_attempt_without_auto_retry(self) -> None:
        first = self.direct_asset("recovery-first.png", (170, 78, 44))
        last = self.direct_asset("recovery-last.png", (42, 116, 188))
        raw_parameters = self.video_request(
            first["id"],
            last["id"],
            request_id="unused-direct-recovery",
            ratio="3:4",
            duration=10,
        )["parameters"]
        parameters = server.normalize_image_to_video_parameters(
            raw_parameters,
            source_asset_id=first["id"],
        )
        job, created = self.ledger.create_job(
            "single",
            [first["id"]],
            engine_key="local-video-preview",
            parameters=parameters,
            idempotency_key="video-api-recovery",
            requested_concurrency=1,
            max_attempts=1,
            title="离线视频恢复",
            command_id="command:image-to-video",
        )
        self.assertTrue(created)
        item = job["items"][0]
        self.assertIsNotNone(self.ledger.claim_job_item(item["id"]))
        self.ledger.update_job_item_progress(item["id"], 0.44)

        with self.live_client() as client:
            self.assertEqual(
                server.JOB_ENGINE.recovery_result,
                {"interrupted": 1, "requeued": 0, "failed": 1},
            )
            response = client.get(f"/api/jobs/{job['id']}")
            self.assertEqual(response.status_code, 200, response.text)
            recovered = response.json()["job"]
            self.assertEqual(recovered["status"], "failed")
            recovered_item = recovered["items"][0]
            self.assertEqual(recovered_item["status"], "failed")
            self.assertEqual(recovered_item["attempt_count"], 1)
            self.assertEqual(recovered_item["max_attempts"], 1)
            self.assertEqual(recovered_item["result_asset_ids"], [])
            self.assertEqual(recovered_item["error_code"], "PROCESS_RESTARTED")
            self.assertEqual(recovered_item["attempts"][0]["status"], "interrupted")
            self.assertEqual(
                recovered_item["attempts"][0]["error_code"],
                "PROCESS_RESTARTED",
            )
            self.assert_zero_network()


if __name__ == "__main__":
    unittest.main(verbosity=2)
