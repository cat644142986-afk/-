from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from python import server
from python.generation_quality_eval import load_quality_manifest
from tools.run_generation_prompt_experiment import (
    DEFAULT_CASE_IDS,
    build_prompts,
    case_context,
    run_provider_call,
    validate_prompt_experiment,
)
from tools.run_generation_strategy_experiment import canonical_sha256, load_state


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "tests" / "fixtures" / "generation_quality" / "manifest.json"


def authorized_plan(max_paid_calls: int = 12) -> dict:
    return {
        "schema_version": "1.0",
        "experiment_id": "r7-prompt-v1-v3-unit-test",
        "status": "authorized",
        "paid_execution_authorized": True,
        "variable_under_test": "prompt_version",
        "fixed_parameters": {
            "model": "gpt-image-2",
            "model_snapshot": "provider:gpt-image-2;adapter:lk-media-generate-v1",
            "output_ratio": "1:1",
            "output_resolution": "2k",
            "generation_strategy": "legacy_double_pass",
            "stage_count": 2,
        },
        "variants": [
            {"id": "baseline-v1", "changes": {"prompt_version": "prompt_v1"}},
            {"id": "candidate-v3", "changes": {"prompt_version": "prompt_v3"}},
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


class GenerationPromptExperimentRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_quality_manifest(MANIFEST_PATH)

    def test_plan_freezes_prompt_only_and_exact_twelve_call_limit(self) -> None:
        validated = validate_prompt_experiment(authorized_plan(), self.manifest)
        self.assertEqual(validated["variable_under_test"], "prompt_version")
        with self.assertRaisesRegex(ValueError, "exactly 12"):
            validate_prompt_experiment(authorized_plan(max_paid_calls=13), self.manifest)

    def test_prompt_v3_preserves_frozen_request_count_and_is_shorter(self) -> None:
        case = next(
            item for item in self.manifest["cases"]
            if item["id"] == "multi-count-procedural"
        )
        output_spec = server.resolve_output_spec(
            "gpt-image-2", "1:1", "2k", (640, 640), explicit=True
        )
        v1 = build_prompts(case, "gpt-image-2", "prompt_v1", output_spec)
        v3 = build_prompts(case, "gpt-image-2", "prompt_v3", output_spec)
        context = case_context(case, "gpt-image-2", "prompt_v3", output_spec)

        self.assertEqual(context["product_count"], 3)
        self.assertIn("exactly three bottles", v3["primary_prompt"])
        self.assertIn("产品数量严格保持为3", v3["primary_prompt"])
        self.assertIn("exactly three bottles", v3["refine_prompt"])
        self.assertLess(len(v3["primary_prompt"]), len(v1["primary_prompt"]))
        self.assertEqual(
            v3["prompt_snapshot"]["render_plan_version"],
            "prompt-v3-render-plan-2026-09-01.1",
        )

    def test_provider_result_is_persisted_before_call_is_completed(self) -> None:
        plan = authorized_plan(max_paid_calls=len(DEFAULT_CASE_IDS) * 4)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            result_path = root / "primary.png"
            state = load_state(state_path, canonical_sha256(plan))

            def fake_call(*args, **kwargs):
                kwargs["on_submitted"]("remote-private-id")
                kwargs["on_evidence"]({"completed": True, "timings_ms": {"total": 1.0}})
                return Image.new("RGB", (32, 32), "white")

            with mock.patch.object(server, "ai_i2i", side_effect=fake_call):
                result = run_provider_call(
                    state,
                    state_path,
                    plan,
                    case_id="case-one",
                    stage="baseline-v1:primary",
                    prompt="prompt",
                    negative_prompt="negative",
                    reference=Image.new("RGB", (16, 16), "white"),
                    output_spec={},
                    result_path=result_path,
                )

            self.assertEqual(result.size, (32, 32))
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["calls"][0]["status"], "completed")
            self.assertTrue(result_path.is_file())
            self.assertTrue(persisted["calls"][0]["result_sha256"])


if __name__ == "__main__":
    unittest.main()
