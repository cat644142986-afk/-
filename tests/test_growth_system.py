from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from python.growth_system import GrowthSystem, ObsidianKnowledgeIndex, parse_frontmatter


class FakeLedger:
    def __init__(self) -> None:
        self.feedback = [{
            "id": "fb-approved-layout",
            "session_id": "ses-tea",
            "generation_id": "gen-tea",
            "asset_id": "ast-result-tea",
            "signal": "adopted",
            "reason": "采用克制留白和自然柔光，包装文字保持清楚",
            "structured": {
                "job_id": "job-tea",
                "result_asset_id": "ast-result-tea",
                "reason_codes": ["composition_ready", "packaging_clean"],
            },
            "created_at": "2026-09-24T08:00:00Z",
        }, {
            "id": "fb-rejected-background",
            "session_id": "ses-tea",
            "generation_id": "gen-tea",
            "asset_id": "ast-result-tea",
            "signal": "rejected",
            "reason": "背景方向过暗，品牌包装显得沉闷",
            "structured": {
                "job_id": "job-tea",
                "result_asset_id": "ast-result-tea",
                "reason_codes": ["background_wrong"],
            },
            "created_at": "2026-09-24T09:00:00Z",
        }]

    def list_feedback(self, limit: int = 2000):  # noqa: ARG002
        return list(self.feedback)

    def get_session(self, session_id: str, *, include_timeline: bool = False):  # noqa: ARG002
        if session_id != "ses-tea":
            raise KeyError(session_id)
        return {
            "id": session_id,
            "title": "山茶饮料主图",
            "project_name": "PA Tea Launch",
            "designer_profile": "default",
            "brand_profile": "PA Tea",
            "category": "food",
            "mode": "single",
            "brief": {"style": "clean", "product_name": "山茶饮料"},
        }

    def get_generation(self, generation_id: str):
        if generation_id != "gen-tea":
            raise KeyError(generation_id)
        return {
            "id": generation_id,
            "session_id": "ses-tea",
            "parent_generation_id": "gen-source",
            "parameters": {"output_kind": "ecommerce-main", "style": "clean"},
        }

    def get_asset(self, asset_id: str):
        if asset_id != "ast-result-tea":
            raise KeyError(asset_id)
        return {
            "id": asset_id,
            "session_id": "ses-tea",
            "parent_asset_id": "ast-source-tea",
        }

    def get_job(self, job_id: str, *, include_attempts: bool = False):  # noqa: ARG002
        if job_id != "job-tea":
            raise KeyError(job_id)
        return {
            "id": job_id,
            "session_id": "ses-tea",
            "snapshot": {
                "canvas_document_version_id": "canvas-version-tea",
                "canvas_operation_id": "canvas-operation-tea",
                "parameters": {
                    "execution_context": {
                        "canvas_context": {"document_id": "canvas-tea"},
                    },
                },
            },
        }


class ObsidianIndexTests(unittest.TestCase):
    def test_frontmatter_block_lists_and_wikilinks_are_indexed_read_only(self) -> None:
        text = """---
id: LES-0042
page_type: lesson
review: approved
summary: 食品主图的留白经验
project_scope:
  - PA Tea Launch
tags: [食品, 主图]
---
# 饮料主图经验

- 保持包装四周有克制留白，避免主体顶边

关联 [[品牌调性维度|品牌调性]]。
"""
        metadata, body = parse_frontmatter(text)
        self.assertEqual(metadata["project_scope"], ["PA Tea Launch"])
        self.assertEqual(metadata["tags"], ["食品", "主图"])
        self.assertIn("[[品牌调性维度|品牌调性]]", body)

    def test_only_relevant_approved_pages_compile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            knowledge = root / "20 知识库" / "经验教训"
            knowledge.mkdir(parents=True)
            (knowledge / "tea-approved.md").write_text(
                """---
id: LES-0042
page_type: lesson
review: approved
summary: 食品饮料主图留白
project_scope: [PA Tea Launch]
tags: [食品, 饮料, 主图]
---
# 饮料主图经验
- 保持包装四周有克制留白，避免主体顶边
关联 [[品牌调性维度]]。
""",
                encoding="utf-8",
            )
            (knowledge / "tea-pending.md").write_text(
                """---
id: LES-0043
page_type: lesson
review: pending
summary: 食品饮料暗色背景
project_scope: [PA Tea Launch]
---
# 未审核经验
- 一律使用暗色背景
""",
                encoding="utf-8",
            )
            index = ObsidianKnowledgeIndex(root)
            matches = index.retrieve({
                "project_name": "PA Tea Launch",
                "category": "food",
                "product_name": "山茶饮料",
                "output_kind": "ecommerce-main",
            })
            status = index.status()

        self.assertEqual(status["approved_count"], 1)
        self.assertEqual(status["pending_count"], 1)
        self.assertEqual([item["id"] for item in matches], ["LES-0042"])
        self.assertEqual(matches[0]["wikilinks"], ["品牌调性维度"])


