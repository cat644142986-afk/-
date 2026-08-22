from __future__ import annotations

import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from python.asset_store import AssetStore
from python.atelier_ledger import (
    AtelierLedger,
    IdempotencyConflictError,
    InvalidStatusTransitionError,
    JOB_ITEM_STATUSES,
    JOB_ITEM_STATUS_TRANSITIONS,
    JOB_STATUSES,
    JOB_STATUS_TRANSITIONS,
    validate_status_transition,
)


def png_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (12, 9), color).save(buffer, "PNG")
    return buffer.getvalue()


class JobLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "atelier.sqlite3"
        self.ledger = AtelierLedger(self.db_path)
        self.asset_store = AssetStore(self.root / "assets", self.ledger)
        self.assets = [
            self.asset_store.import_bytes(
                png_bytes((40 + index * 30, 80, 150)),
                f"source-{index}.png",
            )
            for index in range(3)
        ]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @property
    def asset_ids(self) -> list[str]:
        return [asset["id"] for asset in self.assets]

    def create_job(
        self,
        asset_ids: list[str] | None = None,
        *,
        idempotency_key: str = "",
        max_attempts: int = 2,
    ) -> dict:
        job, created = self.ledger.create_job(
            "multi-file",
            asset_ids or self.asset_ids,
            engine_key="mock-cloud",
            parameters={
                "model": "offline-mock-v1",
                "brief": {"goal": "offline ledger test"},
                "knowledge_refs": ["fixture:test"],
            },
            idempotency_key=idempotency_key,
            requested_concurrency=3,
            max_attempts=max_attempts,
            title="Offline job",
        )
        self.assertTrue(created)
        return job

    def test_create_job_persists_asset_inputs_and_survives_reopen(self) -> None:
        job = self.create_job()

        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["total_items"], 3)
        self.assertEqual(job["requested_concurrency"], 3)
        self.assertEqual(job["parameters"]["model"], "offline-mock-v1")
        self.assertEqual(
            [item["source_asset_id"] for item in job["items"]],
            self.asset_ids,
        )
        self.assertEqual([item["position"] for item in job["items"]], [0, 1, 2])
        self.assertTrue(all(item["generation_id"] for item in job["items"]))

        reopened = AtelierLedger(self.db_path).get_job(job["id"])
        self.assertEqual(reopened["session_id"], job["session_id"])
        self.assertEqual(
            [item["source_asset_id"] for item in reopened["items"]],
            self.asset_ids,
        )
        self.assertEqual(reopened["parameters"], job["parameters"])

    def test_concurrent_idempotent_creation_returns_one_durable_job(self) -> None:
        key = "client-request-42"

        def submit(_: int) -> tuple[str, bool]:
            ledger = AtelierLedger(self.db_path)
            job, created = ledger.create_job(
                "multi-file",
                self.asset_ids,
                engine_key="mock-cloud",
                parameters={"model": "offline-mock-v1"},
                idempotency_key=key,
                requested_concurrency=2,
            )
            return job["id"], created

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit, range(16)))

        self.assertEqual(len({job_id for job_id, _ in results}), 1)
        self.assertEqual(sum(1 for _, created in results if created), 1)
        jobs = self.ledger.list_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["idempotency_key"], key)
        self.assertEqual(len(jobs[0]["items"]), 3)

    def test_idempotency_key_reuse_with_different_payload_is_rejected(self) -> None:
        self.create_job(self.asset_ids[:1], idempotency_key="same-key")

        with self.assertRaises(IdempotencyConflictError):
            self.ledger.create_job(
                "cutout-batch",
                self.asset_ids[:1],
                engine_key="local-cutout",
                parameters={"operation": "background-removal"},
                idempotency_key="same-key",
            )

        self.assertEqual(len(self.ledger.list_jobs()), 1)

    def test_claim_is_atomic_and_creates_exactly_one_attempt(self) -> None:
        job = self.create_job(self.asset_ids[:1])
        item_id = job["items"][0]["id"]

        def claim(_: int) -> dict | None:
            return AtelierLedger(self.db_path).claim_job_item(item_id)

        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(claim, range(16)))

        winners = [claim for claim in claims if claim is not None]
        self.assertEqual(len(winners), 1)
        claimed = self.ledger.get_job(job["id"])
        self.assertEqual(claimed["status"], "running")
        self.assertEqual(claimed["items"][0]["status"], "running")
        self.assertEqual(claimed["items"][0]["attempt_count"], 1)
        self.assertEqual(len(claimed["items"][0]["attempts"]), 1)
        self.assertEqual(claimed["items"][0]["attempts"][0]["status"], "running")
        self.assertEqual(claimed["items"][0]["attempts"][0]["model"], "offline-mock-v1")

    def test_claim_enforces_requested_concurrency_inside_database_transaction(self) -> None:
        job, _ = self.ledger.create_job(
            "multi-file",
            self.asset_ids[:2],
            engine_key="mock-cloud",
            requested_concurrency=1,
        )
        first_id, second_id = [item["id"] for item in job["items"]]

        self.assertIsNotNone(self.ledger.claim_job_item(first_id))
        self.assertIsNone(AtelierLedger(self.db_path).claim_job_item(second_id))
        self.ledger.finish_job_item(first_id, "completed")
        self.assertIsNotNone(AtelierLedger(self.db_path).claim_job_item(second_id))

    def test_progress_and_success_update_item_attempt_parent_and_session(self) -> None:
        job = self.create_job(self.asset_ids[:1])
        item_id = job["items"][0]["id"]
        self.ledger.claim_job_item(item_id)

        progress = self.ledger.update_job_item_progress(item_id, 0.375)
        self.assertAlmostEqual(progress["progress"], 0.375)
        clamped = self.ledger.update_job_item_progress(item_id, 4)
        self.assertAlmostEqual(clamped["progress"], 0.999)

        completed = self.ledger.finish_job_item(
            item_id,
            "completed",
            attempt_metadata={"fixture": "offline", "quality": 0.9},
        )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["completed_items"], 1)
        self.assertEqual(completed["failed_items"], 0)
        self.assertEqual(completed["items"][0]["status"], "completed")
        self.assertEqual(completed["items"][0]["progress"], 1.0)
        attempt = completed["items"][0]["attempts"][0]
        self.assertEqual(attempt["status"], "completed")
        self.assertEqual(attempt["metadata"], {"fixture": "offline", "quality": 0.9})
        self.assertIsNotNone(attempt["completed_at"])
        self.assertGreaterEqual(attempt["latency_ms"], 0)
        session = self.ledger.get_session(completed["session_id"])
        self.assertEqual(session["status"], "completed")

    def test_result_publication_atomically_completes_attempt_before_recovery(self) -> None:
        job = self.create_job(self.asset_ids[:1])
        item = job["items"][0]
        self.ledger.claim_job_item(item["id"])
        output_path = self.root / "published-result.png"
        output_path.write_bytes(png_bytes((12, 34, 56)))

        asset_ids = self.ledger.commit_generation_results(
            item["generation_id"],
            item["source_asset_id"],
            [{
                "path": str(output_path),
                "name": output_path.name,
                "role": "result_main",
                "mime": "image/png",
                "width": 12,
                "height": 9,
            }],
            job_item_id=item["id"],
            attempt_metadata={"phase": "publish-boundary"},
        )

        final = self.ledger.get_job(job["id"])
        final_item = final["items"][0]
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final_item["status"], "completed")
        self.assertEqual(final_item["result_asset_ids"], asset_ids)
        self.assertEqual(final_item["attempts"][0]["status"], "completed")
        self.assertEqual(
            final_item["attempts"][0]["metadata"]["phase"],
            "publish-boundary",
        )
        self.assertEqual(
            self.ledger.recover_interrupted_jobs(),
            {"interrupted": 0, "requeued": 0, "failed": 0},
        )
        self.assertEqual(
            self.ledger.get_job(job["id"])["items"][0]["result_asset_ids"],
            asset_ids,
        )

    def test_mixed_success_and_failure_aggregate_to_partial(self) -> None:
        job = self.create_job(self.asset_ids[:2])
        first_id, second_id = [item["id"] for item in job["items"]]
        self.ledger.claim_job_item(first_id)
        self.ledger.claim_job_item(second_id)
        self.ledger.finish_job_item(first_id, "completed")
        partial = self.ledger.finish_job_item(
            second_id,
            "failed",
            error_code="MOCK_FAILURE",
            error_message="deterministic offline failure",
            attempt_metadata={"phase": "mock"},
        )

        self.assertEqual(partial["status"], "partial")
        self.assertEqual(partial["completed_items"], 1)
        self.assertEqual(partial["failed_items"], 1)
        self.assertEqual(partial["canceled_items"], 0)
        failed_item = partial["items"][1]
        self.assertEqual(failed_item["error_code"], "MOCK_FAILURE")
        self.assertEqual(failed_item["progress"], 1.0)
        self.assertEqual(failed_item["attempts"][0]["status"], "failed")
        self.assertEqual(failed_item["attempts"][0]["error_code"], "MOCK_FAILURE")

    def test_all_failures_aggregate_to_failed(self) -> None:
        job = self.create_job(self.asset_ids[:2])
        latest = job
        for item in job["items"]:
            self.ledger.claim_job_item(item["id"])
            latest = self.ledger.finish_job_item(
                item["id"],
                "failed",
                error_code="OFFLINE_FAILURE",
            )

        self.assertEqual(latest["status"], "failed")
        self.assertEqual(latest["failed_items"], 2)
        self.assertEqual(latest["completed_items"], 0)

    def test_cancel_queued_job_is_immediate_and_creates_no_attempts(self) -> None:
        job = self.create_job(self.asset_ids[:2])

        canceled = self.ledger.request_job_cancel(job["id"])

        self.assertEqual(canceled["status"], "canceled")
        self.assertEqual(canceled["canceled_items"], 2)
        self.assertEqual(canceled["canceling_item_ids"], [])
        self.assertTrue(all(item["status"] == "canceled" for item in canceled["items"]))
        self.assertTrue(all(item["progress"] == 1 for item in canceled["items"]))
        self.assertTrue(all(item["attempts"] == [] for item in canceled["items"]))

    def test_pause_stops_new_claims_and_resume_preserves_attempts(self) -> None:
        job = self.create_job(self.asset_ids[:2])
        first_id, second_id = [item["id"] for item in job["items"]]
        self.ledger.claim_job_item(first_id)

        paused = self.ledger.pause_job(job["id"])
        self.assertEqual(paused["status"], "paused")
        self.assertEqual([item["status"] for item in paused["items"]], [
            "running", "queued",
        ])
        self.assertIsNone(self.ledger.claim_job_item(second_id))

        still_paused = self.ledger.finish_job_item(first_id, "completed")
        self.assertEqual(still_paused["status"], "paused")
        self.assertEqual(still_paused["items"][0]["attempt_count"], 1)
        self.assertEqual(still_paused["items"][1]["attempt_count"], 0)

        resumed = self.ledger.resume_job(job["id"])
        self.assertEqual(resumed["status"], "running")
        self.assertIsNotNone(self.ledger.claim_job_item(second_id))
        final = self.ledger.finish_job_item(second_id, "completed")
        self.assertEqual(final["status"], "completed")
        self.assertEqual(
            [item["attempt_count"] for item in final["items"]], [1, 1]
        )

    def test_cancel_running_job_marks_active_item_then_finishes_canceled(self) -> None:
        job = self.create_job(self.asset_ids[:2])
        running_id, queued_id = [item["id"] for item in job["items"]]
        self.ledger.claim_job_item(running_id)

        canceling = self.ledger.request_job_cancel(job["id"])
        states = {item["id"]: item["status"] for item in canceling["items"]}
        self.assertEqual(canceling["status"], "canceling")
        self.assertEqual(canceling["canceling_item_ids"], [running_id])
        self.assertEqual(states[running_id], "canceling")
        self.assertEqual(states[queued_id], "canceled")

        canceled = self.ledger.finish_job_item(
            running_id,
            "canceled",
            error_code="USER_CANCELED",
        )
        self.assertEqual(canceled["status"], "canceled")
        self.assertEqual(canceled["canceled_items"], 2)
        running = next(item for item in canceled["items"] if item["id"] == running_id)
        self.assertEqual(running["attempts"][0]["status"], "canceled")

    def test_retry_reruns_only_failed_item_and_preserves_attempt_history(self) -> None:
        job = self.create_job(self.asset_ids[:2], max_attempts=1)
        succeeded_id, failed_id = [item["id"] for item in job["items"]]
        self.ledger.claim_job_item(succeeded_id)
        self.ledger.finish_job_item(succeeded_id, "completed")
        self.ledger.claim_job_item(failed_id)
        self.ledger.finish_job_item(
            failed_id,
            "failed",
            error_code="FIRST_FAILURE",
            attempt_metadata={"attempt": 1},
        )

        retried = self.ledger.retry_job_items(
            job["id"],
            item_ids=[succeeded_id, failed_id],
        )
        self.assertEqual(retried["retried_item_ids"], [failed_id])
        items = {item["id"]: item for item in retried["items"]}
        self.assertEqual(items[succeeded_id]["status"], "completed")
        self.assertEqual(items[succeeded_id]["attempt_count"], 1)
        self.assertEqual(items[failed_id]["status"], "queued")
        self.assertEqual(items[failed_id]["attempt_count"], 1)
        self.assertEqual(items[failed_id]["error_code"], "")

        self.ledger.claim_job_item(failed_id)
        completed = self.ledger.finish_job_item(
            failed_id,
            "completed",
            attempt_metadata={"attempt": 2},
        )
        final_items = {item["id"]: item for item in completed["items"]}
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(final_items[succeeded_id]["attempt_count"], 1)
        self.assertEqual(final_items[failed_id]["attempt_count"], 2)
        attempts = final_items[failed_id]["attempts"]
        self.assertEqual([attempt["attempt_number"] for attempt in attempts], [1, 2])
        self.assertEqual([attempt["status"] for attempt in attempts], ["failed", "completed"])
        self.assertEqual(attempts[0]["metadata"], {"attempt": 1})
        self.assertEqual(attempts[1]["metadata"], {"attempt": 2})
        self.assertEqual([attempt["model"] for attempt in attempts], [
            "offline-mock-v1", "offline-mock-v1",
        ])

    def test_frozen_status_matrices_accept_exactly_documented_edges(self) -> None:
        expected_jobs = {
            "queued": {"running", "paused", "canceled"},
            "running": {"paused", "completed", "partial", "failed", "canceling", "interrupted"},
            "paused": {
                "queued", "running", "completed", "partial", "failed",
                "canceling", "canceled", "interrupted",
            },
            "canceling": {"canceled", "partial", "failed"},
            "interrupted": {"queued", "running", "partial", "failed", "canceled"},
            "partial": {"running", "completed", "failed", "canceled"},
            "failed": {"queued", "running"},
            "completed": set(),
            "canceled": set(),
        }
        expected_items = {
            "queued": {"running", "canceled"},
            "running": {"completed", "failed", "canceling", "interrupted"},
            "canceling": {"canceled"},
            "interrupted": {"queued", "failed", "canceled"},
            "failed": {"queued"},
            "completed": set(),
            "canceled": set(),
        }
        self.assertEqual(
            {state: set(targets) for state, targets in JOB_STATUS_TRANSITIONS.items()},
            expected_jobs,
        )
        self.assertEqual(
            {state: set(targets) for state, targets in JOB_ITEM_STATUS_TRANSITIONS.items()},
            expected_items,
        )
        for item, statuses, expected in (
            (False, JOB_STATUSES, expected_jobs),
            (True, JOB_ITEM_STATUSES, expected_items),
        ):
            for current in statuses:
                for target in statuses:
                    allowed = target == current or target in expected[current]
                    if allowed:
                        validate_status_transition(current, target, item=item)
                    else:
                        with self.assertRaises(InvalidStatusTransitionError):
                            validate_status_transition(current, target, item=item)

    def test_live_orphan_recovery_commits_running_interrupted_queued_path(self) -> None:
        job = self.create_job(self.asset_ids[:1], max_attempts=2)
        item_id = job["items"][0]["id"]
        self.assertIsNotNone(self.ledger.claim_job_item(item_id))

        recovered = self.ledger.recover_orphaned_job_item(item_id)

        self.assertTrue(recovered["recovered"])
        self.assertEqual(recovered["status"], "queued")
        self.assertEqual(recovered["parent_status"], "queued")
        durable = self.ledger.get_job(job["id"])
        item = durable["items"][0]
        self.assertEqual(durable["status"], "queued")
        self.assertEqual(item["status"], "queued")
        self.assertEqual(item["attempt_count"], 1)
        self.assertEqual(item["attempts"][0]["status"], "interrupted")
        self.assertEqual(
            item["attempts"][0]["error_code"],
            "WORKER_INFRASTRUCTURE_FAILURE",
        )

        self.assertIsNotNone(self.ledger.claim_job_item(item_id))
        final = self.ledger.finish_job_item(item_id, "completed")
        self.assertEqual(final["status"], "completed")
        self.assertEqual(
            [attempt["status"] for attempt in final["items"][0]["attempts"]],
            ["interrupted", "completed"],
        )

    def test_startup_recovery_requeues_retryable_and_fails_exhausted_items(self) -> None:
        retryable = self.create_job(self.asset_ids[:1], max_attempts=2)
        exhausted = self.create_job(self.asset_ids[1:2], max_attempts=1)
        retryable_id = retryable["items"][0]["id"]
        exhausted_id = exhausted["items"][0]["id"]
        self.ledger.claim_job_item(retryable_id)
        self.ledger.update_job_item_progress(retryable_id, 0.6)
        self.ledger.claim_job_item(exhausted_id)
        self.ledger.update_job_item_progress(exhausted_id, 0.7)

        reopened = AtelierLedger(self.db_path)
        recovered_counts = reopened.recover_interrupted_jobs()

        self.assertEqual(
            recovered_counts,
            {"interrupted": 2, "requeued": 1, "failed": 1},
        )
        retryable_after = reopened.get_job(retryable["id"])
        exhausted_after = reopened.get_job(exhausted["id"])
        retry_item = retryable_after["items"][0]
        exhausted_item = exhausted_after["items"][0]
        self.assertEqual(retryable_after["status"], "queued")
        self.assertEqual(retry_item["status"], "queued")
        self.assertEqual(retry_item["progress"], 0)
        self.assertEqual(retry_item["attempts"][0]["status"], "interrupted")
        self.assertEqual(retry_item["attempts"][0]["error_code"], "PROCESS_RESTARTED")
        self.assertEqual(exhausted_after["status"], "failed")
        self.assertEqual(exhausted_item["status"], "failed")
        self.assertAlmostEqual(exhausted_item["progress"], 1.0)
        self.assertEqual(exhausted_item["attempts"][0]["status"], "interrupted")
        second_pass = reopened.recover_interrupted_jobs()
        self.assertEqual(second_pass, {"interrupted": 0, "requeued": 0, "failed": 0})

    def test_startup_recovery_with_completed_sibling_aggregates_to_partial(self) -> None:
        job = self.create_job(self.asset_ids[:2], max_attempts=1)
        completed_id, interrupted_id = [item["id"] for item in job["items"]]
        self.assertIsNotNone(self.ledger.claim_job_item(completed_id))
        self.ledger.finish_job_item(completed_id, "completed")
        self.assertIsNotNone(self.ledger.claim_job_item(interrupted_id))

        recovery = self.ledger.recover_interrupted_jobs()

        self.assertEqual(recovery, {"interrupted": 1, "requeued": 0, "failed": 1})
        final = self.ledger.get_job(job["id"])
        self.assertEqual(final["status"], "partial")
        by_id = {item["id"]: item for item in final["items"]}
        self.assertEqual(by_id[completed_id]["status"], "completed")
        self.assertEqual(by_id[interrupted_id]["status"], "failed")
        self.assertEqual(
            by_id[interrupted_id]["attempts"][0]["status"], "interrupted"
        )

    def test_startup_recovery_converges_canceling_item_to_canceled(self) -> None:
        job = self.create_job(self.asset_ids[:1], max_attempts=2)
        item_id = job["items"][0]["id"]
        self.ledger.claim_job_item(item_id)
        self.ledger.request_job_cancel(job["id"])

        reopened = AtelierLedger(self.db_path)
        counts = reopened.recover_interrupted_jobs()
        recovered = reopened.get_job(job["id"])

        self.assertEqual(counts, {"interrupted": 1, "requeued": 0, "failed": 0})
        self.assertEqual(recovered["status"], "canceled")
        self.assertEqual(recovered["items"][0]["status"], "canceled")
        self.assertEqual(recovered["items"][0]["attempts"][0]["status"], "interrupted")

    def test_illegal_status_transitions_are_rejected_without_mutation(self) -> None:
        job = self.create_job(self.asset_ids[:1])
        item_id = job["items"][0]["id"]

        with self.assertRaises(InvalidStatusTransitionError):
            validate_status_transition("queued", "completed", item=True)
        with self.assertRaises(InvalidStatusTransitionError):
            validate_status_transition("completed", "running", item=True)
        with self.assertRaises(InvalidStatusTransitionError):
            validate_status_transition("completed", "running")
        with self.assertRaises(InvalidStatusTransitionError):
            self.ledger.finish_job_item(item_id, "completed")

        unchanged = self.ledger.get_job(job["id"])
        self.assertEqual(unchanged["status"], "queued")
        self.assertEqual(unchanged["items"][0]["status"], "queued")
        self.assertEqual(unchanged["items"][0]["attempt_count"], 0)
        self.assertEqual(unchanged["items"][0]["attempts"], [])
        rejection_events = [
            event for event in self.ledger.get_session(job["session_id"])["events"]
            if event["event_type"] == "job.transition_rejected"
        ]
        self.assertEqual(len(rejection_events), 1)
        self.assertEqual(rejection_events[0]["payload"]["from"], "queued")
        self.assertEqual(rejection_events[0]["payload"]["to"], "completed")

        self.ledger.claim_job_item(item_id)
        self.ledger.finish_job_item(item_id, "completed")
        with self.assertRaises(InvalidStatusTransitionError):
            self.ledger.finish_job_item(item_id, "failed")
        completed = self.ledger.get_job(job["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["items"][0]["status"], "completed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
