from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "docs" / "contracts" / "growth-foundation-v1.schema.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "growth_foundation" / "minimal-contract.json"
EXPECTED_CONTRACTS = {
    "CanvasDocument",
    "Artboard",
    "Layer",
    "Operation",
    "Mask",
    "ROI",
    "ProductProfile",
    "QualityIssue",
    "Recipe",
}


def _duplicate_ids(items: list[dict]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in items:
        item_id = item["id"]
        if item_id in seen:
            duplicates.add(item_id)
        seen.add(item_id)
    return duplicates


def semantic_contract_errors(value: dict) -> list[str]:
    document = value["canvas_document"]
    collections = {
        "artboard": document["artboards"],
        "layer": document["layers"],
        "operation": document["operations"],
        "mask": value["masks"],
        "roi": value["rois"],
        "product_profile": value["product_profiles"],
        "quality_issue": value["quality_issues"],
        "recipe": value["recipes"],
    }
    ids = {name: {item["id"] for item in items} for name, items in collections.items()}
    errors: list[str] = []

    for name, items in collections.items():
        for duplicate_id in sorted(_duplicate_ids(items)):
            errors.append(f"duplicate {name} id: {duplicate_id}")

    if document["active_artboard_id"] not in ids["artboard"]:
        errors.append("active_artboard_id does not reference an artboard")

    source_asset_ids = set(document["source_asset_ids"])
    for layer in document["layers"]:
        if layer["artboard_id"] not in ids["artboard"]:
            errors.append(f"layer {layer['id']} references a missing artboard")
        source = layer["source"]
        if source["kind"] == "asset" and source["id"] not in source_asset_ids:
            errors.append(f"layer {layer['id']} references an undeclared source asset")

    for operation in document["operations"]:
        for layer_id in operation["input_layer_ids"]:
            if layer_id not in ids["layer"]:
                errors.append(f"operation {operation['id']} references a missing input layer")
        if operation["output_layer_id"] not in ids["layer"]:
            errors.append(f"operation {operation['id']} references a missing output layer")
        for field, collection_name in (
            ("mask_id", "mask"),
            ("roi_id", "roi"),
            ("product_profile_id", "product_profile"),
        ):
            reference = operation[field]
            if reference is not None and reference not in ids[collection_name]:
                errors.append(f"operation {operation['id']} references a missing {collection_name}")

    for collection_name in ("mask", "roi"):
        for item in collections[collection_name]:
            if item["source_layer_id"] not in ids["layer"]:
                errors.append(f"{collection_name} {item['id']} references a missing source layer")

    for roi in value["rois"]:
        rect = roi["normalized_rect"]
        if rect["x"] + rect["width"] > 1 or rect["y"] + rect["height"] > 1:
            errors.append(f"roi {roi['id']} exceeds normalized source bounds")

    for issue in value["quality_issues"]:
        if issue["roi_id"] is not None and issue["roi_id"] not in ids["roi"]:
            errors.append(f"quality issue {issue['id']} references a missing roi")

    for recipe in value["recipes"]:
        if recipe["product_profile_id"] not in ids["product_profile"]:
            errors.append(f"recipe {recipe['id']} references a missing product profile")

    max_undo_cursor = len(document["operations"]) - 1
    if document["undo_cursor"] > max_undo_cursor:
        errors.append("undo_cursor exceeds the operation history")

    return errors


class GrowthFoundationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(
            cls.schema,
            format_checker=FormatChecker(),
        )

    def assert_valid(self, value: dict) -> None:
        errors = sorted(self.validator.iter_errors(value), key=lambda error: list(error.path))
        self.assertEqual([], [error.message for error in errors])

    def assert_invalid(self, value: dict) -> None:
        self.assertTrue(list(self.validator.iter_errors(value)))

    def assert_semantically_valid(self, value: dict) -> None:
        self.assertEqual([], semantic_contract_errors(value))

    def test_schema_freezes_the_nine_g0_contract_names(self) -> None:
        self.assertEqual(EXPECTED_CONTRACTS, set(self.schema["$defs"]))
        self.assertEqual("growth-foundation-v1", self.fixture["contract_version"])
        self.assert_valid(self.fixture)

    def test_document_references_only_existing_contract_entities(self) -> None:
        self.assert_semantically_valid(self.fixture)

    def test_semantic_integrity_rejects_dangling_duplicate_and_history_references(self) -> None:
        dangling = copy.deepcopy(self.fixture)
        dangling["canvas_document"]["operations"][0]["input_layer_ids"] = ["layer:missing"]
        self.assertIn("missing input layer", " ".join(semantic_contract_errors(dangling)))

        duplicate = copy.deepcopy(self.fixture)
        duplicate["canvas_document"]["layers"][1]["id"] = duplicate["canvas_document"]["layers"][0]["id"]
        self.assertIn("duplicate layer id", " ".join(semantic_contract_errors(duplicate)))

        invalid_history = copy.deepcopy(self.fixture)
        invalid_history["canvas_document"]["undo_cursor"] = 2
        self.assertIn("undo_cursor", " ".join(semantic_contract_errors(invalid_history)))

    def test_roi_must_remain_inside_the_normalized_source_bounds(self) -> None:
        outside = copy.deepcopy(self.fixture)
        outside["rois"][0]["normalized_rect"].update(x=0.6, width=0.5)
        self.assertIn("normalized source bounds", " ".join(semantic_contract_errors(outside)))

    def test_fixture_is_synthetic_and_contains_no_embedded_or_private_data(self) -> None:
        serialized = json.dumps(self.fixture, ensure_ascii=False).lower()
        forbidden = (
            "data:image",
            "base64",
            "%appdata%",
            "productatelier\\atelier.sqlite3",
            "api_key",
            "authorization",
            "user_prompt",
        )
        for marker in forbidden:
            self.assertNotIn(marker, serialized)
        self.assertIsNone(re.search(r"[a-z]:\\\\", serialized))
        source_ids = self.fixture["canvas_document"]["source_asset_ids"]
        self.assertTrue(all("synthetic" in source_id for source_id in source_ids))

    def test_layer_source_rejects_base64_and_absolute_path_payloads(self) -> None:
        embedded = copy.deepcopy(self.fixture)
        embedded["canvas_document"]["layers"][0]["source"]["proxy_ref"] = "data:image/png;base64,AA=="
        self.assert_invalid(embedded)

        absolute = copy.deepcopy(self.fixture)
        absolute["masks"][0]["storage_ref"] = "D:\\user-images\\mask.png"
        self.assert_invalid(absolute)

    def test_paid_operations_require_confirmation_and_forbid_automatic_retry(self) -> None:
        unsafe = copy.deepcopy(self.fixture)
        cost = unsafe["canvas_document"]["operations"][0]["cost"]
        cost.update(
            mode="paid",
            confirmed_call_count=1,
            user_confirmation_required=False,
            automatic_paid_retry=True,
        )
        self.assert_invalid(unsafe)

        approved = copy.deepcopy(self.fixture)
        approved_cost = approved["canvas_document"]["operations"][0]["cost"]
        approved_cost.update(
            mode="paid",
            confirmed_call_count=1,
            user_confirmation_required=True,
            automatic_paid_retry=False,
        )
        self.assert_valid(approved)

    def test_paid_recipe_steps_require_user_confirmation(self) -> None:
        unsafe = copy.deepcopy(self.fixture)
        unsafe_step = unsafe["recipes"][0]["steps"][0]
        unsafe_step.update(paid=True, requires_user_confirmation=False)
        self.assert_invalid(unsafe)

    def test_unknown_fields_are_rejected_instead_of_silently_persisted(self) -> None:
        drift = copy.deepcopy(self.fixture)
        drift["canvas_document"]["layers"][0]["raw_image_base64"] = "AA=="
        self.assert_invalid(drift)

    def test_date_time_checker_dependency_is_active(self) -> None:
        self.assertFalse(FormatChecker().conforms("not-a-date", "date-time"))

    def test_invalid_date_time_is_rejected_by_the_format_checker(self) -> None:
        invalid = copy.deepcopy(self.fixture)
        invalid["canvas_document"]["updated_at"] = "2026-13-40T25:61:00Z"
        self.assert_invalid(invalid)


if __name__ == "__main__":
    unittest.main()