class GrowthCaseProjectionTests(unittest.TestCase):
    def test_relevant_cases_reference_existing_evidence_without_copying_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "20 知识库").mkdir(parents=True)
            growth = GrowthSystem(FakeLedger(), root)
            compiled = growth.compile({
                "project_name": "PA Tea Launch",
                "brand_profile": "PA Tea",
                "category": "food",
                "product_name": "山茶饮料",
                "user_request": "生成干净的电商主图",
                "mode": "single",
                "intent_locks": {"packaging_text": True, "logo": True},
            })

        self.assertEqual(len(compiled["cases"]), 2)
        self.assertEqual(
            {item["case_id"] for item in compiled["cases"]},
            {"case:fb-approved-layout", "case:fb-rejected-background"},
        )
        self.assertTrue(any(
            "克制留白" in item["text"] for item in compiled["case_rules"]
        ))
        self.assertTrue(any(
            "背景方向过暗" in item["text"] for item in compiled["case_rules"]
        ))
        for case in compiled["cases"]:
            self.assertEqual(case["result_asset_id"], "ast-result-tea")
            self.assertEqual(case["parent_asset_id"], "ast-source-tea")
            self.assertEqual(case["canvas_document_id"], "canvas-tea")
            self.assertEqual(case["canvas_document_version_id"], "canvas-version-tea")
            self.assertNotIn("path", case)
            self.assertNotIn("bytes", case)

    def test_task_locks_win_over_conflicting_case(self) -> None:
        ledger = FakeLedger()
        ledger.feedback = [{
            **ledger.feedback[0],
            "id": "fb-remove-logo",
            "reason": "采用去除 Logo 和包装文字的极简版本",
        }]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "20 知识库").mkdir(parents=True)
            growth = GrowthSystem(ledger, root)
            compiled = growth.compile({
                "project_name": "PA Tea Launch",
                "brand_profile": "PA Tea",
                "category": "food",
                "intent_locks": {"packaging_text": True, "logo": True},
            })

        self.assertEqual(compiled["case_rules"], [])
        self.assertTrue(any(
            item["reason"] == "conflicts-with-task" for item in compiled["ignored_rules"]
        ))
        self.assertTrue(any(item["winner"] == "task" for item in compiled["conflicts"]))

    def test_relevant_case_survives_existing_prompt_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "20 知识库").mkdir(parents=True)
            growth = GrowthSystem(FakeLedger(), root)
            compiled = growth.compile({
                "project_name": "PA Tea Launch",
                "brand_profile": "PA Tea",
                "category": "food",
            })
            base = {
                "positive_rules": [
                    {"id": f"generic-{index}", "text": f"通用规则 {index}"}
                    for index in range(10)
                ],
                "negative_rules": [
                    {"id": f"negative-{index}", "text": f"通用避坑 {index}"}
                    for index in range(10)
                ],
            }
            attached = growth.attach_cases(base, compiled)
            reserved = growth.reserve_relevant_context(attached, prompt_version="prompt_v1")
            _, snapshot = growth.finalize_snapshot(reserved, compiled)

        positive_ids = {item["id"] for item in reserved["positive_rules"][:10]}
        negative_ids = {item["id"] for item in reserved["negative_rules"][:10]}
        self.assertIn("case:fb-approved-layout", positive_ids)
        self.assertIn("case:fb-rejected-background", negative_ids)
        self.assertEqual(snapshot["status"], "applied")
        self.assertTrue(all(item["applied"] for item in snapshot["cases"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
