from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from python.knowledge_engine import (
    KnowledgeCompiler,
    canonicalize_vault_path,
    default_vault_path,
)
from python.memory_engine import resolve_approved_memory_rules


class KnowledgePathTests(unittest.TestCase):
    def test_environment_override_is_canonicalized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            nested = Path(temp_dir) / "vault"
            nested.mkdir()
            alias = nested / ".." / "vault"
            with patch.dict(os.environ, {"PRODUCT_ATELIER_KNOWLEDGE_BASE": str(alias)}):
                self.assertEqual(default_vault_path(), nested.resolve())

    def test_compiler_keeps_one_canonical_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir) / "vault"
            design = vault / "20 知识库" / "设计知识"
            design.mkdir(parents=True)
            alias = design / ".." / "设计知识" / ".." / ".."
            compiler = KnowledgeCompiler(alias)
            self.assertEqual(compiler.vault_path, canonicalize_vault_path(vault))
            self.assertEqual(compiler.status()["vault_path"], str(vault.resolve()))


class KnowledgeMemoryContractTests(unittest.TestCase):
    def test_prompt_v3_prunes_conflicts_and_enforces_rule_budgets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir) / "vault"
            (vault / "20 知识库" / "设计知识").mkdir(parents=True)
            compiler = KnowledgeCompiler(vault)
            source = {"id": "K-1", "title": "测试规则", "path": "K-1.md"}
            bundle = {
                "intent_lock_rules": [
                    "严格保持包装文字",
                    "严格保持品牌标志",
                    "严格保持产品数量",
                    "严格保持主体结构",
                    "严格保持品牌色",
                    "严格保持原图拍摄角度、透视与产品朝向",
                    "第七条应被预算裁剪",
                ],
                "positive_rules": [
                    {"text": "保留器皿并优化摆盘", "source": source},
                    {"text": "产品完整不裁切", "source": source},
                    {"text": "白底保持纯净", "source": source},
                    {"text": "材质反光自然", "source": source},
                    {"text": "第五条超出数量预算", "source": source},
                ],
                "negative_rules": [
                    {"text": "不要使用深色背景", "source": source},
                    {"text": "不要增加装饰物", "source": source},
                ],
                "sources": [source],
                "conflicts": [],
            }
            with patch.object(compiler, "compile", return_value=bundle):
                result = compiler.enrich_prompt(
                    "短执行计划",
                    "模糊,文字,logo,水印",
                    {
                        "prompt_version": "prompt_v3",
                        "category": "packaging",
                        "platter": "remove",
                        "background": "white-studio",
                        "intent_locks": {"packaging_text": True, "logo": True},
                    },
                )

            self.assertNotIn("保留器皿并优化摆盘", result["prompt"])
            self.assertEqual(len(result["intent_lock_rules"]), 6)
            self.assertTrue(any(
                "产品数量不变" in item for item in result["intent_lock_rules"]
            ))
            self.assertLessEqual(
                sum(len(item) for item in result["intent_lock_rules"]), 160
            )
            self.assertLessEqual(len(result["positive_rules"]), 3)
            self.assertLessEqual(len(result["negative_rules"]), 1)
            self.assertIn("包装文字数字或品牌标志错误", result["negative_prompt"])
            self.assertNotIn("模糊,文字,logo", result["negative_prompt"])
            self.assertTrue(any(
                item.get("reason") == "conflicts-with-task"
                for item in result["ignored_rules"]
            ))
            self.assertEqual(result["sources"], [source])

    def test_memory_scope_priority_chooses_brand_then_category_then_designer(self) -> None:
        suggestions = [
            {
                "id": "designer-rule",
                "status": "approved",
                "scope_type": "designer",
                "scope_id": "default",
                "category": "general",
                "rule_key": "lighting.softness",
                "created_at": "2026-08-01",
                "proposed_value": {"label": "个人柔光", "directive": "个人默认柔光"},
            },
            {
                "id": "category-rule",
                "status": "approved",
                "scope_type": "category",
                "scope_id": "food",
                "category": "food",
                "rule_key": "lighting.softness",
                "created_at": "2026-08-02",
                "proposed_value": {"label": "食品柔光", "directive": "食品使用均匀柔光"},
            },
            {
                "id": "brand-rule",
                "status": "approved",
                "scope_type": "brand",
                "scope_id": "PA Tea",
                "category": "food",
                "rule_key": "lighting.softness",
                "created_at": "2026-08-03",
                "proposed_value": {"label": "品牌硬光", "directive": "PA Tea 使用清晰硬光"},
            },
            {
                "id": "disabled-brand-rule",
                "status": "disabled",
                "scope_type": "brand",
                "scope_id": "PA Tea",
                "category": "food",
                "rule_key": "composition.spacing",
                "proposed_value": {"label": "已停用", "directive": "不应参与执行"},
            },
        ]
        brand_rules = resolve_approved_memory_rules(
            suggestions, {"category": "food", "brand_profile": "PA Tea"}
        )
        self.assertEqual([item["id"] for item in brand_rules], ["brand-rule"])
        category_rules = resolve_approved_memory_rules(
            suggestions, {"category": "food", "brand_profile": "Other"}
        )
        self.assertEqual([item["id"] for item in category_rules], ["category-rule"])
        designer_rules = resolve_approved_memory_rules(
            suggestions, {"category": "general"}
        )
        self.assertEqual([item["id"] for item in designer_rules], ["designer-rule"])

    def test_approved_memory_rules_reach_prompt_and_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir) / "vault"
            (vault / "20 知识库" / "设计知识").mkdir(parents=True)
            compiler = KnowledgeCompiler(vault)
            bundle = compiler.compile({
                "category": "food",
                "product_name": "牛油果饮品",
                "approved_memory_rules": [
                    {"id": "memory:keep-goblet", "label": "保留器皿", "text": "保持器皿完整，不抠除杯身"},
                ],
            })
            self.assertTrue(
                any(r["text"] == "已批准记忆反馈：保持器皿完整，不抠除杯身" for r in bundle["positive_rules"])
            )
            self.assertTrue(
                any(s["relative_path"] == "记忆反馈/已批准" for s in bundle["sources"])
            )

    def test_malformed_memory_rules_are_ignored_without_breaking_compile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir) / "vault"
            (vault / "20 知识库" / "设计知识").mkdir(parents=True)
            compiler = KnowledgeCompiler(vault)
            bundle = compiler.compile({
                "approved_memory_rules": [
                    None,
                    {"text": ""},
                    {"text": "保持数量不变", "label": "数量一致", "id": "memory:count"},
                    42,
                ],
            })
            self.assertTrue(
                any(r["text"] == "已批准记忆反馈：保持数量不变" for r in bundle["positive_rules"])
            )
            self.assertEqual(
                sum(1 for s in bundle["sources"] if s["relative_path"] == "记忆反馈/已批准"),
                1,
            )

    def test_execution_evidence_freezes_exact_rules_and_sources(self) -> None:
        bundle = {
            "intent_lock_rules": ["保持包装文字"],
            "positive_rules": [{
                "text": "保留玻璃杯身",
                "source": {"id": "memory:cup", "title": "已批准反馈"},
            }],
            "negative_rules": [{"text": "不要使用冷蓝色"}],
            "sources": [{"id": "K-1", "title": "食品主图规则"}],
        }
        evidence = KnowledgeCompiler.execution_evidence(bundle)
        self.assertIn({"kind": "intent_lock", "text": "保持包装文字"}, evidence)
        self.assertTrue(any(
            item["kind"] == "positive_rule" and item["text"] == "保留玻璃杯身"
            for item in evidence
        ))
        self.assertTrue(any(
            item["kind"] == "negative_rule" and item["text"] == "不要使用冷蓝色"
            for item in evidence
        ))
        self.assertTrue(any(
            item["kind"] == "source" and item["source"]["id"] == "K-1"
            for item in evidence
        ))


if __name__ == "__main__":
    unittest.main(verbosity=2)
