from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from python.generation_quality_eval import (
    SCORE_AXES,
    load_quality_manifest,
    png_bytes,
    render_procedural_fixture,
    validate_scorecard,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "generation_quality" / "manifest.json"


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


if __name__ == "__main__":
    unittest.main()
