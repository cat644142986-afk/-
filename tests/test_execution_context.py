from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from python.execution_context import (
    EXECUTION_CONTEXT_PRIORITY,
    ExecutionContextError,
    build_execution_context,
    knowledge_bundle_from_execution_context,
    validate_execution_context,
)
from python.knowledge_engine import KnowledgeCompiler


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "docs" / "contracts" / "execution-context-v1.schema.json"


def sample_bundle() -> dict:
    source = {
        "id": "knowledge:food-main-image",
        "title": "食品主图规则",
        "path": r"D:\知识库\20 知识库\设计知识\食品主图规则.md",
        "relative_path": "20 知识库/设计知识/食品主图规则.md",
    }
    return {
        "creative_brief": {
            "objective": "生成可交付的白底商品主图",
            "user_request": "保留包装文字，使用克制阴影",
            "output_kind": "ecommerce-main-image",
            "output_spec": {"ratio": "1:1", "resolution": "2k"},
        },
        "intent_lock_rules": ["严格保持包装文字"],
        "positive_rules": [{"text": "阴影克制", "source": source}],
        "negative_rules": [{"text": "不要改变品牌色", "source": source}],
        "sources": [source, source],
        "conflicts": [],
        "ignored_rules": [],
        "fallback": False,
    }


def build_context(*, binding: str = "preview") -> dict:
    return build_execution_context(
        context=sample_bundle()["creative_brief"],
        knowledge_bundle=sample_bundle(),
        mode="single",
        source_asset_ids=["asset:source-1"],
        command_id="canvas.generate.single",
        canvas_context={
            "document_id": "canvas:document-1",
            "expected_revision": 7,
            "operation_id": "operation:generate-1",
        },
        product_profile_context={
            "profile_id": "profile:tea",
            "version_id": "profile-version:tea-v3",
            "revision": 3,
            "sku": "TEA-001",
            "name": "透明茶饮瓶",
        },
        provider_context={
            "model": "gpt-image-2",
            "family": "openai-image",
            "adapter_version": "openai-image-v1",
            "requested_prompt_version": "prompt_v1",
            "effective_prompt_version": "prompt_v1",
            "route_reason": "stable-baseline",
            "generation_strategy": "single_pass",
            "material_profile": "transparent",
        },
        binding=binding,
    )


class ExecutionContextContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)

    def test_context_is_deterministic_schema_valid_and_path_safe(self) -> None:
        first = build_context()
        second = build_context()

        self.assertEqual(first, second)
        self.assertEqual(list(EXECUTION_CONTEXT_PRIORITY), first["priority_order"])
        self.assertEqual([], list(self.validator.iter_errors(first)))
        self.assertEqual(1, first["summary"]["source_count"])
        serialized = json.dumps(first, ensure_ascii=False)
        self.assertNotIn(r"D:\知识库", serialized)
        self.assertNotIn('"path"', serialized)
        self.assertEqual([], first["extensions"]["skills"])
        self.assertEqual([], first["extensions"]["cases"])

    def test_preview_promotes_to_job_snapshot_without_changing_identity(self) -> None:
        preview = build_context(binding="preview")
        frozen = build_context(binding="job-snapshot")

        self.assertNotEqual(preview["binding"], frozen["binding"])
        self.assertEqual(preview["context_sha256"], frozen["context_sha256"])
        self.assertEqual([], list(self.validator.iter_errors(frozen)))

    def test_tampered_context_is_rejected(self) -> None:
        context = build_context(binding="job-snapshot")
        context["user_intent"]["user_request"] = "偷偷改掉用户目标"

        with self.assertRaises(ExecutionContextError):
            validate_execution_context(context)

    def test_frozen_prompt_inputs_override_later_live_knowledge(self) -> None:
        context = build_context(binding="job-snapshot")
        expected = knowledge_bundle_from_execution_context(context)
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir) / "vault"
            (vault / "20 知识库" / "设计知识").mkdir(parents=True)
            compiler = KnowledgeCompiler(vault)
            actual = compiler.compile({
                "execution_context": copy.deepcopy(context),
                "approved_memory_rules": [{
                    "id": "memory:later",
                    "label": "后来的规则",
                    "text": "这条规则不应进入已经冻结的任务",
                }],
            })

        self.assertEqual(expected, actual)
        self.assertFalse(any(
            "后来的规则" in str(item)
            for item in actual["positive_rules"]
        ))

    def test_existing_prompt_route_budget_is_frozen_before_execution(self) -> None:
        source = {"id": "knowledge:budget", "title": "预算规则", "relative_path": "规则.md"}
        raw = {
            "creative_brief": sample_bundle()["creative_brief"],
            "intent_lock_rules": [f"锁定规则 {index}" for index in range(8)],
            "positive_rules": [
                {"text": f"正向规则 {index}", "source": source}
                for index in range(5)
            ],
            "negative_rules": [
                {"text": f"负向规则 {index}", "source": source}
                for index in range(3)
            ],
            "sources": [source],
            "conflicts": [],
            "fallback": False,
        }
        scope = {"prompt_version": "prompt_v3", "intent_locks": {}}
        governed = KnowledgeCompiler.govern_execution_bundle(raw, scope)
        context = build_execution_context(
            context=scope,
            knowledge_bundle=governed,
            mode="single",
            command_id="canvas.generate.single",
            binding="job-snapshot",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir) / "vault"
            (vault / "20 知识库" / "设计知识").mkdir(parents=True)
            actual = KnowledgeCompiler(vault).enrich_prompt(
                "base", "", {"prompt_version": "prompt_v3", "execution_context": context}
            )

        self.assertEqual(6, len(actual["intent_lock_rules"]))
        self.assertEqual(3, len(actual["positive_rules"]))
        self.assertEqual(1, len(actual["negative_rules"]))
        self.assertEqual(
            context["prompt_inputs"]["positive_rules"], actual["positive_rules"]
        )
        self.assertGreaterEqual(context["summary"]["ignored_count"], 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
