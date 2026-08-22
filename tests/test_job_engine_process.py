from __future__ import annotations

import io
import multiprocessing
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Callable

from PIL import Image

from python.asset_store import AssetStore
from python.atelier_ledger import AtelierLedger
from python.job_engine import ExecutionContext, JobEngine


def _png_bytes(index: int) -> bytes:
    buffer = io.BytesIO()
    color = ((index * 41) % 256, (index * 79) % 256, (index * 113) % 256)
    Image.new("RGB", (12, 9), color).save(buffer, "PNG")
    return buffer.getvalue()


def _run_engine_until_terminated(db_path: str, ready_path: str) -> None:
    """Child-process entry point; it intentionally never shuts down cleanly."""
    ledger = AtelierLedger(db_path)
    blocked_forever = threading.Event()

    def processor(context: ExecutionContext) -> dict[str, bool]:
        context.progress(0.4, {"entered_crash_worker": True})
        Path(ready_path).write_text(context.item_id, encoding="utf-8")
        blocked_forever.wait()
        return {"unexpectedly_released": True}

    engine = JobEngine(
        ledger,
        {"mock": processor},
        max_workers=1,
        poll_interval=0.02,
    )
    engine.start()
    blocked_forever.wait()


def _run_contending_engine_until_terminated(
    db_path: str,
    ready_path: str,
    executed_path: str,
) -> None:
    """Start a second live sidecar; it must remain a passive participant."""
    ledger = AtelierLedger(db_path)
    blocked_forever = threading.Event()

    def processor(context: ExecutionContext) -> dict[str, bool]:
        Path(executed_path).write_text(context.item_id, encoding="utf-8")
        return {"duplicate_execution": True}

    engine = JobEngine(
        ledger,
        {"mock": processor},
        max_workers=1,
        poll_interval=0.02,
    )
    engine.start()
    Path(ready_path).write_text("leader" if engine.is_leader else "passive", encoding="utf-8")
    blocked_forever.wait()


def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float,
    description: str,
) -> None:
    deadline = time.monotonic() + timeout
    poll_wakeup = threading.Event()
    while True:
        if predicate():
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for {description}")
        poll_wakeup.wait(timeout=min(0.02, remaining))


