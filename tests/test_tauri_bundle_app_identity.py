from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tools" / "tauri_bundle_app_identity.py"
SPEC = importlib.util.spec_from_file_location("tauri_bundle_app_identity", HELPER)
assert SPEC and SPEC.loader
IDENTITY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IDENTITY)


class TauriBundleAppIdentityTests(unittest.TestCase):
    def test_source_identity_derives_only_the_three_byte_unk_to_nss_change(
        self,
    ) -> None:
        source = b"prefix" + IDENTITY.UNKNOWN_MARKER + b"suffix"
        expected = b"prefix" + IDENTITY.NSIS_MARKER + b"suffix"

        result = IDENTITY.derive_nsis_identity_bytes(source)

        self.assertEqual(result["algorithm_version"], IDENTITY.ALGORITHM_VERSION)
        self.assertEqual(
            result["source_app_sha256"], hashlib.sha256(source).hexdigest().upper()
        )
        self.assertEqual(
            result["expected_installed_app_sha256"],
            hashlib.sha256(expected).hexdigest().upper(),
        )
        self.assertEqual(result["source_app_size_bytes"], len(source))
        self.assertEqual(result["expected_installed_app_size_bytes"], len(source))
        self.assertEqual(
            result["source_marker_counts"], {"unknown": 1, "nsis": 0, "msi": 0}
        )
        self.assertEqual(
            result["expected_installed_marker_counts"],
            {"unknown": 0, "nsis": 1, "msi": 0},
        )
        self.assertEqual(result["changed_byte_count"], 3)
        self.assertEqual(len(result["changed_byte_offsets"]), 3)

    def test_source_identity_rejects_missing_or_duplicate_unknown_markers(self) -> None:
        for source in (
            b"no marker",
            IDENTITY.UNKNOWN_MARKER + b":" + IDENTITY.UNKNOWN_MARKER,
        ):
            with (
                self.subTest(source=source),
                self.assertRaises(IDENTITY.BundleIdentityError),
            ):
                IDENTITY.derive_nsis_identity_bytes(source)

    def test_source_identity_rejects_existing_nsis_or_msi_markers(self) -> None:
        for extra_marker in (IDENTITY.NSIS_MARKER, IDENTITY.MSI_MARKER):
            with (
                self.subTest(extra_marker=extra_marker),
                self.assertRaises(IDENTITY.BundleIdentityError),
            ):
                IDENTITY.derive_nsis_identity_bytes(
                    IDENTITY.UNKNOWN_MARKER + b":" + extra_marker
                )

    def test_installed_identity_requires_expected_hash_and_exact_marker_state(
        self,
    ) -> None:
        installed = b"prefix" + IDENTITY.NSIS_MARKER + b"suffix"
        expected_sha256 = hashlib.sha256(installed).hexdigest()

        result = IDENTITY.validate_installed_nsis_bytes(installed, expected_sha256)

        self.assertEqual(result["installed_app_sha256"], expected_sha256.upper())
        self.assertEqual(result["marker_counts"], {"unknown": 0, "nsis": 1, "msi": 0})
        for invalid_data, invalid_hash in (
            (installed, "0" * 64),
            (b"prefix" + IDENTITY.UNKNOWN_MARKER, expected_sha256),
            (IDENTITY.NSIS_MARKER + IDENTITY.NSIS_MARKER, expected_sha256),
            (IDENTITY.NSIS_MARKER + IDENTITY.MSI_MARKER, expected_sha256),
        ):
            with (
                self.subTest(data=invalid_data, expected=invalid_hash),
                self.assertRaises(IDENTITY.BundleIdentityError),
            ):
                IDENTITY.validate_installed_nsis_bytes(invalid_data, invalid_hash)

    def test_cli_source_and_installed_modes_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_path = Path(temp_dir) / "Product Atelier.exe"
            source = b"prefix" + IDENTITY.UNKNOWN_MARKER + b"suffix"
            app_path.write_bytes(source)
            source_result = subprocess.run(
                [
                    sys.executable,
                    str(HELPER),
                    "--mode",
                    "source",
                    "--app",
                    str(app_path),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(source_result.returncode, 0, source_result.stderr)

            installed = source.replace(IDENTITY.UNKNOWN_MARKER, IDENTITY.NSIS_MARKER)
            app_path.write_bytes(installed)
            installed_result = subprocess.run(
                [
                    sys.executable,
                    str(HELPER),
                    "--mode",
                    "installed",
                    "--app",
                    str(app_path),
                    "--expected-sha256",
                    hashlib.sha256(installed).hexdigest(),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(installed_result.returncode, 0, installed_result.stderr)

            rejected = subprocess.run(
                [
                    sys.executable,
                    str(HELPER),
                    "--mode",
                    "source",
                    "--app",
                    str(app_path),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("exactly one UNK", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
