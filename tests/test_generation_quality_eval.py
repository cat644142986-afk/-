from __future__ import annotations

import hashlib
import json
import unittest
from copy import deepcopy
from pathlib import Path

from python.generation_quality_eval import (
    SCORE_AXES,
    build_blind_review_packet,
    load_quality_manifest,
    paid_run_gate,
    png_bytes,
    render_procedural_fixture,
    validate_experiment_plan,
    validate_scorecard,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "generation_quality" / "manifest.json"
EXPERIMENT_TEMPLATE = MANIFEST.parent / "experiment-template.json"
SINGLE_PASS_EXPERIMENT_TEMPLATE = MANIFEST.parent / "experiment-single-pass-template.json"


class GenerationQualityEvaluationTests(unittest.TestCase):
    def test_manifest_freezes_all_sources_and_required_coverage(self) -> None:
        manifest = load_quality_manifest(MANIFEST)
        self.assertEqual(len(manifest["cases"]), 9)
        coverage = {
            label
            for case in manifest["cases"]
            for label in case.get("coverage", [])
        }
        required = {
            "food",
            "packaging-text",
            "multi-product",
            "quantity",
            "brand-color",
            "transparent-material",
            "reflective-material",
            "vessel-preservation",
            "truncation-completion",
            "complex-shadow",
        }
        self.assertTrue(required.issubset(coverage))
        for case in manifest["cases"]:
            source = case["source"]
            path = (MANIFEST.parent / source["path"]).resolve()
            self.assertTrue(path.is_file(), case["id"])
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                source["sha256"],
                case["id"],
            )

    def test_procedural_renderer_reproduces_committed_pngs(self) -> None:
        manifest = load_quality_manifest(MANIFEST)
        for case in manifest["cases"]:
            source = case["source"]
            if source["type"] != "procedural":
                continue
            rendered = png_bytes(render_procedural_fixture(source["scene"]))
            self.assertEqual(hashlib.sha256(rendered).hexdigest(), source["sha256"])

    def test_blind_scorecard_requires_every_axis_and_bounded_scores(self) -> None:
        scorecard = {axis: 4 for axis in SCORE_AXES}
        self.assertEqual(validate_scorecard(scorecard)["commercial_usability"], 4.0)
        with self.assertRaisesRegex(ValueError, "axes mismatch"):
            validate_scorecard({"commercial_usability": 4})
        scorecard["edge_quality"] = 5.1
        with self.assertRaisesRegex(ValueError, "between 0 and 5"):
            validate_scorecard(scorecard)

    def test_experiment_template_is_valid_but_cannot_spend(self) -> None:
        plan = json.loads(EXPERIMENT_TEMPLATE.read_text(encoding="utf-8"))
        validated = validate_experiment_plan(plan)
        self.assertEqual(validated["variable_under_test"], "prompt_version")
        self.assertEqual(
            paid_run_gate(plan),
            {"allowed": False, "reason": "user_budget_authorization_required"},
        )

        strategy_plan = json.loads(
            SINGLE_PASS_EXPERIMENT_TEMPLATE.read_text(encoding="utf-8")
        )
        validated_strategy = validate_experiment_plan(strategy_plan)
        self.assertEqual(
            validated_strategy["variable_under_test"], "generation_strategy"
        )
        self.assertEqual(
            paid_run_gate(strategy_plan),
            {"allowed": False, "reason": "user_budget_authorization_required"},
        )

    def test_authorized_paid_gate_stops_at_frozen_call_limit(self) -> None:
        plan = json.loads(EXPERIMENT_TEMPLATE.read_text(encoding="utf-8"))
        plan["status"] = "authorized"
        plan["paid_execution_authorized"] = True
        plan["budget"]["max_paid_calls"] = 18
        plan["budget"]["max_total_amount"] = 50
        plan["budget"]["currency"] = "CNY"
        self.assertEqual(
            paid_run_gate(plan, calls_already_used=7, amount_already_used=17.5),
            {
                "allowed": True,
                "reason": "within_authorized_call_limit",
                "remaining_paid_calls": 11,
                "remaining_amount": 32.5,
                "currency": "CNY",
            },
        )
        self.assertEqual(
            paid_run_gate(plan, calls_already_used=7),
            {"allowed": False, "reason": "actual_billing_total_required"},
        )
        self.assertEqual(
            paid_run_gate(plan, calls_already_used=18, amount_already_used=17.5),
            {"allowed": False, "reason": "paid_call_limit_reached"},
        )
        self.assertEqual(
            paid_run_gate(plan, calls_already_used=7, amount_already_used=50),
            {"allowed": False, "reason": "monetary_budget_reached"},
        )

    def test_blind_packet_is_deterministic_and_hides_variant_identity(self) -> None:
        plan = json.loads(EXPERIMENT_TEMPLATE.read_text(encoding="utf-8"))
        for index, variant in enumerate(plan["variants"]):
            variant["artifact_refs"] = [f"round-1/case-01/{index}.png"]
        first_packet, first_mapping = build_blind_review_packet(plan, seed="fixed-seed")
        second_packet, second_mapping = build_blind_review_packet(plan, seed="fixed-seed")
        self.assertEqual(first_packet, second_packet)
        self.assertEqual(first_mapping, second_mapping)
        public_text = json.dumps(first_packet, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("prompt_v1", public_text)
        self.assertNotIn("prompt_v3", public_text)
        self.assertNotIn("baseline-v1", public_text)
        self.assertNotIn("candidate-v3", public_text)
        private_text = json.dumps(first_mapping, ensure_ascii=False, sort_keys=True)
        self.assertIn("prompt_v1", private_text)
        self.assertIn("prompt_v3", private_text)
        self.assertEqual(len(first_mapping["mapping_sha256"]), 64)

    def test_experiment_rejects_a_variant_that_changes_multiple_variables(self) -> None:
        plan = json.loads(EXPERIMENT_TEMPLATE.read_text(encoding="utf-8"))
        invalid = deepcopy(plan)
        invalid["variants"][1]["changes"]["model"] = "another-model"
        with self.assertRaisesRegex(ValueError, "only variable_under_test"):
            validate_experiment_plan(invalid)


if __name__ == "__main__":
    unittest.main()
