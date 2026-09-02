from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from python.asset_store import AssetStore
from python.atelier_ledger import AtelierLedger


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT
    / "python"
    / "video_fixtures"
    / "offline-preview-v1"
    / "16x9"
    / "5s.webm"
)


def image_bytes(image_format: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 18), (196, 87, 54)).save(buffer, image_format)
    return buffer.getvalue()


class VideoJobLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.ledger = AtelierLedger(self.root / "atelier.sqlite3")
        self.asset_store = AssetStore(self.root / "assets", self.ledger)
        self.first_frame = self.asset_store.import_bytes(
            image_bytes(),
            "first-frame.png",
        )
        self.last_frame = self.asset_store.import_bytes(
            image_bytes(),
            "last-frame.png",
        )
        self.output_root = self.root / "outputs"
        self.output_root.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_video_job(self) -> tuple[dict, dict]:
        job, created = self.ledger.create_job(
            "single",
            [self.first_frame["id"]],
            engine_key="local-video-preview",
            parameters={
                "contract_version": "image-to-video-v1",
                "prompt": "镜头缓慢推进，商品包装保持稳定",
                "output_ratio": "16:9",
                "duration_seconds": 5,
                "motion_intensity": 3,
                "first_frame_asset_id": self.first_frame["id"],
                "last_frame_asset_id": self.last_frame["id"],
                "provider": "offline-preview-v1",
                "provider_call_confirmed": False,
                "automatic_paid_retry": False,
                "output_root": str(self.output_root),
            },
            idempotency_key=f"video-ledger-{len(self.ledger.list_jobs())}",
            requested_concurrency=1,
            max_attempts=1,
            title="离线视频预览",
            command_id="command:image-to-video",
        )
        self.assertTrue(created)
        item = job["items"][0]
        self.assertIsNotNone(self.ledger.claim_job_item(item["id"]))
        return job, item

    def output_contract(self) -> tuple[list[dict], bytes, bytes]:
        video_bytes = FIXTURE.read_bytes()
        cover_bytes = image_bytes("JPEG")
        video_path = self.output_root / "preview.webm"
        cover_path = self.output_root / "preview-cover.jpg"
        video_path.write_bytes(video_bytes)
        cover_path.write_bytes(cover_bytes)
        outputs = [
            {
                "path": str(video_path),
                "name": video_path.name,
                "role": "result_video",
                "mime": "video/webm",
                "width": 320,
                "height": 180,
                "sha256": hashlib.sha256(video_bytes).hexdigest(),
                "metadata": {
                    "cover_output_index": 1,
                    "duration_seconds": 5,
                    "size_bytes": len(video_bytes),
                    "provider": "offline-preview-v1",
                    "output_root": str(self.output_root),
                },
            },
            {
                "path": str(cover_path),
                "name": cover_path.name,
                "role": "result_video_cover",
                "mime": "image/jpeg",
                "width": 320,
                "height": 180,
                "sha256": hashlib.sha256(cover_bytes).hexdigest(),
                "metadata": {
                    "auxiliary_result": True,
                    "size_bytes": len(cover_bytes),
                    "output_root": str(self.output_root),
                },
            },
        ]
        return outputs, video_bytes, cover_bytes

    def test_video_and_cover_commit_atomically_with_lineage_and_restart(self) -> None:
        job, item = self.create_video_job()
        outputs, video_bytes, cover_bytes = self.output_contract()

        asset_ids = self.ledger.commit_generation_results(
            item["generation_id"],
            item["source_asset_id"],
            outputs,
            job_item_id=item["id"],
            attempt_metadata={"provider_task_id": "offline-fixture-16x9-5s"},
        )

        self.assertEqual(len(asset_ids), 2)
        video = self.ledger.get_asset(asset_ids[0])
        cover = self.ledger.get_asset(asset_ids[1])
        self.assertEqual(video["role"], "result_video")
        self.assertEqual(video["kind"], "video")
        self.assertEqual(video["mime"], "video/webm")
        self.assertEqual((video["width"], video["height"]), (320, 180))
        self.assertEqual(video["sha256"], hashlib.sha256(video_bytes).hexdigest())
        self.assertEqual(video["metadata"]["size_bytes"], len(video_bytes))
        self.assertEqual(video["metadata"]["duration_seconds"], 5)
        self.assertEqual(video["metadata"]["cover_asset_id"], cover["id"])
        self.assertEqual(video["parent_asset_id"], self.first_frame["id"])
        self.assertEqual(cover["role"], "result_video_cover")
        self.assertEqual(cover["kind"], "image")
        self.assertEqual(cover["mime"], "image/jpeg")
        self.assertEqual(cover["sha256"], hashlib.sha256(cover_bytes).hexdigest())
        self.assertEqual(cover["metadata"]["size_bytes"], len(cover_bytes))
        self.assertEqual(cover["parent_asset_id"], self.first_frame["id"])

        final_job = self.ledger.get_job(job["id"])
        self.assertEqual(final_job["status"], "completed")
        self.assertEqual(final_job["items"][0]["result_asset_ids"], asset_ids)
        attempt = final_job["items"][0]["attempts"][0]
        self.assertEqual(attempt["status"], "completed")
        self.assertEqual(
            attempt["metadata"]["provider_task_id"],
            "offline-fixture-16x9-5s",
        )

        reopened = AtelierLedger(self.ledger.db_path)
        self.assertEqual(reopened.get_asset(video["id"]), video)
        self.assertEqual(reopened.get_asset(cover["id"]), cover)
        last_frame_refs = reopened.asset_reference_summary(
            self.last_frame["id"],
            retention_days=0,
        )
        self.assertIn(job["id"], last_frame_refs["references"]["job_snapshots"])
        self.assertFalse(last_frame_refs["purge_allowed"])

    def test_invalid_video_output_contract_rolls_back_every_asset(self) -> None:
        invalid_mutations = (
            ("video MIME", lambda outputs: outputs[0].update(mime="image/jpeg")),
            ("cover MIME", lambda outputs: outputs[1].update(mime="video/webm")),
            ("duration", lambda outputs: outputs[0]["metadata"].pop("duration_seconds")),
            ("size", lambda outputs: outputs[0]["metadata"].update(size_bytes=1)),
            ("video hash", lambda outputs: outputs[0].update(sha256="0" * 64)),
            ("cover hash", lambda outputs: outputs[1].update(sha256="f" * 64)),
            ("cover reference", lambda outputs: outputs[0]["metadata"].update(cover_output_index=7)),
            (
                "extra main result",
                lambda outputs: outputs.append({
                    **outputs[1],
                    "name": "unexpected-main.jpg",
                    "role": "result_main",
                    "metadata": {},
                }),
            ),
        )
        for label, mutate in invalid_mutations:
            with self.subTest(label=label):
                _job, item = self.create_video_job()
                outputs, _video_bytes, _cover_bytes = self.output_contract()
                mutate(outputs)
                before_assets = self.ledger.stats()["counts"]["assets"]

                with self.assertRaises(ValueError):
                    self.ledger.commit_generation_results(
                        item["generation_id"],
                        item["source_asset_id"],
                        outputs,
                        job_item_id=item["id"],
                    )

                self.assertEqual(
                    self.ledger.stats()["counts"]["assets"],
                    before_assets,
                )
                generation = self.ledger.get_generation(item["generation_id"])
                self.assertEqual(generation["result_asset_ids"], [])
                durable_item = self.ledger.get_job_item(item["id"])
                self.assertEqual(durable_item["status"], "running")
                self.assertEqual(durable_item["result_asset_ids"], [])

    def test_video_job_cannot_requeue_the_original_request(self) -> None:
        job, item = self.create_video_job()
        self.ledger.finish_job_item(
            item["id"],
            "failed",
            error_code="VIDEO_FAILED",
            error_message="return to canvas before retrying",
        )

        with self.assertRaisesRegex(ValueError, "create a new confirmed job"):
            self.ledger.retry_job_items(job["id"])

        unchanged = self.ledger.get_job(job["id"])
        self.assertEqual(unchanged["status"], "failed")
        self.assertEqual(unchanged["items"][0]["attempt_count"], 1)
        self.assertEqual(unchanged["items"][0]["max_attempts"], 1)

    def test_pixel_commands_reject_video_and_auxiliary_cover_sources(self) -> None:
        invalid_sources = (
            self.ledger.add_asset(
                self.first_frame["session_id"],
                "result_video",
                parent_asset_id=self.first_frame["id"],
                path=str(self.output_root / "source-video.webm"),
                name="source-video.webm",
                mime="video/webm",
                kind="video",
                width=320,
                height=180,
                sha256="a" * 64,
            ),
            self.ledger.add_asset(
                self.first_frame["session_id"],
                "result_video_cover",
                parent_asset_id=self.first_frame["id"],
                path=str(self.output_root / "source-cover.jpg"),
                name="source-cover.jpg",
                mime="image/jpeg",
                kind="image",
                width=320,
                height=180,
                sha256="b" * 64,
                metadata={"auxiliary_result": True},
            ),
        )
        commands = (
            ("command:image-to-video", "local-video-preview"),
            ("command:local-edit-generate", "cloud-local-edit"),
        )
        for source in invalid_sources:
            for command_id, engine_key in commands:
                with self.subTest(role=source["role"], command_id=command_id):
                    with self.assertRaisesRegex(ValueError, "pixel-editable image"):
                        self.ledger.create_job(
                            "single",
                            [source["id"]],
                            engine_key=engine_key,
                            parameters={},
                            idempotency_key=f"reject-{command_id}-{source['id']}",
                            max_attempts=1,
                            command_id=command_id,
                        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
