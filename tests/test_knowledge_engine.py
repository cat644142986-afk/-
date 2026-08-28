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
