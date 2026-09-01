from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.audit_prompt_v3 import audit_history_rows, build_report


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "generation_quality" / "manifest.json"


class PromptV3AuditTests(unittest.TestCase):
    def test_all_frozen_quality_cases_preserve_their_offline_contract(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        report = build_report(manifest)

        self.assertEqual(report["fixtures"]["sample_count"], 9)
        self.assertEqual(report["fixtures"]["passed_count"], 9)
        self.assertTrue(report["fixtures"]["all_passed"])
        self.assertTrue(report["ready_for_paid_pilot"])
        self.assertFalse(report["privacy"]["images_read"])
        self.assertFalse(report["privacy"]["providers_called"])
        self.assertNotIn("prompt", report["fixtures"]["cases"][0]["primary"])

    def test_history_replay_hashes_identity_and_preserves_request_and_spec(self) -> None:
        rows = [{
            "id": "job-private-id",
            "mode": "single",
            "category": "packaging",
            "parameters_json": json.dumps({
                "model": "gpt-image-2",
                "product_name": "茶盒",
                "platter": "remove",
                "fidelity": 20,
                "output_ratio": "9:16",
                "output_resolution": "4k",
            }),
            "brief_json": json.dumps({
                "user_request": "保留金色文字，只优化背景光线",
                "quantity": 3,
            }),
            "intent_locks_json": json.dumps({
                "subject_shape": True,
                "product_count": True,
                "packaging_text": True,
            }),
        }]

        report = audit_history_rows(rows)

        self.assertTrue(report["all_passed"])
        self.assertEqual(report["sample_count"], 1)
        case = report["cases"][0]
        self.assertNotEqual(case["case_key"], "job-private-id")
        self.assertTrue(case["checks"]["user_request_preserved"])
        self.assertTrue(case["checks"]["product_count_preserved"])
        self.assertTrue(case["checks"]["output_ratio_preserved"])
        self.assertTrue(case["checks"]["output_resolution_preserved"])


if __name__ == "__main__":
    unittest.main()
