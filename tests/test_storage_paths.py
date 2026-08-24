from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from python.storage_paths import (
    OutputRootError,
    job_delivery_directory,
    output_root_status,
    publish_staged_file,
    validate_output_root,
)


class StoragePathTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
