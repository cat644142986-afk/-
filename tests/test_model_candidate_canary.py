import json
import unittest
from pathlib import Path

from python.model_admission import build_composer_admission
from python.model_candidate_canary import (
    PROVIDER_CANARY_SCHEMA_VERSION,
    apply_provider_canary_overlay,
    provider_canary_sha256,
)
from python.model_candidate_validation import apply_candidate_validation_overlay
from python.model_capability_overlay import build_image_capability_overlay
from python.model_identity import (
    CATALOG_IMAGE_SOURCE,
    attach_identity_to_capability_overlay,
    build_model_identity_resolution,
)


ROOT = Path(__file__).resolve().parents[1]
MODELS = ("tt-image-2", "banana-2", "banana-pro")


def catalog_fixture():
    return {
        "models": [
            {
                "provider_model_id": model_id,
                "display_name": model_id,
                "available_for_this_key": True,
                "modalities": ["image"],
                "source_catalogs": [CATALOG_IMAGE_SOURCE, "v1/models", "v1/skills/models"],
                "provider_parameters": [],
            }
            for model_id in MODELS
        ]
    }


class ModelCandidateCanaryTests(unittest.TestCase):
    def test_exact_id_canaries_admit_only_reference_generate(self) -> None:
        catalog = catalog_fixture()
        offline = apply_candidate_validation_overlay(build_image_capability_overlay(catalog))
        live = apply_provider_canary_overlay(offline)
        identities = build_model_identity_resolution(catalog)
        capabilities = attach_identity_to_capability_overlay(live, identities)
        admission = build_composer_admission(capabilities)
        self.assertEqual(admission["summary"]["categories"]["eligible"], list(MODELS))
        self.assertEqual(admission["summary"]["categories"]["needs_canary"], [])
        by_id = {item["catalog"]["provider_model_id"]: item for item in live["models"]}
        for model_id in MODELS:
            overlay = by_id[model_id]["overlay"]
            self.assertEqual(
                overlay["task_kinds"]["reference-generate"]["evidence_level"],
                "provider-verified",
            )
            self.assertEqual(overlay["tested_output"]["pairs"], [{"ratio": "1:1", "resolution": "2k"}])
            self.assertNotEqual(
                overlay["task_kinds"]["text-to-image"]["evidence_level"],
                "provider-verified",
            )

    def test_tracked_report_binds_exact_evidence_definition(self) -> None:
        report = json.loads(
            (ROOT / "docs" / "reports" / "pa-model-candidate-provider-canary-v1.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(report["canary"]["schema_version"], PROVIDER_CANARY_SCHEMA_VERSION)
        self.assertEqual(report["canary"]["sha256"], provider_canary_sha256())
        self.assertEqual(report["provider_generation_calls"], 3)
        self.assertEqual(report["classification"]["eligible"], list(MODELS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
