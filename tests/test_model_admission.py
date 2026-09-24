import copy
import json
import unittest
from pathlib import Path

from python.model_admission import (
    ADMISSION_SCHEMA_VERSION,
    COMPOSER_SELECTION_SCHEMA_VERSION,
    FOCUS_CANDIDATE_IDS,
    admission_sha256,
    build_composer_admission,
    build_composer_model_selection,
    evaluate_model_admission,
)
from python.model_candidate_canary import apply_provider_canary_overlay
from python.model_candidate_validation import apply_candidate_validation_overlay
from python.model_capability_overlay import build_image_capability_overlay
from python.model_identity import (
    CATALOG_IMAGE_SOURCE,
    attach_identity_to_capability_overlay,
    build_model_identity_resolution,
)


def catalog_fixture():
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
                    {"name": "prompt", "required": True},
                    {"name": "images", "required": False},
                ],
            }
            for model_id in FOCUS_CANDIDATE_IDS
        ]
    }


def capability_fixture():
    catalog = catalog_fixture()
    overlay = build_image_capability_overlay(catalog)
    identities = build_model_identity_resolution(catalog)
    return attach_identity_to_capability_overlay(overlay, identities)


def verified_capability_fixture():
    return attach_identity_to_capability_overlay(
        apply_provider_canary_overlay(
            apply_candidate_validation_overlay(
                build_image_capability_overlay(catalog_fixture())
            )
        ),
        build_model_identity_resolution(catalog_fixture()),
    )


class ModelAdmissionTests(unittest.TestCase):
    def test_focused_candidates_are_classified_by_first_missing_gate(self) -> None:
        result = build_composer_admission(capability_fixture())
        by_id = {item["provider_model_id"]: item for item in result["candidates"]}

        self.assertEqual(by_id["tt-image-2"]["category"], "needs_canary")
        self.assertTrue(
            by_id["tt-image-2"]["checks"]["canonical_identity"]["passed"]
        )
        self.assertTrue(
            by_id["tt-image-2"]["checks"]["adapter_contract"]["passed"]
        )
        self.assertEqual(
            by_id["tt-image-2"]["checks"]["task_capability"]["evidence_level"],
            "adapter-contract",
        )

        for model_id in ("banana-2", "banana-pro"):
            self.assertEqual(by_id[model_id]["category"], "needs_adapter")
            self.assertTrue(by_id[model_id]["checks"]["catalog_route"]["passed"])
            self.assertTrue(
                by_id[model_id]["checks"]["canonical_identity"]["passed"]
            )
            self.assertFalse(
                by_id[model_id]["checks"]["adapter_contract"]["passed"]
            )
        self.assertEqual(result["summary"]["eligible_canonical_model_ids"], [])

    def test_exact_current_identity_does_not_inherit_legacy_evidence(self) -> None:
        result = build_composer_admission(capability_fixture())
        tt = next(
            item for item in result["candidates"]
            if item["provider_model_id"] == "tt-image-2"
        )
        self.assertEqual(
            tt["checks"]["canonical_identity"]["resolution"], "exact-provider-id"
        )
        self.assertEqual(tt["category"], "needs_canary")
        self.assertNotEqual(
            tt["canonical_model_id"],
            next(
                item["canonical_model_id"]
                for item in result["legacy_only"]
                if item["provider_model_id"] == "gpt-image-2"
            ),
        )

    def test_provider_verified_task_evidence_is_required_for_eligibility(self) -> None:
        capabilities = capability_fixture()
        tt = copy.deepcopy(next(
            item for item in capabilities["models"]
            if item["catalog"]["provider_model_id"] == "tt-image-2"
        ))
        tt["overlay"]["task_kinds"]["reference-generate"].update({
            "status": "stable",
            "evidence_level": "provider-verified",
        })
        admitted = evaluate_model_admission(tt)
        self.assertTrue(admitted["eligible"])
        self.assertEqual(admitted["category"], "eligible")

    def test_blocked_task_stays_out_even_with_an_adapter(self) -> None:
        capabilities = capability_fixture()
        tt = copy.deepcopy(next(
            item for item in capabilities["models"]
            if item["catalog"]["provider_model_id"] == "tt-image-2"
        ))
        tt["overlay"]["task_kinds"]["reference-generate"].update({
            "status": "blocked",
            "evidence_level": "none",
        })
        admitted = evaluate_model_admission(tt)
        self.assertFalse(admitted["eligible"])
        self.assertEqual(admitted["category"], "needs_offline_gate")

    def test_tracked_plan_is_bound_to_policy_definition(self) -> None:
        root = Path(__file__).resolve().parents[1]
        report = json.loads(
            (root / "docs" / "reports" / "pa-composer-admission-plan-v1.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(report["admission"]["schema_version"], ADMISSION_SCHEMA_VERSION)
        self.assertEqual(report["admission"]["sha256"], admission_sha256())
        self.assertEqual(report["classification"]["eligible"], [])
        self.assertEqual(report["classification"]["needs_canary"], ["tt-image-2"])
        self.assertEqual(
            report["classification"]["needs_adapter"],
            ["banana-2", "banana-pro"],
        )
        self.assertEqual(report["provider_generation_calls"], 0)

    def test_selection_admits_only_verified_reference_tuple(self) -> None:
        selection = build_composer_model_selection(
            verified_capability_fixture(),
            task_kind="reference-generate",
            output_ratio="1:1",
            output_resolution="2K",
        )
        self.assertEqual(selection["schema_version"], COMPOSER_SELECTION_SCHEMA_VERSION)
        self.assertEqual(selection["status"], "ready")
        self.assertEqual(
            selection["eligible_provider_model_ids"],
            ["tt-image-2", "banana-2", "banana-pro"],
        )
        self.assertEqual(selection["default_provider_model_id"], "tt-image-2")
        self.assertTrue(selection["policy"]["telemetry_is_not_ranking"])
        self.assertNotIn("quality_score", json.dumps(selection["models"]))
        self.assertNotIn("recommended", json.dumps(selection["models"]))
        for item in selection["models"]:
            self.assertEqual(item["output"], {"ratio": "1:1", "resolution": "2k"})
            self.assertEqual(item["adapter"]["status"], "provider-verified")
            self.assertIn("single-canary", item["telemetry"]["interpretation"])

    def test_selection_never_downgrades_or_substitutes_unsupported_request(self) -> None:
        capabilities = verified_capability_fixture()
        for task_kind, ratio, resolution in (
            ("reference-generate", "original", "2k"),
            ("reference-generate", "1:1", "4k"),
            ("variant", "1:1", "2k"),
        ):
            selection = build_composer_model_selection(
                capabilities,
                task_kind=task_kind,
                output_ratio=ratio,
                output_resolution=resolution,
            )
            self.assertEqual(selection["status"], "unsupported")
            self.assertEqual(selection["eligible_provider_model_ids"], [])
            self.assertIsNone(selection["default_provider_model_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
