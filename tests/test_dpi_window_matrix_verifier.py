from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if sys.platform == "win32":
    sys.path.insert(0, str(ROOT / "tools"))
    import verify_dpi_window_matrix as verifier  # noqa: E402
else:
    verifier = None


@unittest.skipUnless(sys.platform == "win32", "Windows-only DWM verifier")
class DpiWindowMatrixVerifierTests(unittest.TestCase):
    def result(self) -> dict[str, object]:
        return {
            "primary_restored": {"dpi": 144},
            "primary_return": {"dpi": 144},
            "secondary_restored": {"dpi": 96},
            "dwm": {
                "corner_query_hresult": 0,
                "corner_preference": 2,
                "window_region_type": 0,
            },
            "minimize_restore": {"minimized": True, "restored": True},
            "maximized": {"is_zoomed": True},
            "restore_after_maximize": {"dpi": 144},
        }

    def test_default_checks_preserve_cross_monitor_gate(self) -> None:
        checks = verifier.build_window_checks(
            self.result(),
            expected_primary_dpi=144,
            expected_secondary_dpi=96,
        )
        self.assertTrue(checks["secondary_dpi_matches"])
        self.assertTrue(all(checks.values()))

    def test_single_monitor_checks_do_not_claim_secondary_coverage(self) -> None:
        result = self.result()
        del result["secondary_restored"]
        checks = verifier.build_window_checks(
            result,
            expected_primary_dpi=144,
            expected_secondary_dpi=None,
        )
        self.assertNotIn("secondary_dpi_matches", checks)
        self.assertTrue(all(checks.values()))

    def test_primary_failures_remain_fatal_in_single_monitor_mode(self) -> None:
        result = self.result()
        result["primary_return"]["dpi"] = 120
        checks = verifier.build_window_checks(
            result,
            expected_primary_dpi=144,
            expected_secondary_dpi=None,
        )
        self.assertFalse(checks["primary_return_dpi_matches"])
        self.assertFalse(all(checks.values()))


if __name__ == "__main__":
    unittest.main()
