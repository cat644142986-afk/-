from __future__ import annotations

import io
import hashlib
import tempfile
import threading
import time
import unittest
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from python.asset_store import AssetStore
from python.atelier_ledger import AtelierLedger
from python.job_engine import (
    ExecutionContext,
    JobEngine,
    JobExecutionError,
    JobProcessorResult,
)


def png_bytes(index: int) -> bytes:
    buffer = io.BytesIO()
    color = ((index * 37) % 256, (index * 73) % 256, (index * 109) % 256)
    Image.new("RGB", (10, 8), color).save(buffer, "PNG")
    return buffer.getvalue()


class CompletionGateLedger(AtelierLedger):
    """Test ledger that exposes the final completed-write race boundary."""

    def __init__(self, db_path: str | Path) -> None:
        self.completion_write_entered = threading.Event()
        self.allow_completion_write = threading.Event()
        super().__init__(db_path)

    def finish_job_item(self, item_id: str, status: str, **kwargs):  # type: ignore[no-untyped-def]
        if status == "completed":
            self.completion_write_entered.set()
            if not self.allow_completion_write.wait(timeout=10):
                raise TimeoutError("test did not release completed ledger write")
        return super().finish_job_item(item_id, status, **kwargs)


class JobEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "atelier.sqlite3"
        self.ledger = AtelierLedger(self.db_path)
        self.asset_store = AssetStore(self.root / "assets", self.ledger)
        self.assets = [
            self.asset_store.import_bytes(png_bytes(index), f"source-{index}.png")
            for index in range(20)
        ]
        self.engines: list[JobEngine] = []
        self.release_callbacks: list[Callable[[], Any]] = []

    def tearDown(self) -> None:
        for release in self.release_callbacks:
            release()
        for engine in self.engines:
            engine.stop()
        self.temp_dir.cleanup()

    def create_job(
        self,
        asset_indexes: list[int],
        *,
        engine_key: str = "mock",
        requested_concurrency: int = 2,
        max_attempts: int = 1,
    ) -> dict:
        job, created = self.ledger.create_job(
            "multi-file",
            [self.assets[index]["id"] for index in asset_indexes],
            engine_key=engine_key,
            parameters={"model": "offline-fake-v1"},
            requested_concurrency=requested_concurrency,
            max_attempts=max_attempts,
        )
        self.assertTrue(created)
        return job

    def make_engine(
        self,
        processor,
        *,
        ledger: AtelierLedger | None = None,
        max_workers: int = 4,
        resource_limits: dict[str, int] | None = None,
    ) -> JobEngine:
        engine = JobEngine(
            ledger or self.ledger,
            {"mock": processor},
            max_workers=max_workers,
            resource_limits=resource_limits
            or {"vlm": 4, "cloud-image": 4, "local-cutout": 2},
            poll_interval=0.05,
        )
        self.engines.append(engine)
        return engine

    def test_twenty_items_are_parallel_but_global_and_job_bounded(self) -> None:
        first = self.create_job(list(range(16)), requested_concurrency=3)
        second = self.create_job(list(range(16, 20)), requested_concurrency=3)
        release = threading.Event()
        self.release_callbacks.append(release.set)
        four_started = threading.Event()
        monitor_lock = threading.Lock()
        active = 0
        peak = 0
        active_by_job: Counter[str] = Counter()
        peak_by_job: Counter[str] = Counter()

        def processor(context: ExecutionContext) -> dict:
            nonlocal active, peak
            context.progress(0.25, {"phase": "offline-mock"})
            with monitor_lock:
                active += 1
                active_by_job[context.job_id] += 1
                peak = max(peak, active)
                peak_by_job[context.job_id] = max(
                    peak_by_job[context.job_id], active_by_job[context.job_id]
                )
                if active >= 4:
                    four_started.set()
            try:
                if not release.wait(timeout=10):
                    raise JobExecutionError("TEST_TIMEOUT", "release event was not set")
            finally:
                with monitor_lock:
                    active -= 1
                    active_by_job[context.job_id] -= 1
            return {"processor": "offline", "position": context.item["position"]}

        engine = self.make_engine(processor, max_workers=4)
        recovery = engine.start()
        self.assertEqual(recovery, {"interrupted": 0, "requeued": 0, "failed": 0})
        self.assertFalse(engine.coordinator_is_daemon)
        self.assertTrue(four_started.wait(timeout=10))

        running = sum(
            item["status"] == "running"
            for job in (self.ledger.get_job(first["id"]), self.ledger.get_job(second["id"]))
            for item in job["items"]
        )
        self.assertEqual(running, 4)
        with monitor_lock:
            self.assertEqual(peak, 4)
            self.assertLessEqual(peak_by_job[first["id"]], 3)
            self.assertLessEqual(peak_by_job[second["id"]], 3)

        release.set()
        first_done = engine.wait_for_job(first["id"], timeout=15)
        second_done = engine.wait_for_job(second["id"], timeout=15)
        self.assertEqual(first_done["status"], "completed")
        self.assertEqual(second_done["status"], "completed")
        self.assertEqual(
            len(first_done["items"]) + len(second_done["items"]),
            20,
        )
        self.assertTrue(all(item["attempt_count"] == 1 for item in first_done["items"]))
        self.assertTrue(all(item["attempt_count"] == 1 for item in second_done["items"]))

    def test_named_resource_gate_never_exceeds_configured_limit(self) -> None:
        job = self.create_job(list(range(6)), requested_concurrency=6)
        release = threading.Event()
        self.release_callbacks.append(release.set)
        two_inside = threading.Event()
        lock = threading.Lock()
        inside = 0
        observed_peak = 0

        def processor(context: ExecutionContext) -> dict:
            nonlocal inside, observed_peak
            with context.resource("vlm"):
                with lock:
                    inside += 1
                    observed_peak = max(observed_peak, inside)
                    if inside == 2:
                        two_inside.set()
                try:
                    if not release.wait(timeout=10):
                        raise JobExecutionError("TEST_TIMEOUT", "release event was not set")
                finally:
                    with lock:
                        inside -= 1
            return {"resource": "vlm"}

        engine = self.make_engine(
            processor,
            max_workers=6,
            resource_limits={"vlm": 2, "cloud-image": 4, "local-cutout": 1},
        )
        engine.start()
        self.assertTrue(two_inside.wait(timeout=10))
        snapshot = engine.snapshot()
        self.assertEqual(snapshot["resource_in_use"]["vlm"], 2)
        self.assertEqual(snapshot["resource_peak"]["vlm"], 2)
        with lock:
            self.assertEqual(observed_peak, 2)

        release.set()
        final = engine.wait_for_job(job["id"], timeout=15)
        self.assertEqual(final["status"], "completed")
        self.assertEqual(engine.snapshot()["resource_peak"]["vlm"], 2)

    def test_resource_admission_keeps_worker_available_for_local_job(self) -> None:
        cloud_job = self.create_job(
            list(range(4)), engine_key="cloud", requested_concurrency=4
        )
        release_cloud = threading.Event()
        two_cloud_started = threading.Event()
        local_started = threading.Event()
        self.release_callbacks.append(release_cloud.set)
        lock = threading.Lock()
        cloud_inside = 0

        def cloud_processor(context: ExecutionContext) -> dict:
            nonlocal cloud_inside
            with context.resource("cloud-image"):
                with lock:
                    cloud_inside += 1
                    if cloud_inside == 2:
                        two_cloud_started.set()
                try:
                    if not release_cloud.wait(timeout=10):
                        raise JobExecutionError("TEST_TIMEOUT", "cloud release was not set")
                finally:
                    with lock:
                        cloud_inside -= 1
            return {"engine": "cloud"}

        def local_processor(context: ExecutionContext) -> dict:
            with context.resource("local-cutout"):
                local_started.set()
            return {"engine": "local"}

        engine = JobEngine(
            self.ledger,
            {"cloud": cloud_processor, "local": local_processor},
            max_workers=4,
            resource_limits={"vlm": 1, "cloud-image": 2, "local-cutout": 1},
            processor_admission_resources={
                "cloud": "cloud-image",
                "local": "local-cutout",
            },
            poll_interval=0.05,
        )
        self.engines.append(engine)
        engine.start()
        self.assertTrue(two_cloud_started.wait(timeout=10))
        self.assertEqual(engine.snapshot()["job_in_flight"][cloud_job["id"]], 2)
        self.assertEqual(engine.snapshot()["admission_in_use"]["cloud-image"], 2)

        local_job = self.create_job([4], engine_key="local", requested_concurrency=1)
        engine.wake()
        self.assertTrue(
            local_started.wait(timeout=5),
            "local work must start while the cloud gate remains occupied",
        )
        self.assertEqual(
            engine.wait_for_job(local_job["id"], timeout=5)["status"], "completed"
        )
        self.assertFalse(release_cloud.is_set())

        release_cloud.set()
        self.assertEqual(engine.wait_for_job(cloud_job["id"], timeout=10)["status"], "completed")

    def test_second_live_engine_is_passive_and_does_not_recover_owned_work(self) -> None:
        job = self.create_job([0], requested_concurrency=1, max_attempts=2)
        started = threading.Event()
        release = threading.Event()
        calls = 0
        call_lock = threading.Lock()
        self.release_callbacks.append(release.set)

        def processor(context: ExecutionContext) -> dict:
            nonlocal calls
            with call_lock:
                calls += 1
            started.set()
            if not release.wait(timeout=10):
                raise JobExecutionError("TEST_TIMEOUT", "leader release was not set")
            return {"owner": "leader"}

        leader = self.make_engine(processor, max_workers=1)
        leader.start()
        self.assertTrue(started.wait(timeout=10))
        self.assertTrue(leader.is_leader)

        participant = self.make_engine(
            processor,
            ledger=AtelierLedger(self.db_path),
            max_workers=1,
        )
        participant.start()
        self.assertFalse(participant.is_leader)
        time.sleep(0.2)
        live = self.ledger.get_job(job["id"])
        self.assertEqual(live["items"][0]["status"], "running")
        self.assertEqual(len(live["items"][0]["attempts"]), 1)
        self.assertEqual(live["items"][0]["attempts"][0]["status"], "running")
        with call_lock:
            self.assertEqual(calls, 1)

        release.set()
        final = leader.wait_for_job(job["id"], timeout=10)
        self.assertEqual(final["status"], "completed")
        self.assertEqual(len(final["items"][0]["attempts"]), 1)

    def test_transient_scheduler_ledger_error_does_not_kill_leader_loop(self) -> None:
        job = self.create_job([0], requested_concurrency=1)
        original = self.ledger.list_runnable_job_heads
        calls = 0

        def fail_once():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("synthetic scheduler ledger fault")
            return original()

        self.ledger.list_runnable_job_heads = fail_once  # type: ignore[method-assign]
        engine = self.make_engine(lambda _context: {"ok": True}, max_workers=1)
        engine.start()
        final = engine.wait_for_job(job["id"], timeout=10)

        self.assertEqual(final["status"], "completed")
        self.assertGreaterEqual(calls, 2)
        self.assertTrue(engine.is_running)
        self.assertTrue(engine.is_leader)
        self.assertTrue(engine._coordinator is not None and engine._coordinator.is_alive())

    def test_terminal_write_retries_without_rerunning_processor(self) -> None:
        job = self.create_job([0], requested_concurrency=1)
        original = self.ledger.finish_job_item
        finish_calls = 0
        processor_calls = 0

        def fail_twice(*args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal finish_calls
            finish_calls += 1
            if finish_calls <= 2:
                raise RuntimeError("synthetic transient terminal write fault")
            return original(*args, **kwargs)

        def processor(_context: ExecutionContext) -> dict[str, bool]:
            nonlocal processor_calls
            processor_calls += 1
            return {"processor_returned": True}

        self.ledger.finish_job_item = fail_twice  # type: ignore[method-assign]
        engine = self.make_engine(processor, max_workers=1)
        engine.start()
        final = engine.wait_for_job(job["id"], timeout=10)

        self.assertEqual(final["status"], "completed")
        self.assertEqual(processor_calls, 1)
        self.assertEqual(finish_calls, 3)
        self.assertEqual(final["items"][0]["attempts"][0]["status"], "completed")

    def test_persistent_terminal_write_fault_is_reconciled_while_leader_lives(self) -> None:
        job = self.create_job([0], requested_concurrency=1, max_attempts=1)
        processor_calls = 0
        recovery_committed = threading.Event()
        allow_recovery_return = threading.Event()
        self.release_callbacks.append(allow_recovery_return.set)
        original_recover = self.ledger.recover_orphaned_job_item

        def always_fail(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("synthetic persistent terminal write fault")

        def hold_after_recovery_commit(*args, **kwargs):  # type: ignore[no-untyped-def]
            result = original_recover(*args, **kwargs)
            recovery_committed.set()
            if not allow_recovery_return.wait(timeout=10):
                raise TimeoutError("test did not release recovery return")
            return result

        def processor(_context: ExecutionContext) -> dict[str, bool]:
            nonlocal processor_calls
            processor_calls += 1
            return {"processor_returned": True}

        self.ledger.finish_job_item = always_fail  # type: ignore[method-assign]
        self.ledger.recover_orphaned_job_item = (  # type: ignore[method-assign]
            hold_after_recovery_commit
        )
        engine = self.make_engine(processor, max_workers=1)
        engine.start()
        self.assertTrue(recovery_committed.wait(timeout=10))
        self.assertEqual(self.ledger.get_job(job["id"])["status"], "failed")
        self.assertNotEqual(engine.snapshot()["unreconciled_workers"], [])

        with self.assertRaises(
            TimeoutError,
            msg="wait_for_job must not return before reconciliation is complete",
        ):
            engine.wait_for_job(job["id"], timeout=0.1)
        allow_recovery_return.set()
        final = engine.wait_for_job(job["id"], timeout=10)

        self.assertEqual(final["status"], "failed")
        self.assertEqual(processor_calls, 1)
        item = final["items"][0]
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["attempts"][0]["status"], "interrupted")
        self.assertEqual(
            item["attempts"][0]["error_code"],
            "WORKER_INFRASTRUCTURE_FAILURE",
        )
        self.assertEqual(engine.snapshot()["unreconciled_workers"], [])

    def test_durable_commit_is_not_cleaned_up_by_post_commit_read_fault(self) -> None:
        job = self.create_job([0], requested_concurrency=1)
        result_path = self.root / "durable-result.png"
        result_bytes = png_bytes(99)
        cleanup_called = threading.Event()
        committed_ids: list[str] = []
        original_get_item = self.ledger.get_job_item
        read_fault_armed = False
        worker_read_faults = 0

        def fail_worker_read_after_commit(item_id: str):
            nonlocal read_fault_armed, worker_read_faults
            if (
                read_fault_armed
                and threading.current_thread().name.startswith("atelier-job-worker")
            ):
                read_fault_armed = False
                worker_read_faults += 1
                raise RuntimeError("synthetic post-commit verification read fault")
            return original_get_item(item_id)

        self.ledger.get_job_item = fail_worker_read_after_commit  # type: ignore[method-assign]

        def processor(context: ExecutionContext) -> JobProcessorResult:
            def commit() -> dict[str, object]:
                nonlocal read_fault_armed
                result_path.write_bytes(result_bytes)
                committed_ids.extend(self.ledger.commit_generation_results(
                    str(context.item["generation_id"]),
                    str(context.item["source_asset_id"]),
                    [{
                        "path": str(result_path),
                        "name": result_path.name,
                        "role": "result_main",
                        "mime": "image/png",
                        "width": 10,
                        "height": 8,
                        "sha256": hashlib.sha256(result_bytes).hexdigest(),
                    }],
                    job_item_id=context.item_id,
                    attempt_metadata={"durable_test": True},
                ))
                read_fault_armed = True
                return {"result_asset_ids": list(committed_ids)}

            def cleanup() -> None:
                cleanup_called.set()
                self.ledger.discard_generation_results(
                    str(context.item["generation_id"]), committed_ids
                )
                result_path.unlink(missing_ok=True)

            return JobProcessorResult(
                commit=commit,
                cleanup=cleanup,
                durable_completion=True,
            )

        engine = self.make_engine(processor, max_workers=1)
        engine.start()
        final = engine.wait_for_job(job["id"], timeout=10)

        self.assertEqual(final["status"], "completed")
        self.assertFalse(cleanup_called.is_set())
        self.assertEqual(worker_read_faults, 0)
        self.assertEqual(final["items"][0]["result_asset_ids"], committed_ids)
        self.assertTrue(result_path.is_file())

    def test_round_robin_runs_late_small_job_before_large_job_drains(self) -> None:
        large = self.create_job(list(range(4)), requested_concurrency=1)
        permits = threading.Semaphore(0)
        self.release_callbacks.append(lambda: [permits.release() for _ in range(10)])
        first_started = threading.Event()
        second_started = threading.Event()
        lock = threading.Lock()
        order: list[str] = []

        def processor(context: ExecutionContext) -> dict:
            with lock:
                order.append(context.job_id)
                if len(order) == 1:
                    first_started.set()
                elif len(order) == 2:
                    second_started.set()
            if not permits.acquire(timeout=10):
                raise JobExecutionError("TEST_TIMEOUT", "permit was not released")
            return {"order": len(order)}

        engine = self.make_engine(processor, max_workers=1)
        engine.start()
        self.assertTrue(first_started.wait(timeout=10))
        small = self.create_job([4], requested_concurrency=1)
        engine.wake()
        permits.release()
        self.assertTrue(second_started.wait(timeout=10))
        with lock:
            self.assertEqual(order[:2], [large["id"], small["id"]])

        for _ in range(4):
            permits.release()
        self.assertEqual(engine.wait_for_job(small["id"], timeout=10)["status"], "completed")
        self.assertEqual(engine.wait_for_job(large["id"], timeout=10)["status"], "completed")

    def test_pause_allows_running_item_to_settle_and_resume_starts_next(self) -> None:
        job = self.create_job([0, 1], requested_concurrency=1)
        first_started = threading.Event()
        second_started = threading.Event()
        release_first = threading.Event()
        self.release_callbacks.append(release_first.set)

        def processor(context: ExecutionContext) -> dict:
            if int(context.item["position"]) == 0:
                first_started.set()
                if not release_first.wait(timeout=10):
                    raise JobExecutionError("TEST_TIMEOUT", "first item was not released")
            else:
                second_started.set()
            return {"position": context.item["position"]}

        engine = self.make_engine(processor, max_workers=1)
        engine.start()
        self.assertTrue(first_started.wait(timeout=10))
        self.assertEqual(engine.pause_job(job["id"])["status"], "paused")
        release_first.set()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            paused = self.ledger.get_job(job["id"])
            if paused["items"][0]["status"] == "completed":
                break
            time.sleep(0.02)
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["items"][1]["status"], "queued")
        self.assertFalse(second_started.wait(timeout=0.2))

        resumed = engine.resume_job(job["id"])
        self.assertEqual(resumed["status"], "running")
        self.assertTrue(second_started.wait(timeout=5))
        self.assertEqual(engine.wait_for_job(job["id"], timeout=5)["status"], "completed")

    def test_cancel_queued_and_uninterruptible_running_discards_success(self) -> None:
        job = self.create_job(list(range(3)), requested_concurrency=1)
        started = threading.Event()
        release = threading.Event()
        committed = threading.Event()
        cleaned = threading.Event()
        self.release_callbacks.append(release.set)

        def processor(context: ExecutionContext) -> JobProcessorResult:
            started.set()
            if not release.wait(timeout=10):
                raise JobExecutionError("TEST_TIMEOUT", "release event was not set")
            return JobProcessorResult(
                metadata={"late_success": True},
                commit=lambda: committed.set() or {"published": True},
                cleanup=cleaned.set,
            )

        engine = self.make_engine(processor, max_workers=1)
        engine.start()
        self.assertTrue(started.wait(timeout=10))
        canceling = engine.request_cancel(job["id"])
        self.assertEqual(canceling["status"], "canceling")
        self.assertEqual(
            [item["status"] for item in canceling["items"]],
            ["canceling", "canceled", "canceled"],
        )
        release.set()

        final = engine.wait_for_job(job["id"], timeout=10)
        self.assertEqual(final["status"], "canceled")
        self.assertTrue(all(item["status"] == "canceled" for item in final["items"]))
        self.assertFalse(committed.is_set())
        self.assertTrue(cleaned.is_set())
        self.assertEqual(final["items"][0]["attempts"][0]["status"], "canceled")
        self.assertEqual(final["items"][1]["attempts"], [])

    def test_cancel_winning_final_write_race_cleans_committed_artifact(self) -> None:
        gated_ledger = CompletionGateLedger(self.db_path)
        job, created = gated_ledger.create_job(
            "multi-file",
            [self.assets[0]["id"]],
            engine_key="mock",
            parameters={"model": "offline-fake-v1"},
            requested_concurrency=1,
        )
        self.assertTrue(created)
        committed = threading.Event()
        cleaned = threading.Event()
        self.release_callbacks.append(gated_ledger.allow_completion_write.set)

        def processor(context: ExecutionContext) -> JobProcessorResult:
            return JobProcessorResult(
                metadata={"staged": True},
                commit=lambda: committed.set() or {"published": True},
                cleanup=cleaned.set,
            )

        engine = self.make_engine(processor, ledger=gated_ledger, max_workers=1)
        engine.start()
        self.assertTrue(gated_ledger.completion_write_entered.wait(timeout=10))
        self.assertTrue(committed.is_set())
        canceling = engine.request_cancel(job["id"])
        self.assertEqual(canceling["status"], "canceling")
        gated_ledger.allow_completion_write.set()

        final = engine.wait_for_job(job["id"], timeout=10)
        self.assertEqual(final["status"], "canceled")
        self.assertTrue(cleaned.is_set())
        self.assertEqual(final["items"][0]["status"], "canceled")

    def test_failure_isolated_and_retry_runs_only_failed_item(self) -> None:
        job = self.create_job(list(range(3)), requested_concurrency=3)
        lock = threading.Lock()
        calls: Counter[int] = Counter()

        def processor(context: ExecutionContext) -> dict:
            position = int(context.item["position"])
            with lock:
                calls[position] += 1
                call = calls[position]
            context.progress(0.4, {"call": call})
            if position == 1 and call == 1:
                raise JobExecutionError(
                    "MOCK_FAILURE",
                    "deterministic offline failure",
                    metadata={"retryable": True},
                )
            return {"call": call, "position": position}

        engine = self.make_engine(processor, max_workers=3)
        engine.start()
        partial = engine.wait_for_job(job["id"], timeout=10)
        self.assertEqual(partial["status"], "partial")
        failed = next(item for item in partial["items"] if item["status"] == "failed")
        completed_ids = [item["id"] for item in partial["items"] if item["status"] == "completed"]
        retried = engine.retry_failed(
            job["id"], item_ids=[completed_ids[0], failed["id"]]
        )
        self.assertEqual(retried["retried_item_ids"], [failed["id"]])

        final = engine.wait_for_job(job["id"], timeout=10)
        self.assertEqual(final["status"], "completed")
        with lock:
            self.assertEqual(calls, Counter({1: 2, 0: 1, 2: 1}))
        final_failed_item = next(item for item in final["items"] if item["id"] == failed["id"])
        self.assertEqual(final_failed_item["attempt_count"], 2)
        self.assertEqual(
            [attempt["status"] for attempt in final_failed_item["attempts"]],
            ["failed", "completed"],
        )
        self.assertEqual(final_failed_item["attempts"][0]["error_code"], "MOCK_FAILURE")
        self.assertTrue(final_failed_item["attempts"][0]["metadata"]["retryable"])

    def test_start_recovers_interrupted_attempt_and_preserves_queued_work(self) -> None:
        job = self.create_job(list(range(2)), requested_concurrency=1, max_attempts=2)
        interrupted_item = job["items"][0]
        self.ledger.claim_job_item(interrupted_item["id"])
        self.ledger.update_job_item_progress(interrupted_item["id"], 0.6)

        def processor(context: ExecutionContext) -> dict:
            context.progress(0.5, {"recovered": True})
            return {"attempt": context.item["attempt_count"]}

        engine = self.make_engine(processor, max_workers=2)
        recovery = engine.start()
        self.assertEqual(recovery, {"interrupted": 1, "requeued": 1, "failed": 0})
        final = engine.wait_for_job(job["id"], timeout=10)

        self.assertEqual(final["status"], "completed")
        recovered = final["items"][0]
        untouched_queued = final["items"][1]
        self.assertEqual(recovered["attempt_count"], 2)
        self.assertEqual(
            [attempt["status"] for attempt in recovered["attempts"]],
            ["interrupted", "completed"],
        )
        self.assertEqual(recovered["attempts"][0]["error_code"], "PROCESS_RESTARTED")
        self.assertEqual(untouched_queued["attempt_count"], 1)
        self.assertEqual(untouched_queued["attempts"][0]["status"], "completed")
        self.assertNotEqual(
            self.ledger.get_session(final["session_id"])["status"],
            "processing",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
