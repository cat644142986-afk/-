from __future__ import annotations

import io
import tempfile
import threading
import unittest
from collections import Counter
from pathlib import Path

from PIL import Image

from python.asset_store import AssetStore
from python.atelier_ledger import AtelierLedger
from python.job_engine import ExecutionContext, JobEngine, JobExecutionError


def fixture_png(index: int) -> bytes:
    buffer = io.BytesIO()
    color = ((index * 29) % 255, (index * 61) % 255, (index * 97) % 255)
    Image.new("RGB", (12, 9), color).save(buffer, "PNG")
    return buffer.getvalue()


class Phase5MultiWorkflowPressureTests(unittest.TestCase):
    def test_ten_cutouts_twenty_batch_items_and_single_stay_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = AtelierLedger(root / "atelier.sqlite3")
            store = AssetStore(root / "assets", ledger)
            products = [
                store.import_bytes(fixture_png(index), f"product-{index:02d}.png", "product")
                for index in range(21)
            ]
            cutouts = [
                store.import_bytes(fixture_png(index + 21), f"cutout-{index:02d}.png", "cutout")
                for index in range(10)
            ]

            single_draft = ledger.save_workflow_draft(
                "single",
                expected_revision=1,
                selected_asset_ids=[products[20]["id"]],
                brief={"user_request": "单产品继续精修"},
                parameters={"batch": 1, "model": "offline-cloud"},
            )
            multi_draft = ledger.save_workflow_draft(
                "multi-file",
                expected_revision=1,
                selected_asset_ids=[asset["id"] for asset in products[:20]],
                brief={"user_request": "二十张独立生成"},
                parameters={"batch": 1, "model": "offline-cloud"},
            )
            cutout_draft = ledger.save_workflow_draft(
                "cutout-batch",
                expected_revision=1,
                selected_asset_ids=[asset["id"] for asset in cutouts],
                brief={"user_request": "十张快速去背景"},
                parameters={"batch": 1, "model": "offline-cutout"},
            )

            multi_job, _ = ledger.create_job(
                "multi-file",
                [asset["id"] for asset in products[:20]],
                engine_key="offline-cloud",
                parameters={"brief": multi_draft["brief"], "model": "offline-cloud"},
                requested_concurrency=4,
                max_attempts=1,
            )
            cutout_job, _ = ledger.create_job(
                "cutout-batch",
                [asset["id"] for asset in cutouts],
                engine_key="offline-cutout",
                parameters={"brief": cutout_draft["brief"], "model": "offline-cutout"},
                requested_concurrency=2,
                max_attempts=1,
            )
            single_job, _ = ledger.create_job(
                "single",
                [products[20]["id"]],
                engine_key="offline-cloud",
                parameters={"brief": single_draft["brief"], "model": "offline-cloud"},
                requested_concurrency=1,
                max_attempts=1,
            )

            release = threading.Event()
            all_workflows_running = threading.Event()
            lock = threading.Lock()
            active = 0
            peak = 0
            active_by_job: Counter[str] = Counter()
            peak_by_job: Counter[str] = Counter()
            modes_seen: set[str] = set()

            def processor(context: ExecutionContext) -> dict:
                nonlocal active, peak
                context.progress(0.35, {"phase": "offline-pressure"})
                with lock:
                    active += 1
                    peak = max(peak, active)
                    active_by_job[context.job_id] += 1
                    peak_by_job[context.job_id] = max(
                        peak_by_job[context.job_id], active_by_job[context.job_id]
                    )
                    modes_seen.add(context.job["mode"])
                    if modes_seen == {"single", "multi-file", "cutout-batch"}:
                        all_workflows_running.set()
                try:
                    if not release.wait(timeout=10):
                        raise JobExecutionError("TEST_TIMEOUT", "pressure fixture was not released")
                finally:
                    with lock:
                        active -= 1
                        active_by_job[context.job_id] -= 1
                return {"processor": "offline", "mode": context.job["mode"]}

            engine = JobEngine(
                ledger,
                {"offline-cloud": processor, "offline-cutout": processor},
                max_workers=7,
                resource_limits={"vlm": 4, "cloud-image": 4, "local-cutout": 2},
                poll_interval=0.02,
            )
            try:
                engine.start()
                self.assertTrue(all_workflows_running.wait(timeout=10))

                # Simulate continuing work while all three jobs remain alive.
                ledger.save_workflow_draft(
                    "multi-file",
                    expected_revision=multi_draft["revision"],
                    selected_asset_ids=[products[19]["id"]],
                    brief={"user_request": "任务运行时继续组织下一批"},
                    parameters={"batch": 2, "model": "offline-cloud"},
                )
                self.assertEqual(
                    ledger.get_job(multi_job["id"])["snapshot"]["source_asset_ids"],
                    [asset["id"] for asset in products[:20]],
                )
                self.assertEqual(
                    ledger.get_job(cutout_job["id"])["snapshot"]["source_asset_ids"],
                    [asset["id"] for asset in cutouts],
                )
                self.assertEqual(
                    ledger.get_job(single_job["id"])["snapshot"]["source_asset_ids"],
                    [products[20]["id"]],
                )

                with lock:
                    self.assertLessEqual(peak, 7)
                    self.assertLessEqual(peak_by_job[multi_job["id"]], 4)
                    self.assertLessEqual(peak_by_job[cutout_job["id"]], 2)
                    self.assertLessEqual(peak_by_job[single_job["id"]], 1)

                release.set()
                final_jobs = [
                    engine.wait_for_job(job["id"], timeout=20)
                    for job in (multi_job, cutout_job, single_job)
                ]
                self.assertEqual([job["status"] for job in final_jobs], ["completed"] * 3)
                self.assertEqual(sum(len(job["items"]) for job in final_jobs), 31)
            finally:
                release.set()
                engine.stop()

            reopened = AtelierLedger(root / "atelier.sqlite3")
            self.assertEqual(
                [reopened.get_job(job["id"])["status"] for job in (multi_job, cutout_job, single_job)],
                ["completed", "completed", "completed"],
            )
            self.assertEqual(len(reopened.get_workflow_draft("cutout-batch")["selected_asset_ids"]), 10)
            self.assertEqual(len(reopened.get_workflow_draft("multi-file")["selected_asset_ids"]), 1)
            self.assertEqual(len(reopened.get_workflow_draft("single")["selected_asset_ids"]), 1)


if __name__ == "__main__":
    unittest.main()
