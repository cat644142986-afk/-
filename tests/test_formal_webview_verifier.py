from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import verify_formal_webview as verifier  # noqa: E402


class FormalWebViewVerifierTests(unittest.TestCase):
    def test_parse_size_rejects_malformed_and_unsupported_dimensions(self) -> None:
        self.assertEqual(verifier.parse_size("1280x720"), (1280, 720))
        with self.assertRaises(Exception):
            verifier.parse_size("1280")
        with self.assertRaises(Exception):
            verifier.parse_size("640x480")

    def test_matrix_cases_builds_the_explicit_cross_product(self) -> None:
        cases = verifier.matrix_cases(
            ((960, 600), (1280, 720)),
            ("light", "dark"),
            ("single", "growth"),
        )
        self.assertEqual(len(cases), 8)
        self.assertEqual(cases[0], ((960, 600), "light", "single"))
        self.assertEqual(cases[-1], ((1280, 720), "dark", "growth"))

    def test_memory_summary_uses_p50_and_peak_per_native_group(self) -> None:
        report = verifier.summarize_memory_samples([
            {"app_shell": 10, "webview2": 100, "sidecar": 20, "other": 0, "total": 130},
            {"app_shell": 12, "webview2": 80, "sidecar": 24, "other": 2, "total": 118},
            {"app_shell": 11, "webview2": 90, "sidecar": 22, "other": 1, "total": 124},
        ])
        self.assertEqual(report["webview2_p50_bytes"], 90)
        self.assertEqual(report["webview2_peak_bytes"], 100)
        self.assertEqual(report["total_p50_bytes"], 124)
        self.assertEqual(report["total_peak_bytes"], 130)

    def test_paid_and_mutating_controls_are_guarded(self) -> None:
        selectors = set(verifier.DANGEROUS_SELECTORS)
        self.assertIn("#btn-generate", selectors)
        self.assertIn("#btn-retry", selectors)
        self.assertIn("#btn-save-all", selectors)
        self.assertIn("#btn-feedback", selectors)
        self.assertIn("#btn-review-suggest", selectors)
        self.assertIn("#asset-purge-action", selectors)
        self.assertIn("#asset-bulk-action", selectors)
        self.assertIn("[data-remove-asset-id]", selectors)
        self.assertIn("[data-memory-action]", selectors)
        self.assertIn("[data-job-action='retry-failed']", selectors)
        self.assertIn("[data-purge-asset]", selectors)

    def test_non_windows_runtime_fails_only_when_windows_features_are_requested(self) -> None:
        with patch.object(verifier.os, "name", "posix"):
            with self.assertRaisesRegex(verifier.VerificationError, "Windows only"):
                verifier.load_windows_runtime()

    def test_keyboard_events_include_windows_virtual_key_codes(self) -> None:
        self.assertEqual(verifier.key_event_params("Enter")["windowsVirtualKeyCode"], 13)
        self.assertEqual(verifier.key_event_params("ArrowRight")["nativeVirtualKeyCode"], 39)
        self.assertEqual(verifier.KEY_CHARACTER_TEXT["Enter"], "\r")
        with self.assertRaises(verifier.VerificationError):
            verifier.key_event_params("Unmapped")


if __name__ == "__main__":
    unittest.main()
