import json
import unittest
from pathlib import Path

from python.model_capability_overlay import (
    CATALOG_V1_BINDING,
    CATALOG_V1_IMAGE_MODEL_IDS,
    build_image_capability_overlay,
)
from python.model_identity import (
    CATALOG_IMAGE_SOURCE,
    LK_PROVIDER,
    MODEL_IDENTITY_SCHEMA_VERSION,
    attach_identity_to_capability_overlay,
    build_model_identity_resolution,
    canonical_model_id,
    identity_definition,
    identity_sha256,
    resolve_model_identity,
)


def catalog_fixture():
    return {
        "models": [
            {
                "provider_model_id": model_id,
                "display_name": model_id,
                "available_for_this_key": True,
                "source_catalogs": [CATALOG_IMAGE_SOURCE],
                "provider_parameters": [
                    {"name": "prompt", "required": True},
                    {"name": "images", "required": False},
                ],
            }
            for model_id in CATALOG_V1_IMAGE_MODEL_IDS
        ]
    }


class ModelIdentityTests(unittest.TestCase):
    def test_exact_provider_id_is_preserved_in_canonical_projection(self) -> None:
        identity = resolve_model_identity("tt-image-2")
        self.assertEqual(identity["provider_model_id"], "tt-image-2")
        self.assertEqual(
            identity["canonical_model_id"],
            "pa:image:lk-ai-model-center:tt-image-2",
        )
        self.assertEqual(identity["resolution"], "exact-provider-id")
        self.assertFalse(identity["merge_allowed"])
        self.assertEqual(identity["upstream_identity"]["status"], "unresolved")

    def test_names_and_adapter_family_do_not_create_aliases(self) -> None:
        definition = identity_definition()
        self.assertEqual(definition["verified_equivalences"], [])
        resolution = build_model_identity_resolution(
            catalog_fixture(),
            normalized_catalog_sha256=CATALOG_V1_BINDING[
                "normalized_catalog_sha256"
            ],
        )
        relations = {
            (
                item["left_provider_model_id"],
                item["right_provider_model_id"],
            ): item
            for item in resolution["relations"]
        }
        for pair in (
            ("gpt-image-2", "tt-image-2"),
            ("gemini-3.1-flash-image-preview", "banana-2"),
            ("gemini-3-pro-image-preview", "banana-pro"),
            ("banana-2", "banana-2-token"),
            ("banana-pro", "banana-pro-token"),
        ):
            self.assertEqual(relations[pair]["status"], "unresolved")
            self.assertFalse(relations[pair]["merge_allowed"])
            self.assertNotEqual(
                relations[pair]["left_canonical_model_id"],
                relations[pair]["right_canonical_model_id"],
            )

    def test_historical_execution_id_is_not_rewritten(self) -> None:
        original = {"model": "gpt-image-2", "receipt_id": "receipt-fixture"}
        identity = resolve_model_identity(
            original["model"], catalog_present=False, source="historical-execution"
        )
        self.assertEqual(original["model"], "gpt-image-2")
        self.assertEqual(identity["provider_model_id"], original["model"])
        self.assertFalse(identity["catalog_present"])
        self.assertEqual(
            identity["canonical_model_id"],
            canonical_model_id(LK_PROVIDER, "gpt-image-2"),
        )

    def test_overlay_is_grouped_by_canonical_identity_without_evidence_leakage(self) -> None:
        catalog = catalog_fixture()
        capabilities = build_image_capability_overlay(
            catalog,
            normalized_catalog_sha256=CATALOG_V1_BINDING[
                "normalized_catalog_sha256"
            ],
        )
        identities = build_model_identity_resolution(
            catalog,
            normalized_catalog_sha256=CATALOG_V1_BINDING[
                "normalized_catalog_sha256"
            ],
        )
        joined = attach_identity_to_capability_overlay(capabilities, identities)
        self.assertEqual(joined["summary"]["canonical_models"], 21)
        self.assertEqual(
            joined["summary"]["composer_eligible_canonical_model_ids"], []
        )
        groups = {item["canonical_model_id"]: item for item in joined["canonical_models"]}
        gpt_id = canonical_model_id(LK_PROVIDER, "gpt-image-2")
        tt_id = canonical_model_id(LK_PROVIDER, "tt-image-2")
        self.assertNotEqual(gpt_id, tt_id)
        self.assertEqual(groups[gpt_id]["aggregate"]["status"], "stable")
        self.assertFalse(groups[gpt_id]["members"][0]["catalog_present"])
        self.assertEqual(groups[tt_id]["aggregate"]["status"], "experimental")
        self.assertEqual(
            groups[tt_id]["aggregate"]["task_kinds"],
            groups[tt_id]["capability_entries"][0]["task_kinds"],
        )
        self.assertEqual(
            groups[tt_id]["capability_entries"][0]["tested_output"]["pairs"], []
        )

    def test_tracked_report_is_bound_to_identity_definition(self) -> None:
        root = Path(__file__).resolve().parents[1]
        report = json.loads(
            (root / "docs" / "reports" / "pa-model-identity-resolution-v1.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(
            report["identity"]["schema_version"], MODEL_IDENTITY_SCHEMA_VERSION
        )
        self.assertEqual(report["identity"]["sha256"], identity_sha256())
        self.assertEqual(report["composer_decision"]["canonical_models"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
