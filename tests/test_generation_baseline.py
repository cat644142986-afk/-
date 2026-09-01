from __future__ import annotations

import unittest

from python.generation_baseline import (
    GENERATION_TRACE_CONTRACT_VERSION,
    LEGACY_DOUBLE_PASS,
    MATERIAL_PROMPT_ROUTE_VERSION,
    PROMPT_COMPILER_VERSION,
    PROMPT_V2_FEATURE_ENV,
    PROMPT_V3_FEATURE_ENV,
    SINGLE_PASS,
    SINGLE_PASS_FEATURE_ENV,
    capability_contract,
    compile_prompt_version,
    normalize_generation_strategy,
    normalize_prompt_version,
    prompt_adapter_profile,
    prompt_snapshot,
    prompt_v3_render_plan,
    resolve_material_prompt_route,
    summarize_trace_timings,
    unavailable_billing_evidence,
)
from tools.export_generation_baseline import build_report


class GenerationBaselineTests(unittest.TestCase):
    def test_prompt_v1_is_byte_identical_and_v2_is_structured(self) -> None:
        template = "原始模板，严格保持。"
        self.assertEqual(
            compile_prompt_version(
                template,
                prompt_version="prompt_v1",
                context={"product_name": "茶盒"},
            ),
            template,
        )
        compiled = compile_prompt_version(
            template,
            prompt_version="prompt_v2",
            context={
                "product_name": "茶盒",
                "output_kind": "ecommerce-main",
                "platter": "remove",
                "angle": "front",
                "fidelity": 20,
            },
            stage="primary",
        )
        self.assertIn("任务目标：", compiled)
        self.assertIn("不可破坏项：", compiled)
        self.assertIn("茶盒", compiled)
        self.assertIn(template, compiled)

    def test_prompt_v2_requires_an_explicit_feature_gate(self) -> None:
        self.assertEqual(normalize_prompt_version(None, environment={}), "prompt_v1")
        with self.assertRaisesRegex(ValueError, "is disabled"):
            normalize_prompt_version("prompt_v2", environment={})
        self.assertEqual(
            normalize_prompt_version(
                "prompt_v2", environment={PROMPT_V2_FEATURE_ENV: "true"}
            ),
            "prompt_v2",
        )
        with self.assertRaisesRegex(ValueError, "unsupported prompt version"):
            normalize_prompt_version("prompt_v99", environment={})

    def test_prompt_v3_is_compact_model_aware_and_excludes_legacy_stack(self) -> None:
        template = "旧提示词：8K超清，广告级质感，柔光箱，专业修图。"
        context = {
            "product_name": "茶盒",
            "output_kind": "ecommerce-main",
            "platter": "remove",
            "angle": "front",
            "fidelity": 20,
            "source_cutoff": True,
            "quantity": 3,
            "user_request": "保留茶盒正面的金色文字，只优化背景光线",
            "output_spec": {
                "effective_ratio": "4:5",
                "requested_resolution": "2k",
            },
        }
        gpt = compile_prompt_version(
            template,
            prompt_version="prompt_v3",
            context={**context, "model": "gpt-image-2"},
            stage="primary",
        )
        gemini = compile_prompt_version(
            template,
            prompt_version="prompt_v3",
            context={**context, "model": "gemini-3.1-flash-image-preview"},
            stage="primary",
        )
        self.assertNotIn(template, gpt)
        self.assertNotIn("8K超清", gpt)
        self.assertIn("未点名的主体内容保持不变", gpt)
        self.assertIn("先保持产品身份一致", gemini)
        self.assertEqual(
            prompt_adapter_profile("gpt-image-2")["id"],
            "gpt-image-2-compact-v1",
        )
        self.assertEqual(
            prompt_adapter_profile("gemini-3.1-flash-image-preview")["id"],
            "gemini-image-compact-v1",
        )
        self.assertIn("4:5", gpt)
        self.assertIn("2k", gpt)
        self.assertIn("产品数量严格保持为3", gpt)
        self.assertIn("保留茶盒正面的金色文字，只优化背景光线", gpt)
        self.assertIn("补全原图被裁切的主体边缘", gpt)
        self.assertLess(len(gpt), 520)
        self.assertNotEqual(gpt, gemini)

        plan = prompt_v3_render_plan(
            context={**context, "model": "gpt-image-2"},
            stage="refine-1",
        )
        self.assertEqual(plan["product_count"], 3)
        self.assertIn(context["user_request"], plan["objective"])
        self.assertEqual(plan["output_bits"], ["ecommerce-main", "4:5", "2k"])

        adjustment = compile_prompt_version(
            template,
            prompt_version="prompt_v3",
            context={
                **context,
                "model": "gpt-image-2",
                "user_request": "只修复包装正面的文字边缘",
            },
            stage="adjustment-primary",
        )
        self.assertIn("只修复包装正面的文字边缘", adjustment)

    def test_prompt_v3_and_single_pass_require_separate_experiment_gates(self) -> None:
        with self.assertRaisesRegex(ValueError, "prompt_v3 is disabled"):
            normalize_prompt_version("prompt_v3", environment={})
        self.assertEqual(
            normalize_prompt_version(
                "prompt_v3", environment={PROMPT_V3_FEATURE_ENV: "yes"}
            ),
            "prompt_v3",
        )
        self.assertEqual(
            normalize_prompt_version(
                "prompt_v3", environment={}, allow_user_prompt_v3=True
            ),
            "prompt_v3",
        )
        self.assertEqual(normalize_generation_strategy(None, environment={}), LEGACY_DOUBLE_PASS)
        with self.assertRaisesRegex(ValueError, "single_pass is disabled"):
            normalize_generation_strategy(SINGLE_PASS, environment={})
        self.assertEqual(
            normalize_generation_strategy(
                SINGLE_PASS,
                environment={SINGLE_PASS_FEATURE_ENV: "1"},
            ),
            SINGLE_PASS,
        )

    def test_material_route_is_explicit_conservative_and_never_upgrades(self) -> None:
        baseline = resolve_material_prompt_route(
            "prompt_v1",
            {"material_profile": "opaque", "category": "packaging"},
        )
        self.assertEqual(baseline["effective_prompt_version"], "prompt_v1")
        self.assertEqual(baseline["reason"], "requested-version-preserved")

        eligible = resolve_material_prompt_route(
            "prompt_v3",
            {
                "material_profile": "opaque",
                "category": "general",
                "product_count": 3,
            },
        )
        self.assertEqual(eligible["contract_version"], MATERIAL_PROMPT_ROUTE_VERSION)
        self.assertEqual(eligible["effective_prompt_version"], "prompt_v3")
        self.assertEqual(eligible["reason"], "eligible-opaque-structured")

        for profile in ("transparent", "reflective", "mixed"):
            route = resolve_material_prompt_route(
                "prompt_v3",
                {"material_profile": profile, "category": "packaging"},
            )
            self.assertEqual(route["effective_prompt_version"], "prompt_v1")
            self.assertEqual(route["reason"], "sensitive-material-baseline")
            self.assertFalse(route["provider_retry_authorized"])

        unknown = resolve_material_prompt_route(
            "prompt_v3",
            {"product_name": "透明水瓶", "category": "packaging"},
        )
        self.assertEqual(unknown["effective_prompt_version"], "prompt_v1")
        self.assertEqual(unknown["reason"], "material-evidence-required")

        low_signal = resolve_material_prompt_route(
            "prompt_v3",
            {"material_profile": "opaque", "category": "food", "product_count": 1},
        )
        self.assertEqual(low_signal["effective_prompt_version"], "prompt_v1")
        self.assertEqual(low_signal["reason"], "compact-benefit-not-demonstrated")

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
