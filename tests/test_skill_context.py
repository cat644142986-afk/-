from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from python.skill_context import (
    CONTEXT_SKILL_ADAPTER_VERSION,
    ContextSkillError,
    attach_context_skill,
    context_skill_status,
    finalize_context_skill,
    resolve_context_skill,
)


SKILL_ID = "comfyui-food-product-main-image"
SKILL_TEXT = """---
name: comfyui-food-product-main-image
description: test fixture
---

# ComfyUI 电商食品饮料主图生成技能

极简纯白背景，产品居中。
柔和柔光箱影棚光线，软阴影。
自然真实色彩，材质清晰。
原始模板包含无文字水印要求，但适配器不得带入文字删除规则。

脚本示例：scripts/should-not-run.py
"""


def write_skill(root: Path, text: str = SKILL_TEXT) -> Path:
    path = root / SKILL_ID / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class ContextSkillAdapterTests(unittest.TestCase):
    def test_allowlisted_skill_freezes_content_and_only_emits_reviewed_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = write_skill(root)
            script_marker = root / "script-was-run.txt"
            (path.parent / "scripts").mkdir()
            (path.parent / "scripts" / "should-not-run.py").write_text(
                f"from pathlib import Path\nPath({str(script_marker)!r}).write_text('bad')\n",
                encoding="utf-8",
            )

            resolved = resolve_context_skill(SKILL_ID, mode="single", skill_root=root)

            expected_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot = resolved["snapshot"]
            self.assertEqual(expected_hash, snapshot["content_sha256"])
            self.assertEqual(f"content-{expected_hash[:12]}", snapshot["version"])
            self.assertEqual(CONTEXT_SKILL_ADAPTER_VERSION, snapshot["adapter_version"])
            self.assertEqual("context-only", snapshot["mode"])
            self.assertFalse(script_marker.exists())
            rule_text = "\n".join(rule["text"] for rule in resolved["rules"])
            self.assertNotIn("无文字", rule_text)
            self.assertNotIn("ComfyUI", rule_text)
            self.assertNotIn("Provider", rule_text)
            self.assertNotIn(str(root), str(resolved))

    def test_unknown_missing_changed_and_unsupported_sources_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(ContextSkillError, "允许列表") as unknown:
                resolve_context_skill("unknown-skill", mode="single", skill_root=root)
            self.assertEqual("SKILL_CONTEXT_NOT_ALLOWED", unknown.exception.code)

            with self.assertRaises(ContextSkillError) as missing:
                resolve_context_skill(SKILL_ID, mode="single", skill_root=root)
            self.assertEqual("SKILL_CONTEXT_UNAVAILABLE", missing.exception.code)

            write_skill(root, SKILL_TEXT.replace("自然真实色彩", "风格化色彩"))
            with self.assertRaises(ContextSkillError) as changed:
                resolve_context_skill(SKILL_ID, mode="single", skill_root=root)
            self.assertEqual("SKILL_CONTEXT_INCOMPATIBLE", changed.exception.code)

            write_skill(root)
            with self.assertRaises(ContextSkillError) as unsupported:
                resolve_context_skill(SKILL_ID, mode="group-split", skill_root=root)
            self.assertEqual("SKILL_CONTEXT_UNSUPPORTED_MODE", unsupported.exception.code)

    def test_identity_remains_bound_when_prompt_budget_omits_skill_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_skill(root)
            resolved = resolve_context_skill(SKILL_ID, mode="single", skill_root=root)
            bundle = attach_context_skill(
                {"positive_rules": [], "negative_rules": [], "sources": []},
                resolved,
            )
            governed = {**bundle, "positive_rules": [], "sources": []}

            finalized, snapshot = finalize_context_skill(governed, resolved)

            self.assertEqual("selected-not-applied", snapshot["status"])
            self.assertEqual([], snapshot["applied_rule_ids"])
            self.assertEqual(snapshot["selected_rule_ids"], snapshot["ignored_rule_ids"])
            self.assertEqual(resolved["source"], finalized["sources"][0])
            self.assertIn(snapshot["content_sha256"], finalized["sources"][0]["id"])
            self.assertIn(snapshot["adapter_version"], finalized["sources"][0]["id"])

    def test_status_is_non_sensitive_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_skill(root)
            status = context_skill_status(skill_root=root)

            self.assertTrue(status["available"])
            self.assertTrue(status["read_only"])
            self.assertEqual(["single", "multi-file"], status["supported_modes"])
            self.assertNotIn(str(root), str(status))


if __name__ == "__main__":
    unittest.main(verbosity=2)
