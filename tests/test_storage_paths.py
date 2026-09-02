from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from python.storage_paths import (
    OutputRootError,
    canonicalize_output_root,
    job_delivery_directory,
    native_io_path,
    output_root_status,
    publish_staged_file,
    validate_output_root,
)


class StoragePathTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows extended paths only")
    def test_native_io_path_uses_extended_length_windows_syntax(self) -> None:
        local = native_io_path(Path("C:/") / ("a" * 270))
        unc = native_io_path(Path("//server/share") / ("b" * 270))

        self.assertTrue(local.startswith("\\\\?\\C:\\"))
        self.assertTrue(unc.startswith("\\\\?\\UNC\\server\\share\\"))

    def test_validation_accepts_a_writable_folder_and_rejects_unsafe_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            default = root / "internal" / "output"
            default.mkdir(parents=True)
            chosen = root / "deliveries"
            chosen.mkdir()
            protected = root / "knowledge"
            protected.mkdir()

            self.assertEqual(
                validate_output_root(
                    chosen,
                    default_root=default,
                    protected_roots=(protected, default.parent),
                    test_write=True,
                ),
                chosen.resolve(),
            )
            self.assertFalse(any(chosen.iterdir()))
            with self.assertRaises(OutputRootError) as protected_error:
                validate_output_root(
                    protected / "generated",
                    default_root=default,
                    protected_roots=(protected,),
                    require_available=False,
                )
            self.assertEqual(protected_error.exception.code, "OUTPUT_ROOT_PROTECTED")
            with self.assertRaises(OutputRootError) as relative_error:
                validate_output_root("relative/output", default_root=default)
            self.assertEqual(relative_error.exception.code, "OUTPUT_ROOT_NOT_ABSOLUTE")

    def test_unavailable_root_has_stable_chinese_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing"
            status = output_root_status(missing, default_root=Path(temp_dir) / "default")
            self.assertFalse(status["available"])
            self.assertEqual(status["code"], "OUTPUT_ROOT_UNAVAILABLE")
            self.assertIn("磁盘", str(status["message"]))

    def test_delivery_path_classifies_by_date_mode_job_and_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = job_delivery_directory(
                temp_dir,
                created_at="2026-08-24T12:00:00+08:00",
                mode="cutout-batch",
                job_id="job:alpha",
                item_id="item/beta",
                item_position=2,
                attempt=3,
            )
            relative = path.relative_to(Path(temp_dir).resolve())
            self.assertEqual(relative.parts[0:2], ("2026-08-24", "04_批量抠图"))
            self.assertEqual(relative.parts[-1], "attempt-3")
            self.assertTrue(relative.parts[-2].startswith("003-"))

    def test_publish_staged_file_replaces_atomically_and_removes_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "stage" / "result.jpg"
            source.parent.mkdir()
            source.write_bytes(b"finished-result")
            target = root / "delivery" / "result.jpg"
            published = publish_staged_file(source, target)
            self.assertEqual(published.read_bytes(), b"finished-result")
            self.assertFalse(source.exists())
            self.assertFalse(any(target.parent.glob(".*.tmp")))

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior only")
    def test_deep_junction_resolves_before_output_root_safety_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            allowed = root / "allowed"
            outside = root / "outside"
            allowed.mkdir()
            outside.mkdir()
            deep_allowed = allowed / Path(*(
                f"deep-{index}-{'d' * 38}"
                for index in range(5)
            ))
            os.makedirs(native_io_path(deep_allowed), exist_ok=True)
            junction = deep_allowed / "escape"
            self.assertGreater(len(str(junction)), 260)
            created = subprocess.run(
                [
                    "cmd.exe",
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    native_io_path(junction),
                    native_io_path(outside),
                ],
                capture_output=True,
                text=False,
                check=False,
            )
            if created.returncode != 0:
                shutil.rmtree(native_io_path(allowed))
                shutil.rmtree(native_io_path(outside))
                self.skipTest("directory junction creation is unavailable")

            tail = Path("result.png")
            physical = outside / tail
            try:
                os.makedirs(native_io_path(physical.parent), exist_ok=True)
                with open(native_io_path(physical), "wb") as handle:
                    handle.write(b"junction-boundary-probe")
                lexical = junction / tail
                self.assertGreater(len(str(lexical)), 260)

                resolved = canonicalize_output_root(lexical, strict=True)
                resolved_allowed = canonicalize_output_root(allowed, strict=True)
                resolved_outside = canonicalize_output_root(outside, strict=True)
                self.assertFalse(resolved.is_relative_to(resolved_allowed))
                self.assertTrue(resolved.is_relative_to(resolved_outside))

                with self.assertRaises(OutputRootError) as protected_error:
                    validate_output_root(
                        junction,
                        default_root=allowed / "default",
                        protected_roots=(outside,),
                    )
                self.assertEqual(protected_error.exception.code, "OUTPUT_ROOT_PROTECTED")
            finally:
                os.rmdir(native_io_path(junction))
                if os.path.exists(native_io_path(allowed)):
                    shutil.rmtree(native_io_path(allowed))
                if os.path.exists(native_io_path(outside)):
                    shutil.rmtree(native_io_path(outside))


if __name__ == "__main__":
    unittest.main(verbosity=2)
