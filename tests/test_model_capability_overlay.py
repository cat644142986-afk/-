import json
import unittest
from pathlib import Path

from python.generation_baseline import PROVIDER_ADAPTER_VERSION
from python.model_capability_overlay import (
    CATALOG_IMAGE_SOURCE,
    CATALOG_V1_BINDING,
    CATALOG_V1_IMAGE_MODEL_IDS,
    OVERLAY_SCHEMA_VERSION,
    TASK_KINDS,
    build_image_capability_overlay,
    model_overlay,
    overlay_definition,
    overlay_sha256,
)


def catalog_fixture(model_ids=CATALOG_V1_IMAGE_MODEL_IDS):
    return {
        "models": [
            {
                "provider_model_id": model_id,
                "display_name": model_id,
                "available_for_this_key": model_id != "wan2.7-image",
                "tags": ["provider-claim"],
                "provider_parameters": [{"name": "prompt", "required": True}],
                "pricing": {"available_for_this_key": True},
                "source_catalogs": [CATALOG_IMAGE_SOURCE],
            }
            for model_id in model_ids
        ]
    }


class ModelCapabilityOverlayTests(unittest.TestCase):
    def test_overlay_definition_contains_only_pa_owned_judgements(self) -> None:
        definition = overlay_definition()
        self.assertEqual(definition["schema_version"], OVERLAY_SCHEMA_VERSION)
        self.assertEqual(definition["task_kinds"], list(TASK_KINDS))
        encoded = json.dumps(definition, ensure_ascii=False)
        for forbidden in (
            '"pricing"', '"balance"', '"available_for_this_key"',
            '"recommended"', '"default_model"',
        ):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(len(overlay_sha256()), 64)

    def test_catalog_v1_is_conservatively_stratified(self) -> None:
        joined = build_image_capability_overlay(
            catalog_fixture(),
            normalized_catalog_sha256=CATALOG_V1_BINDING[
                "normalized_catalog_sha256"
            ],
            catalog_fetched_at=CATALOG_V1_BINDING["fetched_at"],
            catalog_status="fresh",
        )
        self.assertTrue(joined["catalog_binding"]["matches_overlay_v1_source"])
        self.assertEqual(joined["catalog_binding"]["actual_image_model_count"], 19)
        self.assertEqual(
            joined["summary"]["status_counts"],
            {"blocked": 0, "candidate": 18, "experimental": 1, "stable": 0},
        )
        self.assertEqual(
            joined["summary"]["catalog_models_ready_for_composer"], []
        )
        by_id = {
            item["catalog"]["provider_model_id"]: item for item in joined["models"]
        }
        tt = by_id["tt-image-2"]
        self.assertEqual(tt["overlay"]["status"], "experimental")
        self.assertEqual(tt["overlay"]["adapter"]["contract"], "gpt-image-2")
        self.assertEqual(
            tt["overlay"]["adapter"]["version"], PROVIDER_ADAPTER_VERSION
        )
        self.assertEqual(tt["overlay"]["tested_output"]["pairs"], [])
        self.assertFalse(by_id["wan2.7-image"]["catalog"]["available_for_this_key"])
        self.assertEqual(by_id["wan2.7-image"]["overlay"]["status"], "candidate")

    def test_provider_facts_do_not_upgrade_overlay_status(self) -> None:
        joined = build_image_capability_overlay(
            catalog_fixture(["qwen-image"]),
            normalized_catalog_sha256="f" * 64,
            catalog_status="stale",
        )
        self.assertTrue(joined["catalog_binding"]["stale"])
        self.assertFalse(joined["catalog_binding"]["matches_overlay_v1_source"])
        item = joined["models"][0]
        self.assertTrue(item["catalog"]["available_for_this_key"])
        self.assertTrue(item["catalog"]["pricing_available"])
        self.assertEqual(item["overlay"]["status"], "candidate")
        self.assertEqual(item["overlay"]["evidence_level"], "catalog-only")

    def test_new_catalog_model_defaults_to_candidate_not_enabled(self) -> None:
        joined = build_image_capability_overlay(
            catalog_fixture([*CATALOG_V1_IMAGE_MODEL_IDS, "future-image-model"]),
            normalized_catalog_sha256="a" * 64,
        )
        self.assertEqual(
            joined["catalog_binding"]["added_model_ids"], ["future-image-model"]
        )
        future = next(
            item for item in joined["models"]
            if item["catalog"]["provider_model_id"] == "future-image-model"
        )
        self.assertEqual(future["overlay"]["status"], "candidate")
        self.assertEqual(
            future["overlay"]["task_kinds"]["text-to-image"]["status"],
            "blocked",
        )

    def test_legacy_execution_ids_keep_exact_evidence_boundaries(self) -> None:
        gpt = model_overlay("gpt-image-2")
        self.assertEqual(gpt["status"], "stable")
        self.assertEqual(gpt["task_kinds"]["variant"]["status"], "stable")
        self.assertEqual(
            gpt["task_kinds"]["reference-generate"]["status"],
            "experimental",
        )
        self.assertEqual(gpt["tested_output"]["ratios"], ["1:1"])
        self.assertEqual(gpt["tested_output"]["resolutions"], ["2k"])

        gemini = model_overlay("gemini-3.1-flash-image-preview")
        self.assertEqual(gemini["status"], "experimental")
        self.assertEqual(
            gemini["task_kinds"]["edit-current-image"]["evidence_level"],
            "provider-verified",
        )
        self.assertEqual(gemini["tested_output"]["ratios"], ["4:3"])

    def test_tracked_v1_report_is_bound_to_the_runtime_definition(self) -> None:
        root = Path(__file__).resolve().parents[1]
        report = json.loads(
            (root / "docs" / "reports" / "pa-image-capability-overlay-v1.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(report["overlay"]["schema_version"], OVERLAY_SCHEMA_VERSION)
        self.assertEqual(report["overlay"]["sha256"], overlay_sha256())
        self.assertEqual(
            sorted(report["classification"]["experimental"]), ["tt-image-2"]
        )
        self.assertEqual(len(report["classification"]["candidate"]), 18)
        self.assertEqual(
            report["composer_decision"]["new_catalog_models_to_expose_now"], []
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
