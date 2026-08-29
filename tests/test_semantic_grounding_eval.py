from __future__ import annotations

import unittest
from pathlib import Path

from python.semantic_grounding_eval import (
    REQUIRED_COVERAGE,
    box_iou,
    evaluate_grounding_predictions,
    load_grounding_manifest,
    render_semantic_fixture,
)


MANIFEST_PATH = (
    Path(__file__).parent / "fixtures" / "semantic_grounding" / "manifest.json"
)
PHOTO_MANIFEST_PATH = (
    Path(__file__).parent / "fixtures" / "semantic_grounding_photos" / "manifest.json"
)


class SemanticGroundingEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_grounding_manifest(MANIFEST_PATH)

    def test_manifest_is_fixed_complete_and_explicitly_not_a_photo_quality_claim(self) -> None:
        self.assertEqual(self.manifest["corpus_kind"], "procedural-contract")
        self.assertEqual(len(self.manifest["cases"]), 8)
        self.assertTrue(REQUIRED_COVERAGE.issubset(set(self.manifest["coverage"])))
        self.assertTrue(any("不能替代真实照片" in item for item in self.manifest["limitations"]))
        self.assertTrue(all(case["model_query_hint"] for case in self.manifest["cases"]))

    def test_every_procedural_case_renders_offline_at_its_declared_size(self) -> None:
        for case in self.manifest["cases"]:
            with self.subTest(case=case["id"]):
                image = render_semantic_fixture(case)
                self.assertEqual(image.size, tuple(case["canvas"]))
                self.assertEqual(image.mode, "RGB")

    def test_licensed_photo_baseline_locks_source_license_hash_and_manual_boxes(self) -> None:
        manifest = load_grounding_manifest(PHOTO_MANIFEST_PATH)
        self.assertEqual(manifest["corpus_kind"], "licensed-photo-baseline")
        self.assertEqual(len(manifest["cases"]), 4)
        for case in manifest["cases"]:
            with self.subTest(case=case["id"]):
                image = case["image"]
                self.assertTrue(image["source_page"].startswith("https://commons.wikimedia.org/"))
                self.assertIn(image["license"], {"CC0", "CC BY-SA 4.0"})
                self.assertEqual(len(image["sha256"]), 64)

    def test_iou_uses_normalized_xywh_boxes(self) -> None:
        self.assertAlmostEqual(box_iou([0.1, 0.1, 0.4, 0.4], [0.1, 0.1, 0.4, 0.4]), 1)
        self.assertEqual(box_iou([0.0, 0.0, 0.2, 0.2], [0.8, 0.8, 0.2, 0.2]), 0)

    def test_perfect_fixture_predictions_pass_every_contract_gate(self) -> None:
        predictions = {}
        for case in self.manifest["cases"]:
            expected = case["expected"]
            predictions[case["id"]] = {
                "status": "candidates" if expected else "no_match",
                "candidates": [
                    {"bbox": item["bbox"], "confidence": 0.9}
                    for item in expected
                ],
                "elapsed_ms": 25,
            }
        report = evaluate_grounding_predictions(self.manifest, predictions)
        self.assertTrue(report["passed"])
        self.assertEqual(report["metrics"]["recall"], 1)
        self.assertEqual(report["metrics"]["precision"], 1)
        self.assertEqual(report["metrics"]["no_match_accuracy"], 1)

    def test_missing_and_failed_predictions_fail_recall_count_and_recovery_gates(self) -> None:
        report = evaluate_grounding_predictions(self.manifest, {
            case["id"]: {
                "status": "failed",
                "candidates": [],
                "elapsed_ms": 10,
            }
            for case in self.manifest["cases"]
        })
        self.assertFalse(report["passed"])
        self.assertFalse(report["checks"]["recall"])
        self.assertFalse(report["checks"]["exact_count_accuracy"])
        self.assertFalse(report["checks"]["recovery_rate"])


if __name__ == "__main__":
    unittest.main()
