from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw, ImageFilter

from python.semantic_cutout import SemanticCutoutError
from python.semantic_mask_eval import (
    alpha_mask_metrics,
    audit_mask_correction_recovery,
    evaluate_mask_gates,
)
from tools.evaluate_semantic_masks import _model_path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "semantic_mask_quality" / "manifest.json"


class SemanticMaskEvaluationTests(unittest.TestCase):
    @staticmethod
    def _soft_subject() -> Image.Image:
        alpha = Image.new("L", (120, 100), 0)
        draw = ImageDraw.Draw(alpha)
        draw.ellipse((25, 15, 95, 85), fill=255)
        alpha = alpha.filter(ImageFilter.GaussianBlur(2.2))
        image = Image.new("RGBA", alpha.size, (210, 80, 40, 0))
        image.putalpha(alpha)
        return image

    def test_soft_mask_metrics_are_bounded_to_confirmed_region(self) -> None:
        metrics = alpha_mask_metrics(
            self._soft_subject(),
            [{"id": "target-1", "bbox": [0.1, 0.05, 0.8, 0.9]}],
        )
        self.assertGreater(metrics["nonzero_pixels"], 1000)
        self.assertGreater(metrics["soft_pixels"], 100)
        self.assertGreater(metrics["alpha_level_count"], 8)
        self.assertEqual(metrics["outside_region_nonzero_pixels"], 0)
        self.assertIsNotNone(metrics["alpha_bbox"])

    def test_gate_rejects_a_hard_binary_mask_when_soft_edges_are_required(self) -> None:
        hard = Image.new("RGBA", (80, 80), (20, 40, 60, 255))
        metrics = alpha_mask_metrics(
            hard,
            [{"id": "target-1", "bbox": [0.1, 0.1, 0.8, 0.8]}],
        )
        result = evaluate_mask_gates(metrics, {
            "min_nonzero_pixels": 100,
            "min_soft_pixels": 1,
            "min_alpha_level_count": 3,
            "max_outside_region_nonzero_pixels": 0,
        })
        self.assertFalse(result["passed"])
        self.assertIn("soft_pixels", result["failed_checks"])
        self.assertIn("alpha_levels", result["failed_checks"])

    def test_empty_segmentation_fails_before_it_can_be_reported_as_quality(self) -> None:
        empty = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
        with self.assertRaises(SemanticCutoutError) as failure:
            alpha_mask_metrics(
                empty,
                [{"id": "target-1", "bbox": [0.1, 0.1, 0.8, 0.8]}],
            )
        self.assertEqual(failure.exception.code, "SEMANTIC_SEGMENTATION_EMPTY")

    def test_include_exclude_recovery_is_measured_in_order(self) -> None:
        audit = audit_mask_correction_recovery(
            self._soft_subject(),
            [{"id": "target-1", "bbox": [0.1, 0.05, 0.8, 0.9]}],
        )
        self.assertTrue(audit["passed"])
        self.assertLess(audit["excluded_alpha_sum"], audit["baseline_alpha_sum"])
        self.assertGreater(audit["restored_alpha_sum"], audit["excluded_alpha_sum"])

    def test_manifest_is_explicit_about_its_non_accuracy_claim_scope(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], "1.0")
        self.assertEqual(len(manifest["cases"]), 3)
        self.assertIn("no pixel-level ground-truth", manifest["claim_scope"])
        self.assertGreaterEqual(manifest["gates"]["min_alpha_level_count"], 8)

    def test_evaluator_refuses_to_trigger_an_automatic_model_download(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"U2NET_HOME": directory}, clear=False):
                with self.assertRaisesRegex(FileNotFoundError, "refuses automatic downloads"):
                    _model_path()


if __name__ == "__main__":
    unittest.main()
