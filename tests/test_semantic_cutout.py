from __future__ import annotations

import unittest

from PIL import Image

from python.semantic_cutout import (
    SemanticCutoutError,
    apply_confirmed_regions,
    build_confirmed_selection,
    normalize_cutout_selection,
    validate_selection_sources,
)


class SemanticCutoutContractTests(unittest.TestCase):
    def test_missing_selection_keeps_quick_foreground_contract(self) -> None:
        self.assertEqual(normalize_cutout_selection(None), {"strategy": "foreground"})

    def test_confirmed_manual_regions_are_canonical_and_digest_bound(self) -> None:
        selection = build_confirmed_selection(
            source_asset_id="asset-1",
            query=" 汉堡 ",
            target_count=2,
            regions=[
                {"id": "right", "bbox": [0.55, 0.1, 0.35, 0.7]},
                {"id": "left", "bbox": [0.05, 0.1, 0.35, 0.7]},
            ],
        )
        self.assertEqual(selection["query"], "汉堡")
        self.assertEqual(selection["target_count"], 2)
        self.assertEqual(selection["sources"]["asset-1"]["status"], "confirmed")
        self.assertTrue(selection["sources"]["asset-1"]["digest"].startswith("sha256:"))
        validate_selection_sources(selection, ["asset-1"])

    def test_semantic_selection_rejects_missing_or_wrong_source_confirmation(self) -> None:
        with self.assertRaisesRegex(SemanticCutoutError, "逐张确认") as missing:
            validate_selection_sources(
                {"strategy": "semantic", "query": "汉堡", "target_count": 2, "sources": {}},
                ["asset-1"],
            )
        self.assertEqual(missing.exception.stage, "selection")

    def test_semantic_selection_rejects_count_mismatch(self) -> None:
        with self.assertRaises(SemanticCutoutError) as mismatch:
            build_confirmed_selection(
                source_asset_id="asset-1",
                query="汉堡",
                target_count=2,
                regions=[{"id": "only-one", "bbox": [0.1, 0.1, 0.8, 0.8]}],
            )
        self.assertEqual(mismatch.exception.stage, "selection")
        self.assertEqual(mismatch.exception.code, "SEMANTIC_TARGET_COUNT_MISMATCH")

    def test_confirmed_automatic_candidates_bind_model_query_origin_and_method(self) -> None:
        selection = build_confirmed_selection(
            source_asset_id="asset-1",
            query="汉堡",
            model_query="hamburger",
            target_count=1,
            regions=[{
                "id": "candidate-1",
                "label": "hamburger",
                "bbox": [0.1, 0.1, 0.8, 0.8],
                "origin": "automatic",
                "confidence": 0.91,
            }],
        )
        source = selection["sources"]["asset-1"]
        self.assertEqual(selection["model_query"], "hamburger")
        self.assertEqual(source["method"], "model-candidate-confirmed")
        self.assertEqual(source["regions"][0]["origin"], "automatic")
        self.assertEqual(normalize_cutout_selection(selection), selection)

        stale = {**selection, "model_query": "burger"}
        with self.assertRaises(SemanticCutoutError) as mismatch:
            normalize_cutout_selection(stale)
        self.assertEqual(mismatch.exception.code, "SEMANTIC_CONFIRMATION_STALE")

    def test_explicitly_adopted_review_candidate_remains_auditable(self) -> None:
        selection = build_confirmed_selection(
            source_asset_id="asset-1",
            query="红酒杯",
            model_query="wine glass",
            target_count=1,
            regions=[{
                "id": "candidate-1",
                "label": "wine glass",
                "bbox": [0.1, 0.1, 0.5, 0.8],
                "origin": "automatic-review",
                "confidence": 0.72,
            }],
        )
        source = selection["sources"]["asset-1"]
        self.assertEqual(source["method"], "model-assisted-confirmed")
        self.assertEqual(source["regions"][0]["origin"], "automatic-review")
        self.assertEqual(normalize_cutout_selection(selection), selection)

    def test_confirmed_regions_keep_only_selected_alpha_area(self) -> None:
        source = Image.new("RGBA", (100, 80), (220, 80, 40, 255))
        result = apply_confirmed_regions(
            source,
            [{"id": "target-1", "bbox": [0.1, 0.25, 0.4, 0.5]}],
        )
        alpha = result.getchannel("A")
        self.assertEqual(alpha.getpixel((20, 30)), 255)
        self.assertEqual(alpha.getpixel((80, 30)), 0)
        self.assertEqual(alpha.getbbox(), (10, 20, 50, 60))


if __name__ == "__main__":
    unittest.main()
