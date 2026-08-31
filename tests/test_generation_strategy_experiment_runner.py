from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.run_generation_strategy_experiment import (
    canonical_sha256,
    load_state,
    reserve_call,
    trailing_failed_calls,
)


def authorized_plan(max_paid_calls: int = 2) -> dict:
    return {
        "schema_version": "1.0",
        "experiment_id": "runner-unit-test",
        "status": "authorized",
        "paid_execution_authorized": True,
        "variable_under_test": "generation_strategy",
        "fixed_parameters": {},
        "variants": [
            {
                "id": "double",
                "changes": {"generation_strategy": "legacy_double_pass"},
            },
            {
                "id": "single",
                "changes": {"generation_strategy": "single_pass"},
            },
        ],
        "budget": {
            "max_paid_calls": max_paid_calls,
            "max_total_amount": None,
            "currency": None,
        },
        "stop_conditions": {
            "consecutive_failures": 2,
            "unusable_results": 3,
            "stop_on_call_limit": True,
            "stop_on_amount_limit": True,
        },
    }


class GenerationStrategyExperimentRunnerTests(unittest.TestCase):
    def test_reservation_is_atomic_and_enforces_the_paid_call_limit(self) -> None:
        plan = authorized_plan(max_paid_calls=2)
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = load_state(state_path, canonical_sha256(plan))
            reserve_call(state, state_path, plan, case_id="one", stage="primary")
            reserve_call(state, state_path, plan, case_id="one", stage="refine")
            with self.assertRaisesRegex(RuntimeError, "paid_call_limit_reached"):
                reserve_call(state, state_path, plan, case_id="two", stage="primary")
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual([call["call_index"] for call in persisted["calls"]], [1, 2])

    def test_state_rejects_a_different_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(
                json.dumps({"plan_sha256": "old", "calls": [], "artifacts": []}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "different plan"):
                load_state(state_path, "new")

    def test_trailing_failures_reset_after_a_completed_provider_call(self) -> None:
        self.assertEqual(
            trailing_failed_calls(
                {
                    "calls": [
                        {"status": "completed"},
                        {"status": "failed"},
                        {"status": "failed"},
                    ]
                }
            ),
            2,
        )
        self.assertEqual(
            trailing_failed_calls(
                {
                    "calls": [
                        {"status": "failed"},
                        {"status": "completed"},
                    ]
                }
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
