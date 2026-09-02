from __future__ import annotations

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"

if sys.platform == "win32":
    sys.path.insert(0, str(TOOLS))
    import capture_startup_video as startup_video
else:
    startup_video = None


class CandidateIsolationEntrySourceTests(unittest.TestCase):
    def test_python_launches_use_complete_isolation_environment_builders(self) -> None:
        expected_calls = {
            "capture_startup_video.py": "build_candidate_isolation_environment",
            "verify_dpi_window_matrix.py": "build_candidate_isolation_environment",
            "sample_packaged_startups.py": "build_candidate_isolation_environment",
            "verify_packaged_schema_upgrade.py": "_build_candidate_isolation_environment",
        }
        for filename, expected_call in expected_calls.items():
            with self.subTest(filename=filename):
                source = (TOOLS / filename).read_text(encoding="utf-8")
                tree = ast.parse(source, filename=filename)
                called_names = {
                    node.func.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                }
                self.assertIn(expected_call, called_names)
                if filename in {
                    "capture_startup_video.py",
                    "verify_packaged_schema_upgrade.py",
                }:
                    self.assertIn("PRODUCT_ATELIER_CANDIDATE_ISOLATION", source)
                    self.assertIn("PRODUCT_ATELIER_DATA_DIR", source)
                    self.assertIn("PRODUCT_ATELIER_WEBVIEW_DATA_DIR", source)
                    self.assertIn("PRODUCT_ATELIER_LEGACY_CONFIG", source)
                    self.assertIn("PRODUCT_ATELIER_KNOWLEDGE_BASE", source)

    def test_portable_app_smoke_is_bom_crlf_and_restores_all_isolation_variables(self) -> None:
        raw = (TOOLS / "Test-Portable-App.ps1").read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\n", raw[3:].replace(b"\r\n", b""))
        source = raw.decode("utf-8-sig")
        self.assertIn("webview2-user-data", source)
        self.assertIn("no-legacy-config.json", source)
        for variable_name in (
            "PRODUCT_ATELIER_DATA_DIR",
            "PRODUCT_ATELIER_WEBVIEW_DATA_DIR",
            "PRODUCT_ATELIER_LEGACY_CONFIG",
        ):
            self.assertGreaterEqual(source.count(f"$env:{variable_name}"), 3)


@unittest.skipUnless(sys.platform == "win32", "Windows-only startup tooling")
class CandidateIsolationEnvironmentTests(unittest.TestCase):
    def test_shared_gui_environment_is_absolute_nested_and_fail_closed(self) -> None:
        inherited = {
            "PRODUCT_ATELIER_DATA_DIR": "unsafe-data",
            "PRODUCT_ATELIER_WEBVIEW_DATA_DIR": "unsafe-webview",
            "PRODUCT_ATELIER_LEGACY_CONFIG": "unsafe-legacy-config",
            "PRODUCT_ATELIER_KNOWLEDGE_BASE": "unsafe-knowledge",
            "WEBVIEW2_USER_DATA_FOLDER": "unsafe-webview-profile",
            "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": "--remote-debugging-port=1",
        }
        with (
            tempfile.TemporaryDirectory(
                prefix="ProductAtelier-gui-environment-"
            ) as temporary_dir,
            mock.patch.dict(os.environ, inherited),
        ):
            data_dir = Path(temporary_dir).resolve()
            environment = startup_video.build_candidate_isolation_environment(data_dir)

            actual_data = Path(environment["PRODUCT_ATELIER_DATA_DIR"])
            actual_webview = Path(environment["PRODUCT_ATELIER_WEBVIEW_DATA_DIR"])
            actual_legacy = Path(environment["PRODUCT_ATELIER_LEGACY_CONFIG"])
            actual_knowledge = Path(environment["PRODUCT_ATELIER_KNOWLEDGE_BASE"])
            self.assertEqual(actual_data, data_dir)
            self.assertEqual(actual_webview, data_dir / "webview2-user-data")
            self.assertEqual(actual_legacy, data_dir / "no-legacy-config.json")
            self.assertEqual(actual_knowledge, data_dir / "no-knowledge-vault")
            self.assertEqual(environment["PRODUCT_ATELIER_CANDIDATE_ISOLATION"], "1")
            self.assertEqual(environment["WEBVIEW2_USER_DATA_FOLDER"], str(actual_webview))
            self.assertFalse(
                any(
                    name.casefold() == "webview2_additional_browser_arguments"
                    for name in environment
                )
            )
            self.assertTrue(
                all(
                    path.is_absolute()
                    for path in (
                        actual_data,
                        actual_webview,
                        actual_legacy,
                        actual_knowledge,
                    )
                )
            )
            self.assertTrue(actual_data.is_dir())
            self.assertTrue(actual_webview.is_dir())
            self.assertTrue(actual_knowledge.is_dir())
            self.assertFalse(actual_legacy.exists())

            actual_legacy.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "must not exist"):
                startup_video.build_candidate_isolation_environment(data_dir)

    def test_sidecar_smoke_isolates_legacy_config_and_knowledge(self) -> None:
        raw = (TOOLS / "Test-Portable.ps1").read_bytes()
        source = raw.decode("utf-8-sig")
        self.assertIn("no-legacy-config.json", source)
        self.assertIn("no-knowledge-vault", source)
        for variable_name in (
            "PRODUCT_ATELIER_DATA_DIR",
            "PRODUCT_ATELIER_LEGACY_CONFIG",
            "PRODUCT_ATELIER_KNOWLEDGE_BASE",
        ):
            self.assertGreaterEqual(source.count(f"$env:{variable_name}"), 3)


if __name__ == "__main__":
    unittest.main()
