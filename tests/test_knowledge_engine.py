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


if __name__ == "__main__":
    unittest.main(verbosity=2)
