from __future__ import annotations

import unittest

from python.semantic_query import (
    contains_cjk,
    load_semantic_query_lexicon,
    resolve_semantic_query,
)


class SemanticQueryTests(unittest.TestCase):
    def test_source_controlled_lexicon_is_valid_and_english_only_on_model_side(self) -> None:
        lexicon = load_semantic_query_lexicon()
        self.assertGreaterEqual(len(lexicon["exact"]), 60)
        self.assertTrue(all(not contains_cjk(value) for value in lexicon["exact"].values()))

    def test_exact_chinese_name_maps_without_network_or_model_calls(self) -> None:
        result = resolve_semantic_query("两个汉堡")
        self.assertEqual(result["status"], "mapped_exact")
        self.assertEqual(result["model_query"], "hamburger")
        self.assertTrue(result["mapped"])
        self.assertIn("两个汉堡", result["message"])

    def test_modifier_and_object_are_composed_auditably(self) -> None:
        result = resolve_semantic_query("红色的包装盒")
        self.assertEqual(result["status"], "mapped_composed")
        self.assertEqual(result["model_query"], "red package box")
        self.assertEqual(result["source_terms"], ["红色", "包装盒"])

    def test_english_input_and_user_override_are_supported(self) -> None:
        direct = resolve_semantic_query("Water Bottle")
        self.assertEqual(direct["status"], "direct_english")
        self.assertEqual(direct["model_query"], "water bottle")
        override = resolve_semantic_query("限定版礼盒", "limited edition gift box")
        self.assertEqual(override["status"], "user_override")
        self.assertEqual(override["model_query"], "limited edition gift box")

    def test_unknown_chinese_and_invalid_override_fail_closed(self) -> None:
        unknown = resolve_semantic_query("火星纪念摆件")
        self.assertEqual(unknown["status"], "unmapped")
        self.assertFalse(unknown["mapped"])
        invalid = resolve_semantic_query("汉堡", "两个汉堡")
        self.assertEqual(invalid["status"], "invalid_override")
        self.assertEqual(invalid["model_query"], "")


if __name__ == "__main__":
    unittest.main()