class JobEngineProcessRecoveryTests(unittest.TestCase):
    def test_passive_sidecar_takes_over_after_live_leader_is_killed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "atelier.sqlite3"
            leader_ready = root / "leader-ready"
            participant_ready = root / "participant-ready"
            participant_executed = root / "participant-executed"
            ledger = AtelierLedger(db_path)
            asset_store = AssetStore(root / "assets", ledger)
            source = asset_store.import_bytes(_png_bytes(9), "takeover.png")
            job, created = ledger.create_job(
                "multi-file",
                [source["id"]],
                engine_key="mock",
                parameters={"model": "offline-takeover-v1"},
                requested_concurrency=1,
                max_attempts=2,
            )
            self.assertTrue(created)

            context = multiprocessing.get_context("spawn")
            leader = context.Process(
                target=_run_engine_until_terminated,
                args=(str(db_path), str(leader_ready)),
                name="job-engine-takeover-leader",
            )
            participant = context.Process(
                target=_run_contending_engine_until_terminated,
                args=(
                    str(db_path),
                    str(participant_ready),
                    str(participant_executed),
                ),
                name="job-engine-takeover-participant",
            )
            leader.start()
            try:
                _wait_until(
                    leader_ready.exists,
                    timeout=15,
                    description="the leader to claim the item",
                )
                participant.start()
                _wait_until(
                    participant_ready.exists,
                    timeout=15,
                    description="the passive participant to start",
                )
                self.assertEqual(
                    participant_ready.read_text(encoding="utf-8"), "passive"
                )
                self.assertFalse(participant_executed.exists())

                leader.terminate()
                leader.join(timeout=10)
                self.assertFalse(leader.is_alive())
                _wait_until(
                    participant_executed.exists,
                    timeout=15,
                    description="the participant to acquire leadership and execute",
                )

                def takeover_completed() -> bool:
                    return AtelierLedger(db_path).get_job(job["id"])["status"] == "completed"

                _wait_until(
                    takeover_completed,
                    timeout=15,
                    description="the participant to complete recovered work",
                )
                final = AtelierLedger(db_path).get_job(job["id"])
                attempts = final["items"][0]["attempts"]
                self.assertEqual([attempt["status"] for attempt in attempts], [
                    "interrupted", "completed",
                ])
                self.assertEqual([attempt["model"] for attempt in attempts], [
                    "offline-takeover-v1", "offline-takeover-v1",
                ])
            finally:
                for process in (participant, leader):
                    if process.pid is not None and process.is_alive():
                        process.terminate()
                        process.join(timeout=10)

    def test_forced_process_exit_is_recovered_with_attempt_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "atelier.sqlite3"
            ready_path = root / "processor-entered"
            contender_ready_path = root / "contender-ready"
            contender_executed_path = root / "contender-executed"
            ledger = AtelierLedger(db_path)
            asset_store = AssetStore(root / "assets", ledger)
            assets = [
                asset_store.import_bytes(_png_bytes(index), f"source-{index}.png")
                for index in range(2)
            ]
            job, created = ledger.create_job(
                "multi-file",
                [asset["id"] for asset in assets],
                engine_key="mock",
                parameters={"model": "offline-process-recovery-v1"},
                requested_concurrency=1,
                max_attempts=2,
            )
            self.assertTrue(created)
            first_item_id = job["items"][0]["id"]
            second_item_id = job["items"][1]["id"]

            context = multiprocessing.get_context("spawn")
            child = context.Process(
                target=_run_engine_until_terminated,
                args=(str(db_path), str(ready_path)),
                name="job-engine-crash-fixture",
            )
            child.start()
            try:
                def child_claim_is_durable() -> bool:
                    if child.exitcode is not None:
                        self.fail(
                            f"child exited before its job claim was observed: {child.exitcode}"
                        )
                    item = AtelierLedger(db_path).get_job_item(first_item_id)
                    return (
                        ready_path.exists()
                        and item["status"] == "running"
                        and item["attempt_count"] == 1
                        and item["progress"] == 0.4
                    )

                _wait_until(
                    child_claim_is_durable,
                    timeout=15,
                    description="the child process to persist a running attempt",
                )
                self.assertEqual(ready_path.read_text(encoding="utf-8"), first_item_id)
                before_crash = AtelierLedger(db_path).get_job(job["id"])
                self.assertEqual(
                    [item["status"] for item in before_crash["items"]],
                    ["running", "queued"],
                )

                contender = context.Process(
                    target=_run_contending_engine_until_terminated,
                    args=(
                        str(db_path),
                        str(contender_ready_path),
                        str(contender_executed_path),
                    ),
                    name="job-engine-live-contender-fixture",
                )
                contender.start()
                try:
                    _wait_until(
                        contender_ready_path.exists,
                        timeout=15,
                        description="the contending sidecar to start",
                    )
                    self.assertEqual(
                        contender_ready_path.read_text(encoding="utf-8"), "passive"
                    )
                    time.sleep(0.15)
                    self.assertFalse(contender_executed_path.exists())
                    while_leader_alive = AtelierLedger(db_path).get_job(job["id"])
                    self.assertEqual(
                        len(while_leader_alive["items"][0]["attempts"]), 1
                    )
                    self.assertEqual(
                        while_leader_alive["items"][0]["attempts"][0]["status"],
                        "running",
                    )
                finally:
                    if contender.is_alive():
                        contender.terminate()
                        contender.join(timeout=10)

                child.terminate()
                child.join(timeout=10)
                self.assertFalse(child.is_alive(), "terminated child process did not exit")
                self.assertIsNotNone(child.exitcode)
                self.assertNotEqual(child.exitcode, 0)
            finally:
                if child.is_alive():
                    child.terminate()
                    child.join(timeout=10)

            restarted_ledger = AtelierLedger(db_path)

            def successful_processor(context: ExecutionContext) -> dict[str, int | bool]:
                context.progress(0.7, {"restarted_worker": True})
                return {
                    "completed_after_restart": True,
                    "attempt_number": context.item["attempt_count"],
                }

            restarted_engine = JobEngine(
                restarted_ledger,
                {"mock": successful_processor},
                max_workers=1,
                poll_interval=0.02,
            )
            try:
                recovery = restarted_engine.start()
                self.assertEqual(
                    recovery,
                    {"interrupted": 1, "requeued": 1, "failed": 0},
                )
                final = restarted_engine.wait_for_job(job["id"], timeout=15)
            finally:
                restarted_engine.stop()

            self.assertEqual(final["status"], "completed")
            final_by_id = {item["id"]: item for item in final["items"]}
            recovered = final_by_id[first_item_id]
            originally_queued = final_by_id[second_item_id]

            self.assertEqual(recovered["attempt_count"], 2)
            self.assertEqual(
                [attempt["status"] for attempt in recovered["attempts"]],
                ["interrupted", "completed"],
            )
            self.assertEqual(
                recovered["attempts"][0]["error_code"], "PROCESS_RESTARTED"
            )
            self.assertTrue(
                recovered["attempts"][0]["metadata"]["entered_crash_worker"]
            )
            self.assertEqual(originally_queued["attempt_count"], 1)
            self.assertEqual(
                [attempt["status"] for attempt in originally_queued["attempts"]],
                ["completed"],
            )

            transient_statuses = {"running", "canceling", "interrupted"}
            self.assertFalse(
                transient_statuses
                & {final["status"], *(item["status"] for item in final["items"])}
            )
            self.assertNotEqual(
                restarted_ledger.get_session(final["session_id"])["status"],
                "processing",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
