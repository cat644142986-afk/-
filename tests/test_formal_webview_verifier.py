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

    def test_stable_wait_resets_after_a_transient_true_value(self) -> None:
        class SequenceClient:
            def __init__(self) -> None:
                self.values = iter((True, False, True, True, True, True, True, True))
                self.calls = 0

            def evaluate(self, _expression: str) -> bool:
                self.calls += 1
                return next(self.values, True)

        client = SequenceClient()
        clock = {"now": 0.0}

        def perf_counter() -> float:
            return clock["now"]

        def sleep(duration: float) -> None:
            clock["now"] += duration

        with (
            patch.object(verifier.time, "perf_counter", side_effect=perf_counter),
            patch.object(verifier.time, "sleep", side_effect=sleep),
        ):
            self.assertTrue(verifier.wait_for_stable(
                client,
                "ready",
                stable_for=0.008,
                timeout=0.1,
                poll_interval=0.002,
            ))
        self.assertGreaterEqual(client.calls, 6)

    def test_snapshot_passes_only_when_visual_and_accessibility_checks_are_clean(self) -> None:
        snapshot = {
            "documentOverflowX": 0,
            "unnamedControls": [],
            "positiveTabIndex": [],
            "brokenImages": [],
            "boundsIssues": [],
        }
        self.assertTrue(verifier.snapshot_passes(snapshot))

        for field, failure in (
            ("documentOverflowX", 2),
            ("unnamedControls", ["button"]),
            ("positiveTabIndex", ["input"]),
            ("brokenImages", [{"id": "thumbnail"}]),
            ("boundsIssues", [{"id": "page-memory"}]),
        ):
            failing = {**snapshot, field: failure}
            with self.subTest(field=field):
                self.assertFalse(verifier.snapshot_passes(failing))

    def test_keyboard_events_include_windows_virtual_key_codes(self) -> None:
        self.assertEqual(verifier.key_event_params("Enter")["windowsVirtualKeyCode"], 13)
        self.assertEqual(verifier.key_event_params("ArrowRight")["nativeVirtualKeyCode"], 39)
        self.assertEqual(verifier.KEY_CHARACTER_TEXT["Enter"], "\r")
        with self.assertRaises(verifier.VerificationError):
            verifier.key_event_params("Unmapped")


if __name__ == "__main__":
    unittest.main()
