from __future__ import annotations

import unittest

from python.generation_baseline import (
    GENERATION_TRACE_CONTRACT_VERSION,
    PROMPT_COMPILER_VERSION,
    capability_contract,
    prompt_snapshot,
    summarize_trace_timings,
    unavailable_billing_evidence,
)
from tools.export_generation_baseline import build_report


class GenerationBaselineTests(unittest.TestCase):
    def test_prompt_snapshot_changes_for_each_reproducibility_input(self) -> None:
        baseline = prompt_snapshot(
            base_prompt="base",
            compiled_prompt="compiled",
            negative_prompt="negative",
            knowledge_evidence=[{"id": "rule-1", "text": "keep label"}],
        )
        self.assertEqual(baseline["prompt_version"], PROMPT_COMPILER_VERSION)
        self.assertEqual(
            baseline["trace_contract_version"], GENERATION_TRACE_CONTRACT_VERSION
        )
        self.assertEqual(baseline["knowledge_evidence_count"], 1)

        variants = [
            prompt_snapshot(
                base_prompt="changed",
                compiled_prompt="compiled",
                negative_prompt="negative",
                knowledge_evidence=[{"id": "rule-1", "text": "keep label"}],
            ),
            prompt_snapshot(
                base_prompt="base",
                compiled_prompt="changed",
                negative_prompt="negative",
                knowledge_evidence=[{"id": "rule-1", "text": "keep label"}],
            ),
            prompt_snapshot(
                base_prompt="base",
                compiled_prompt="compiled",
                negative_prompt="changed",
                knowledge_evidence=[{"id": "rule-1", "text": "keep label"}],
            ),
            prompt_snapshot(
                base_prompt="base",
                compiled_prompt="compiled",
                negative_prompt="negative",
                knowledge_evidence=[{"id": "rule-2", "text": "keep label"}],
            ),
        ]
        digest_keys = {
            "base_prompt_sha256",
            "compiled_prompt_sha256",
            "negative_prompt_sha256",
            "knowledge_snapshot_sha256",
        }
        self.assertTrue(all(
            any(candidate[key] != baseline[key] for key in digest_keys)
            for candidate in variants
        ))

    def test_capability_registry_does_not_overclaim_unknown_models(self) -> None:
        gpt = capability_contract("gpt-image-2", "gpt-image-2")
        self.assertEqual(gpt["status"], "request-shape-checked")
        self.assertEqual(gpt["reference_parameter"], "params.images[]")
        self.assertEqual(gpt["billing_telemetry"], "not-exposed-by-current-adapter")

        unknown = capability_contract("future-model")
        self.assertEqual(unknown["family"], "generic-image")
        self.assertEqual(unknown["status"], "compatibility-only")

    def test_offline_summary_keeps_missing_cost_visible(self) -> None:
        traces = [
            {
                "stage": "provider.image.1-1",
                "status": "completed",
                "output": {
                    "elapsed_ms": 1200.5,
                    "billing": unavailable_billing_evidence(),
                },
            },
            {
                "stage": "vlm.detect",
                "status": "skipped",
                "output": {"elapsed_ms": 0.0},
            },
            {
                "stage": "local.cutout.1",
                "status": "completed",
                "output": {"elapsed_ms": 400.25},
            },
        ]
        summary = summarize_trace_timings(traces)
        self.assertEqual(summary["billable_call_count"], 1)
        self.assertEqual(summary["priced_call_count"], 0)
        self.assertEqual(summary["cost_baseline_status"], "incomplete")
        self.assertEqual(summary["summed_stage_elapsed_ms"], 1600.75)

    def test_export_report_omits_prompts_and_hashes_job_identity(self) -> None:
        rows = [
            {
                "job_id": "private-job-id",
                "stage": "prompt.primary",
                "status": "completed",
                "parameters_json": '{"base_prompt":"private prompt"}',
                "output_json": (
                    '{"prompt_snapshot":{"prompt_version":"prompt_v1",'
                    '"compiled_prompt_sha256":"abc"}}'
                ),
            },
            {
                "job_id": "private-job-id",
                "stage": "workflow.complete",
                "status": "completed",
                "parameters_json": "{}",
                "output_json": '{"elapsed_ms":2500}',
            },
        ]
        report = build_report(rows, [])
        self.assertEqual(report["coverage"]["job_count"], 1)
        self.assertEqual(report["coverage"]["instrumented_job_count"], 1)
        self.assertNotEqual(report["jobs"][0]["job_key"], "private-job-id")
        self.assertNotIn("private prompt", str(report))
        self.assertFalse(report["privacy"]["prompts_included"])


if __name__ == "__main__":
    unittest.main()
