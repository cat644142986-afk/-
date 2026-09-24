import hashlib
import json
import unittest
from pathlib import Path

from python.model_admission import build_composer_admission
from python.model_candidate_validation import (
    CANDIDATE_VALIDATION_SCHEMA_VERSION,
    FROZEN_REFERENCE_SHA256,
    apply_candidate_validation_overlay,
    candidate_validation_sha256,
)
from python.model_capability_overlay import (
    build_image_capability_overlay,
    overlay_sha256,
)
from python.model_identity import (
    CATALOG_IMAGE_SOURCE,
    attach_identity_to_capability_overlay,
    build_model_identity_resolution,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "model_candidate_validation"
    / "reference-generate-v1.json"
)
REFERENCE_PATH = (
    ROOT / "tests" / "fixtures" / "generation_quality" / "generated"
    / "packaging-text-brand.png"
)


def catalog_fixture():
    parameters = {
        "tt-image-2": ["prompt", "images", "size", "quality"],
        "banana-2": ["prompt", "images", "aspectRatio", "imageSize"],
        "banana-pro": ["prompt", "images", "aspectRatio", "imageSize"],
    }
    return {
        "models": [
            {
                "provider_model_id": model_id,
                "display_name": model_id,
                "available_for_this_key": True,
                "modalities": ["image"],
                "source_catalogs": [
                    CATALOG_IMAGE_SOURCE,
                    "v1/models",
                    "v1/skills/models",
                ],
                "provider_parameters": [
                    {"name": name, "required": name in {"prompt", "size", "aspectRatio", "imageSize"}}
                    for name in names
                ],
            }
            for model_id, names in parameters.items()
        ]
    }


class ModelCandidateValidationTests(unittest.TestCase):
    def test_frozen_fixture_and_reference_are_byte_stable(self) -> None:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(fixture["task_kind"], "reference-generate")
        self.assertEqual(fixture["reference"]["semantics"], "exact-single-result")
        self.assertEqual(fixture["reference"]["asset_role"], "result_main")
        self.assertEqual(fixture["execution"]["max_attempts"], 1)
        self.assertFalse(fixture["execution"]["automatic_paid_retry"])
        self.assertFalse(fixture["execution"]["provider_call_confirmed"])
        self.assertEqual(
            hashlib.sha256(REFERENCE_PATH.read_bytes()).hexdigest(),
            FROZEN_REFERENCE_SHA256,
        )

    def test_candidate_evidence_is_additive_and_exact_id_scoped(self) -> None:
        catalog = catalog_fixture()
        base = build_image_capability_overlay(catalog)
        original_hash = base["overlay_sha256"]
        projected = apply_candidate_validation_overlay(base)
        self.assertEqual(original_hash, overlay_sha256())
        self.assertEqual(projected["overlay_sha256"], original_hash)
        self.assertEqual(
            projected["candidate_validation"]["applied_model_ids"],
            ["banana-2", "banana-pro"],
        )
        by_id = {
            item["catalog"]["provider_model_id"]: item
            for item in projected["models"]
        }
        for model_id in ("banana-2", "banana-pro"):
            item = by_id[model_id]
            self.assertEqual(item["overlay"]["adapter"]["contract"], model_id)
            self.assertEqual(
                item["overlay"]["task_kinds"]["reference-generate"]["evidence_level"],
                "adapter-contract",
            )
            self.assertEqual(item["overlay"]["tested_output"]["pairs"], [])
        self.assertNotEqual(
            by_id["banana-2"]["overlay"]["adapter"]["contract"],
            by_id["banana-pro"]["overlay"]["adapter"]["contract"],
        )
        self.assertNotIn("candidate_validation", by_id["tt-image-2"])

    def test_offline_gate_moves_banana_candidates_to_needs_canary_only(self) -> None:
        catalog = catalog_fixture()
        projected = apply_candidate_validation_overlay(
            build_image_capability_overlay(catalog)
        )
        identities = build_model_identity_resolution(catalog)
        capabilities = attach_identity_to_capability_overlay(projected, identities)
        admission = build_composer_admission(capabilities)
        categories = admission["summary"]["categories"]
        self.assertEqual(categories["eligible"], [])
        self.assertEqual(categories["needs_adapter"], [])
        self.assertEqual(
            categories["needs_canary"],
            ["tt-image-2", "banana-2", "banana-pro"],
        )
        by_id = {item["provider_model_id"]: item for item in admission["candidates"]}
        for model_id in ("banana-2", "banana-pro"):
            self.assertTrue(by_id[model_id]["checks"]["adapter_contract"]["passed"])
            self.assertFalse(by_id[model_id]["checks"]["task_capability"]["passed"])
            self.assertEqual(
                by_id[model_id]["validation_evidence"]["provider_model_id"],
                model_id,
            )

    def test_tracked_gate_report_is_bound_to_candidate_definition(self) -> None:
        report = json.loads(
            (ROOT / "docs" / "reports" / "pa-model-candidate-offline-gate-v1.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(
            report["candidate_validation"]["schema_version"],
            CANDIDATE_VALIDATION_SCHEMA_VERSION,
        )
        self.assertEqual(
            report["candidate_validation"]["sha256"],
            candidate_validation_sha256(),
        )
        self.assertEqual(
            report["fixture"]["json_sha256"],
            hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        )
        self.assertEqual(report["provider_generation_calls"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
