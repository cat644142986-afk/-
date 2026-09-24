import copy
import unittest

from python.model_router import (
    SMART_ROUTER_SCHEMA_VERSION,
    attach_smart_route,
    build_smart_route,
    smart_router_policy_sha256,
)


def selection_fixture():
    records = {
        "tt-image-2": (0.0359, 53825.919),
        "banana-2": (0.1256, 35204.842),
        "banana-pro": (0.1579, 39957.692),
    }
    return {
        "schema_version": "pa-composer-model-selection-v1",
        "selection_sha256": "selection-fixture",
        "request": {
            "task_kind": "reference-generate",
            "output_ratio": "1:1",
            "output_resolution": "2k",
        },
        "status": "ready",
        "eligible_provider_model_ids": list(records),
        "default_provider_model_id": "tt-image-2",
        "models": [
            {
                "provider_model_id": model_id,
                "telemetry": {
                    "billing": {"cost": cost, "unit": "算力"},
                    "provider_elapsed_ms": elapsed,
                    "evidence_source": {"task_id": f"task-{model_id}"},
                },
            }
            for model_id, (cost, elapsed) in records.items()
        ],
    }


class SmartRouterTests(unittest.TestCase):
    def test_recommends_only_from_exact_admitted_models(self) -> None:
        route = build_smart_route(selection_fixture())
        self.assertEqual(route["schema_version"], SMART_ROUTER_SCHEMA_VERSION)
        self.assertEqual(route["policy_sha256"], smart_router_policy_sha256())
        self.assertEqual(route["recommended_provider_model_id"], "tt-image-2")
        self.assertEqual(
            route["reason_codes"],
            ["EXACT_CAPABILITY_AND_PARAMETERS", "LOWEST_OBSERVED_COST"],
        )
        self.assertTrue(route["policy"]["manual_override_allowed"])
        self.assertTrue(route["policy"]["no_execution_fallback"])
        self.assertTrue(route["policy"]["no_quality_ranking"])
        self.assertNotIn("score", str(route).lower())

    def test_latency_breaks_only_an_equal_cost_tie(self) -> None:
        selection = selection_fixture()
        selection["models"][1]["telemetry"]["billing"]["cost"] = 0.0359
        route = build_smart_route(selection)
        self.assertEqual(route["recommended_provider_model_id"], "banana-2")
        self.assertIn("LOWEST_OBSERVED_LATENCY_TIEBREAK", route["reason_codes"])

    def test_incomparable_price_uses_latency_without_quality_inference(self) -> None:
        selection = selection_fixture()
        selection["models"][1]["telemetry"]["billing"]["unit"] = "积分"
        route = build_smart_route(selection)
        self.assertEqual(route["recommended_provider_model_id"], "banana-2")
        self.assertEqual(
            route["reason_codes"],
            ["EXACT_CAPABILITY_AND_PARAMETERS", "LOWEST_OBSERVED_LATENCY"],
        )

    def test_no_evidence_falls_back_deterministically_without_substitution(self) -> None:
        selection = selection_fixture()
        for model in selection["models"]:
            model["telemetry"] = None
        routed = attach_smart_route(selection)
        self.assertEqual(routed["routing"]["recommended_provider_model_id"], "tt-image-2")
        self.assertEqual(
            routed["routing"]["reason_codes"][-1],
            "DETERMINISTIC_ADMISSION_FALLBACK",
        )
        self.assertEqual(selection.get("routing"), None)

    def test_unsupported_selection_has_no_recommendation(self) -> None:
        selection = copy.deepcopy(selection_fixture())
        selection.update({
            "status": "unsupported",
            "eligible_provider_model_ids": [],
            "default_provider_model_id": None,
            "models": [],
        })
        route = build_smart_route(selection)
        self.assertEqual(route["status"], "unsupported")
        self.assertIsNone(route["recommended_provider_model_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
