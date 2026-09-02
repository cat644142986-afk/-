from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from python.atelier_ledger import AtelierLedger

_TOP_LEVEL_SEMANTIC_MODULES = {
    "semantic_cutout",
    "semantic_grounding",
    "semantic_mask_eval",
}
_PATH_BEFORE_SEED_IMPORT = tuple(sys.path)
_SEMANTIC_MODULES_BEFORE_SEED_IMPORT = _TOP_LEVEL_SEMANTIC_MODULES.intersection(
    sys.modules
)

seed = importlib.import_module("tools.seed_feedback_checkpoint")

_PATH_AFTER_SEED_IMPORT = tuple(sys.path)
_SEMANTIC_MODULES_AFTER_SEED_IMPORT = _TOP_LEVEL_SEMANTIC_MODULES.intersection(
    sys.modules
)


class SeedFeedbackCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_dir = Path(
            tempfile.mkdtemp(
                prefix=seed.ISOLATED_DATA_PREFIX,
                dir=tempfile.gettempdir(),
            )
        ).resolve(strict=True)

    def tearDown(self) -> None:
        temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
        if self.data_dir.exists() and self.data_dir.parent == temp_root:
            shutil.rmtree(self.data_dir)

    def test_package_import_does_not_pollute_global_module_search_state(self) -> None:
        self.assertEqual(_PATH_AFTER_SEED_IMPORT, _PATH_BEFORE_SEED_IMPORT)
        self.assertEqual(
            _SEMANTIC_MODULES_AFTER_SEED_IMPORT,
            _SEMANTIC_MODULES_BEFORE_SEED_IMPORT,
        )

    def test_seed_uses_real_ledger_and_creates_main_and_cutout_results(self) -> None:
        manifest = seed.seed_feedback_checkpoint(self.data_dir)

        self.assertEqual(manifest["fixture"], "formal-webview-result-review")
        self.assertEqual(len(manifest["jobs"]), 2)
        self.assertEqual(manifest["ledger_schema_version"], 8)
        self.assertFalse((self.data_dir / "config.json").exists())
        self.assertFalse((self.data_dir / seed.SEED_CLAIM_NAME).exists())
        self.assertEqual(
            {
                path.name
                for path in self.data_dir.iterdir()
                if path.name.startswith(f"{seed.LEDGER_NAME}-")
            },
            set(),
        )
        database = self.data_dir / seed.LEDGER_NAME
        self.assertTrue(database.is_file())
        self.assertEqual(database.stat().st_nlink, 1)
        persisted = json.loads(
            (self.data_dir / seed.FIXTURE_MANIFEST_NAME).read_text(encoding="utf-8")
        )
        self.assertEqual(persisted, manifest)

        ledger = AtelierLedger(database)
        stats = ledger.stats()
        self.assertEqual(stats["schema_version"], 8)
        self.assertEqual(stats["counts"]["jobs"], 2)
        self.assertEqual(stats["counts"]["assets"], 6)
        for fixture_job in manifest["jobs"]:
            job = ledger.get_job(fixture_job["job_id"], include_attempts=False)
            self.assertEqual(job["status"], "completed")
            result_ids = job["items"][0]["result_asset_ids"]
            self.assertEqual(
                result_ids,
                [
                    fixture_job["main_result_asset_id"],
                    fixture_job["cutout_result_asset_id"],
                ],
            )
            self.assertEqual(ledger.get_asset(result_ids[0])["role"], "result_main")
            self.assertEqual(ledger.get_asset(result_ids[1])["role"], "result_cutout")
            for file_info in fixture_job["files"]:
                output = self.data_dir / "output" / file_info["path"]
                self.assertEqual(output.stat().st_size, file_info["size"])
                self.assertEqual(
                    hashlib.sha256(output.read_bytes()).hexdigest(), file_info["sha256"]
                )

        serialized = json.dumps(manifest, ensure_ascii=False).casefold()
        for forbidden in ("api_key", "password", "credential", "token", "secret"):
            self.assertNotIn(forbidden, serialized)

    def test_seed_is_fail_closed_for_nonempty_or_replayed_directory(self) -> None:
        (self.data_dir / "config.json").write_text(
            '{"api_key":"must-not-be-read"}', encoding="utf-8"
        )
        with self.assertRaisesRegex(seed.SeedSafetyError, "non-empty"):
            seed.seed_feedback_checkpoint(self.data_dir)

        (self.data_dir / "config.json").unlink()
        seed.seed_feedback_checkpoint(self.data_dir)
        with self.assertRaisesRegex(seed.SeedSafetyError, "non-empty"):
            seed.seed_feedback_checkpoint(self.data_dir)

    def test_seed_rejects_missing_relative_nested_and_wrong_prefix_paths(self) -> None:
        missing = self.data_dir.parent / f"{seed.ISOLATED_DATA_PREFIX}missing-test"
        with self.assertRaisesRegex(seed.SeedSafetyError, "already exist"):
            seed.validate_fresh_isolated_data_dir(missing)
        with self.assertRaisesRegex(seed.SeedSafetyError, "absolute"):
            seed.validate_fresh_isolated_data_dir("relative-fixture")

        nested = self.data_dir / f"{seed.ISOLATED_DATA_PREFIX}nested"
        nested.mkdir()
        with self.assertRaisesRegex(seed.SeedSafetyError, "direct child"):
            seed.validate_fresh_isolated_data_dir(nested)

        wrong_prefix = Path(
            tempfile.mkdtemp(prefix="wrong-prefix-", dir=tempfile.gettempdir())
        )
        try:
            with self.assertRaisesRegex(seed.SeedSafetyError, "prefix"):
                seed.validate_fresh_isolated_data_dir(wrong_prefix)
        finally:
            wrong_prefix.rmdir()

    @unittest.skipIf(os.name == "nt", "Windows coverage uses an unprivileged junction")
    @unittest.skipUnless(hasattr(Path, "symlink_to"), "path symlinks unavailable")
    def test_seed_rejects_a_symlink_root_when_supported(self) -> None:
        link = self.data_dir.parent / f"{seed.ISOLATED_DATA_PREFIX}link-{os.getpid()}"
        try:
            try:
                link.symlink_to(self.data_dir, target_is_directory=True)
            except OSError:
                self.skipTest("This platform does not permit creating a test symlink")
            with self.assertRaisesRegex(seed.SeedSafetyError, "reparse"):
                seed.validate_fresh_isolated_data_dir(link)
        finally:
            if link.is_symlink():
                link.unlink()

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_seed_rejects_a_windows_junction_root(self) -> None:
        link = self.data_dir.parent / (
            f"{seed.ISOLATED_DATA_PREFIX}junction-{os.getpid()}-{id(self)}"
        )
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(self.data_dir)],
            check=False,
            capture_output=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            result.stderr.decode(errors="replace"),
        )
        try:
            with self.assertRaisesRegex(seed.SeedSafetyError, "reparse"):
                seed.validate_fresh_isolated_data_dir(link)
        finally:
            if os.path.lexists(link):
                os.rmdir(link)

    @unittest.skipUnless(os.name == "nt", "Windows directory sharing behavior")
    def test_windows_root_guard_blocks_directory_rename(self) -> None:
        moved = self.data_dir.with_name(f"{self.data_dir.name}-moved")
        try:
            with seed._PinnedDataDirectory(self.data_dir):
                with self.assertRaises(PermissionError) as captured:
                    os.replace(self.data_dir, moved)
                self.assertEqual(captured.exception.winerror, 32)
            self.assertTrue(self.data_dir.is_dir())
            self.assertFalse(moved.exists())
        finally:
            if moved.exists() and not self.data_dir.exists():
                os.replace(moved, self.data_dir)

    def test_root_identity_change_fails_before_business_fixture_writes(self) -> None:
        original_identity = seed._PinnedDataDirectory._current_path_identity
        identity_reads = 0

        def simulate_identity_change(
            guard: seed._PinnedDataDirectory,
        ) -> seed._FileIdentity | tuple[int, int]:
            nonlocal identity_reads
            identity = original_identity(guard)
            identity_reads += 1
            if identity_reads != 2:
                return identity
            if isinstance(identity, seed._FileIdentity):
                return seed._FileIdentity(
                    identity.volume,
                    identity.index_high,
                    identity.index_low ^ 1,
                )
            return (identity[0], identity[1] ^ 1)

        with (
            mock.patch.object(
                seed._PinnedDataDirectory,
                "_current_path_identity",
                simulate_identity_change,
            ),
            self.assertRaisesRegex(seed.SeedSafetyError, "identity changed"),
        ):
            seed.seed_feedback_checkpoint(self.data_dir)

        self.assertEqual(identity_reads, 2)
        self.assertEqual(
            {path.name for path in self.data_dir.iterdir()},
            {seed.SEED_CLAIM_NAME},
        )

    def test_hardlink_injected_before_database_publish_does_not_modify_victim(
        self,
    ) -> None:
        descriptor, victim_name = tempfile.mkstemp(
            prefix="seed-hardlink-victim-", dir=tempfile.gettempdir()
        )
        victim = Path(victim_name)
        canary = b"external database canary\x00" * 128
        try:
            os.write(descriptor, canary)
        finally:
            os.close(descriptor)
        self.addCleanup(victim.unlink, missing_ok=True)

        original_publish = seed._publish_database

        def inject_hardlink(
            guard: seed._PinnedDataDirectory, database: bytes
        ) -> None:
            os.link(victim, guard.path / seed.LEDGER_NAME)
            original_publish(guard, database)

        with (
            mock.patch.object(
                seed, "_publish_database", side_effect=inject_hardlink
            ),
            self.assertRaises((OSError, seed.SeedSafetyError)),
        ):
            seed.seed_feedback_checkpoint(self.data_dir)

        self.assertEqual(victim.read_bytes(), canary)
        self.assertTrue((self.data_dir / seed.SEED_CLAIM_NAME).exists())
        self.assertFalse((self.data_dir / seed.FIXTURE_MANIFEST_NAME).exists())

    def test_in_memory_seed_never_writes_an_injected_sqlite_shm_hardlink(
        self,
    ) -> None:
        descriptor, victim_name = tempfile.mkstemp(
            prefix="seed-shm-victim-", dir=tempfile.gettempdir()
        )
        victim = Path(victim_name)
        canary = b"external shm canary\x00" * 1024
        try:
            os.write(descriptor, canary)
        finally:
            os.close(descriptor)
        self.addCleanup(victim.unlink, missing_ok=True)

        original_publish = seed._publish_database

        def inject_shm_hardlink(
            guard: seed._PinnedDataDirectory, database: bytes
        ) -> None:
            os.link(victim, guard.path / f"{seed.LEDGER_NAME}-shm")
            original_publish(guard, database)

        with (
            mock.patch.object(
                seed, "_publish_database", side_effect=inject_shm_hardlink
            ),
            self.assertRaises((OSError, seed.SeedSafetyError)),
        ):
            seed.seed_feedback_checkpoint(self.data_dir)

        self.assertEqual(victim.read_bytes(), canary)
        self.assertFalse((self.data_dir / seed.FIXTURE_MANIFEST_NAME).exists())

    @unittest.skipUnless(os.name == "nt", "Windows directory sharing behavior")
    def test_business_write_guard_blocks_child_swap_and_reparse_writer(self) -> None:
        original_write = seed._write_guarded_exclusive
        attack_checked = False

        def attack_assets_before_write(
            guard: seed._PinnedDataDirectory, name: str, data: bytes
        ) -> None:
            nonlocal attack_checked
            if guard.path.name == "assets" and not attack_checked:
                attack_checked = True
                moved = guard.path.with_name("assets-moved-by-attacker")
                with self.assertRaises(PermissionError) as rename_error:
                    os.replace(guard.path, moved)
                self.assertEqual(rename_error.exception.winerror, 32)
                with self.assertRaisesRegex(seed.SeedSafetyError, "WinError 32"):
                    seed._open_windows_handle(
                        guard.path,
                        access=seed.GENERIC_WRITE,
                        share=seed.FILE_SHARE_READ | seed.FILE_SHARE_WRITE,
                        creation=seed.OPEN_EXISTING,
                        flags=(
                            seed.FILE_FLAG_BACKUP_SEMANTICS
                            | seed.FILE_FLAG_OPEN_REPARSE_POINT
                        ),
                    )
            original_write(guard, name, data)

        with mock.patch.object(
            seed, "_write_guarded_exclusive", side_effect=attack_assets_before_write
        ):
            manifest = seed.seed_feedback_checkpoint(self.data_dir)

        self.assertTrue(attack_checked)
        self.assertEqual(manifest["ledger_schema_version"], 8)
        self.assertFalse((self.data_dir / "assets-moved-by-attacker").exists())


if __name__ == "__main__":
    unittest.main()
