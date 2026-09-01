from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import portable_release  # noqa: E402


class PortableReleaseTests(unittest.TestCase):
    def test_formal_entrypoint_runs_schema_migration_gate_before_promotion(self) -> None:
        script = (ROOT / "tools" / "dev.ps1").read_text(encoding="utf-8-sig")
        candidate_smoke = script.index('Write-Host "[9/11] Smoking the isolated candidate..."')
        migration_gate = script.index('"test_schema_v4_candidate.py"')
        promotion = script.index('Write-Host "[10/11] Backing up and promoting')
        self.assertLess(candidate_smoke, migration_gate)
        self.assertLess(migration_gate, promotion)

    commit = "a" * 40

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "ProductAtelier-Desktop"
        self.project.mkdir()
        (self.project / "package.json").write_text("{}", encoding="utf-8")
        (self.project / "src-tauri").mkdir()
        (self.project / "src-tauri" / "tauri.conf.json").write_text("{}", encoding="utf-8")
        (self.project / "build").mkdir()
        (self.project / "release").mkdir()
        self.app_source = self.root / "product-atelier.exe"
        self.app_source.write_bytes(b"new-tauri-shell")
        self.sidecar_source = self.root / "python-server"
        self._write_sidecar(self.sidecar_source, self.commit)
        self.candidate = self.project / "build" / "portable-candidate-current"
        self.formal = self.project / "release" / "ProductAtelier-Portable"
        self.backup = self.root / "ProductAtelier-Backups" / "release-before-test"
        self.transaction = self.project / "build" / "portable-promotion-transaction.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write_sidecar(path: Path, commit: str) -> None:
        path.mkdir(parents=True)
        executable = path / "python-server.exe"
        executable.write_bytes(b"new-python-sidecar")
        source_hashes = {"python/server.py": "TEST"}
        fingerprint_text = "\n".join(
            f"{source_path}:{source_hash}" for source_path, source_hash in source_hashes.items()
        )
        manifest = {
            "product": "Product Atelier",
            "contract_version": "test-contract",
            "ledger_schema_version": 3,
            "git_commit": commit,
            "source_fingerprint": hashlib.sha256(fingerprint_text.encode("utf-8")).hexdigest().upper(),
            "built_at": "2026-08-28T00:00:00Z",
            "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest().upper(),
            "source_hashes": source_hashes,
        }
        (path / "sidecar-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    def _stage(self) -> dict:
        return portable_release.stage_candidate(
            project_root=self.project,
            app_exe=self.app_source,
            sidecar_dir=self.sidecar_source,
            candidate_dir=self.candidate,
            expected_git_commit=self.commit,
        )

    def test_project_root_breadth_allows_a_windows_drive_child_only(self) -> None:
        windows_home = PureWindowsPath("C:/Users/designer")
        self.assertFalse(portable_release._is_broad_project_root(
            PureWindowsPath("D:/ProductAtelier-Desktop"), windows_home
        ))
        self.assertTrue(portable_release._is_broad_project_root(
            PureWindowsPath("D:/"), windows_home
        ))
        self.assertTrue(portable_release._is_broad_project_root(
            windows_home, windows_home
        ))

        posix_home = PurePosixPath("/Users/designer")
        self.assertFalse(portable_release._is_broad_project_root(
            PurePosixPath("/workspace/ProductAtelier-Desktop"), posix_home
        ))
        self.assertTrue(portable_release._is_broad_project_root(
            PurePosixPath("/"), posix_home
        ))

    def _write_old_formal(self) -> dict:
        self.formal.mkdir(parents=True)
        (self.formal / "Product Atelier.exe").write_bytes(b"old-formal-shell")
        (self.formal / "keep-me.txt").write_text("recoverable", encoding="utf-8")
        return portable_release.directory_inventory(self.formal)

    def _begin(self) -> dict:
        return portable_release.begin_promotion(
            project_root=self.project,
            candidate_dir=self.candidate,
            portable_dir=self.formal,
            backup_dir=self.backup,
            transaction_path=self.transaction,
            expected_git_commit=self.commit,
        )

    def test_stage_promote_and_finalize_keep_a_verified_backup(self) -> None:
        candidate = self._stage()
        old_inventory = self._write_old_formal()

        transaction = self._begin()
        self.assertEqual(transaction["phase"], "promoted")
        self.assertEqual(
            portable_release.directory_inventory(self.formal), candidate["inventory"]
        )
        self.assertEqual(
            portable_release.directory_inventory(self.backup), old_inventory
        )
        self.assertTrue(self.transaction.is_file())

        evidence = portable_release.finalize_promotion(
            project_root=self.project, transaction_path=self.transaction
        )
        self.assertEqual(evidence["status"], "finalized")
        self.assertEqual(evidence["git_commit"], self.commit)
        self.assertFalse(self.transaction.exists())
        self.assertFalse(Path(transaction["previous_dir"]).exists())
        evidence_path = Path(evidence["evidence_path"])
        self.assertTrue(evidence_path.is_file())
        stored = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["candidate"]["artifacts"]["git_commit"], self.commit)
        self.assertEqual(
            portable_release.directory_inventory(self.backup), old_inventory
        )

    def test_smoke_failure_rollback_restores_the_previous_formal_release(self) -> None:
        self._stage()
        old_inventory = self._write_old_formal()
        self._begin()

        evidence = portable_release.rollback_promotion(
            project_root=self.project,
            transaction_path=self.transaction,
            reason="synthetic formal smoke failure",
        )

        self.assertEqual(evidence["status"], "rolled_back")
        self.assertEqual(
            portable_release.directory_inventory(self.formal), old_inventory
        )
        self.assertFalse(self.transaction.exists())
        failed_candidate = Path(evidence["failed_candidate_dir"])
        self.assertTrue((failed_candidate / "python-server" / "python-server.exe").is_file())
        self.assertEqual(
            portable_release.directory_inventory(self.backup), old_inventory
        )

    def test_candidate_validation_failure_never_touches_the_formal_release(self) -> None:
        old_inventory = self._write_old_formal()
        self.candidate.mkdir()
        (self.candidate / "Product Atelier.exe").write_bytes(b"incomplete")

        with self.assertRaises(portable_release.ReleaseError):
            self._begin()

        self.assertEqual(
            portable_release.directory_inventory(self.formal), old_inventory
        )
        self.assertFalse(self.backup.exists())
        self.assertFalse(self.transaction.exists())

    def test_mid_promotion_failure_is_automatically_rolled_back(self) -> None:
        self._stage()
        old_inventory = self._write_old_formal()
        real_replace = os.replace
        failed = False

        def fail_candidate_swap(source, destination):
            nonlocal failed
            source_path = Path(source)
            destination_path = Path(destination)
            if (
                not failed
                and source_path.name.startswith(".ProductAtelier-Portable.replacement-")
                and destination_path.resolve(strict=False)
                == self.formal.resolve(strict=False)
            ):
                failed = True
                raise OSError("synthetic promotion swap failure")
            return real_replace(source, destination)

        with mock.patch.object(portable_release.os, "replace", side_effect=fail_candidate_swap):
            with self.assertRaises(OSError):
                self._begin()

        self.assertTrue(failed)
        self.assertEqual(
            portable_release.directory_inventory(self.formal), old_inventory
        )
        self.assertFalse(self.transaction.exists())
        self.assertEqual(
            portable_release.directory_inventory(self.backup), old_inventory
        )

    def test_manifest_commit_mismatch_is_rejected_during_staging(self) -> None:
        mismatched_sidecar = self.root / "mismatched-sidecar"
        self._write_sidecar(mismatched_sidecar, "b" * 40)

        with self.assertRaises(portable_release.ReleaseError):
            portable_release.stage_candidate(
                project_root=self.project,
                app_exe=self.app_source,
                sidecar_dir=mismatched_sidecar,
                candidate_dir=self.candidate,
                expected_git_commit=self.commit,
            )

        self.assertFalse(self.candidate.exists())

    def test_stage_refuses_a_candidate_outside_project_build(self) -> None:
        outside = self.root / "unrelated" / "portable-candidate-current"

        with self.assertRaises(portable_release.ReleaseError):
            portable_release.stage_candidate(
                project_root=self.project,
                app_exe=self.app_source,
                sidecar_dir=self.sidecar_source,
                candidate_dir=outside,
                expected_git_commit=self.commit,
            )

        self.assertFalse(outside.exists())

    def test_initial_promotion_rolls_back_if_journal_write_fails_after_swap(self) -> None:
        self._stage()
        real_write = portable_release._write_json_atomic

        def fail_promoted_write(path, payload):
            if payload.get("phase") == "promoted":
                raise OSError("synthetic journal failure after initial swap")
            return real_write(path, payload)

        with mock.patch.object(
            portable_release, "_write_json_atomic", side_effect=fail_promoted_write
        ):
            with self.assertRaises(OSError):
                self._begin()

        self.assertFalse(self.formal.exists())
        self.assertFalse(self.transaction.exists())
        failed = list((self.project / "build").glob("failed-portable-candidate-*"))
        self.assertEqual(len(failed), 1)
        self.assertTrue((failed[0] / "Product Atelier.exe").is_file())

    def test_finalize_evidence_failure_is_resumable_and_not_rollbackable(self) -> None:
        self._stage()
        self._write_old_formal()
        transaction = self._begin()

        with mock.patch.object(
            portable_release,
            "_record_evidence",
            side_effect=OSError("synthetic evidence failure"),
        ):
            with self.assertRaises(OSError):
                portable_release.finalize_promotion(
                    project_root=self.project, transaction_path=self.transaction
                )

        journal = json.loads(self.transaction.read_text(encoding="utf-8"))
        self.assertEqual(journal["phase"], "finalizing")
        self.assertTrue(Path(transaction["previous_dir"]).exists())
        with self.assertRaises(portable_release.ReleaseError):
            portable_release.rollback_promotion(
                project_root=self.project,
                transaction_path=self.transaction,
                reason="must not reverse a committed finalization decision",
            )

        evidence = portable_release.finalize_promotion(
            project_root=self.project, transaction_path=self.transaction
        )
        self.assertEqual(evidence["status"], "finalized")
        self.assertFalse(self.transaction.exists())
        self.assertFalse(Path(transaction["previous_dir"]).exists())

    def test_finalize_cleanup_failure_can_be_retried_after_previous_is_removed(self) -> None:
        self._stage()
        self._write_old_formal()
        transaction = self._begin()
        real_remove = portable_release._remove_generated_tree
        failed = False

        def remove_then_fail(path, allowed_root, prefix):
            nonlocal failed
            real_remove(path, allowed_root, prefix)
            if not failed and ".ProductAtelier-Portable.previous-" in Path(path).name:
                failed = True
                raise OSError("synthetic cleanup interruption")

        with mock.patch.object(
            portable_release, "_remove_generated_tree", side_effect=remove_then_fail
        ):
            with self.assertRaises(OSError):
                portable_release.finalize_promotion(
                    project_root=self.project, transaction_path=self.transaction
                )

        self.assertTrue(failed)
        self.assertFalse(Path(transaction["previous_dir"]).exists())
        self.assertEqual(
            json.loads(self.transaction.read_text(encoding="utf-8"))["phase"],
            "finalized",
        )
        evidence = portable_release.finalize_promotion(
            project_root=self.project, transaction_path=self.transaction
        )
        self.assertEqual(evidence["status"], "finalized")
        self.assertFalse(self.transaction.exists())

    def test_rollback_restores_from_durable_backup_if_previous_is_missing(self) -> None:
        self._stage()
        old_inventory = self._write_old_formal()
        transaction = self._begin()
        shutil.rmtree(Path(transaction["previous_dir"]))

        portable_release.rollback_promotion(
            project_root=self.project,
            transaction_path=self.transaction,
            reason="synthetic lost previous directory",
        )

        self.assertEqual(portable_release.directory_inventory(self.formal), old_inventory)
        self.assertFalse(self.transaction.exists())

    def test_rollback_can_resume_between_candidate_removal_and_previous_restore(self) -> None:
        self._stage()
        old_inventory = self._write_old_formal()
        transaction = self._begin()
        previous_path = Path(transaction["previous_dir"])
        real_replace = os.replace
        failed = False

        def fail_previous_restore(source, destination):
            nonlocal failed
            if (
                not failed
                and Path(source).resolve(strict=False) == previous_path.resolve(strict=False)
                and Path(destination).resolve(strict=False) == self.formal.resolve(strict=False)
            ):
                failed = True
                raise OSError("synthetic rollback interruption")
            return real_replace(source, destination)

        with mock.patch.object(
            portable_release.os, "replace", side_effect=fail_previous_restore
        ):
            with self.assertRaises(OSError):
                portable_release.rollback_promotion(
                    project_root=self.project,
                    transaction_path=self.transaction,
                    reason="synthetic interrupted rollback",
                )

        self.assertTrue(failed)
        self.assertFalse(self.formal.exists())
        self.assertTrue(previous_path.exists())
        portable_release.rollback_promotion(
            project_root=self.project,
            transaction_path=self.transaction,
            reason="resume interrupted rollback",
        )
        self.assertEqual(portable_release.directory_inventory(self.formal), old_inventory)
        self.assertFalse(self.transaction.exists())

    def test_backup_may_not_overlap_the_project(self) -> None:
        self._stage()
        old_inventory = self._write_old_formal()
        unsafe_backup = self.project / "build" / "release-before-unsafe"

        with self.assertRaises(portable_release.ReleaseError):
            portable_release.begin_promotion(
                project_root=self.project,
                candidate_dir=self.candidate,
                portable_dir=self.formal,
                backup_dir=unsafe_backup,
                transaction_path=self.transaction,
                expected_git_commit=self.commit,
            )

        self.assertEqual(portable_release.directory_inventory(self.formal), old_inventory)
        self.assertFalse(unsafe_backup.exists())
        self.assertFalse(self.transaction.exists())

    def test_tampered_transaction_path_is_rejected_without_mutating_release(self) -> None:
        self._stage()
        self._write_old_formal()
        transaction = self._begin()
        candidate_inventory = portable_release.directory_inventory(self.formal)
        journal = json.loads(self.transaction.read_text(encoding="utf-8"))
        journal["portable_dir"] = str(self.project / "release" / "Other-Product")
        self.transaction.write_text(json.dumps(journal), encoding="utf-8")

        with self.assertRaises(portable_release.ReleaseError):
            portable_release.rollback_promotion(
                project_root=self.project,
                transaction_path=self.transaction,
                reason="tampered path must be rejected",
            )

        self.assertEqual(
            portable_release.directory_inventory(self.formal), candidate_inventory
        )
        self.assertTrue(Path(transaction["previous_dir"]).exists())
        self.assertTrue(self.transaction.exists())

    def test_tampered_formal_release_is_preserved_during_rollback(self) -> None:
        self._stage()
        self._write_old_formal()
        transaction = self._begin()
        tampered = self.formal / "unexpected.txt"
        tampered.write_text("preserve evidence", encoding="utf-8")

        with self.assertRaises(portable_release.ReleaseError):
            portable_release.rollback_promotion(
                project_root=self.project,
                transaction_path=self.transaction,
                reason="tampered formal release",
            )

        self.assertTrue(tampered.is_file())
        self.assertTrue(Path(transaction["previous_dir"]).exists())
        self.assertTrue(self.transaction.exists())

    def test_exclusive_transaction_creation_refuses_a_second_owner(self) -> None:
        payload = {"owner": "first"}
        portable_release._create_json_exclusive(self.transaction, payload)

        with self.assertRaises(portable_release.ReleaseError):
            portable_release._create_json_exclusive(self.transaction, {"owner": "second"})

        self.assertEqual(
            json.loads(self.transaction.read_text(encoding="utf-8")), payload
        )

    def test_tree_hash_is_creation_order_independent_and_content_sensitive(self) -> None:
        first = self.root / "tree-first"
        second = self.root / "tree-second"
        first.mkdir()
        second.mkdir()
        (first / "b.txt").write_text("B", encoding="utf-8")
        (first / "a.txt").write_text("A", encoding="utf-8")
        (second / "a.txt").write_text("A", encoding="utf-8")
        (second / "b.txt").write_text("B", encoding="utf-8")

        first_inventory = portable_release.directory_inventory(first)
        second_inventory = portable_release.directory_inventory(second)
        self.assertEqual(first_inventory, second_inventory)

        (second / "b.txt").write_text("changed", encoding="utf-8")
        self.assertNotEqual(
            first_inventory["tree_sha256"],
            portable_release.directory_inventory(second)["tree_sha256"],
        )

    def test_identical_old_and_candidate_hashes_do_not_break_swap_rollback(self) -> None:
        candidate = self._stage()
        shutil.copytree(self.candidate, self.formal)
        real_replace = os.replace

        def fail_candidate_swap(source, destination):
            if (
                Path(source).name.startswith(".ProductAtelier-Portable.replacement-")
                and Path(destination).resolve(strict=False)
                == self.formal.resolve(strict=False)
            ):
                raise OSError("synthetic identical release swap failure")
            return real_replace(source, destination)

        with mock.patch.object(
            portable_release.os, "replace", side_effect=fail_candidate_swap
        ):
            with self.assertRaises(OSError):
                self._begin()

        self.assertEqual(
            portable_release.directory_inventory(self.formal), candidate["inventory"]
        )
        self.assertFalse(self.transaction.exists())
        self.assertEqual(
            list((self.project / "release").glob(".ProductAtelier-Portable.previous-*")),
            [],
        )

    def test_identical_old_and_candidate_hashes_support_normal_smoke_rollback(self) -> None:
        candidate = self._stage()
        shutil.copytree(self.candidate, self.formal)
        self._begin()

        portable_release.rollback_promotion(
            project_root=self.project,
            transaction_path=self.transaction,
            reason="synthetic smoke failure for identical bytes",
        )

        self.assertEqual(
            portable_release.directory_inventory(self.formal), candidate["inventory"]
        )
        self.assertFalse(self.transaction.exists())

    def test_finalize_can_resume_after_partial_previous_cleanup(self) -> None:
        self._stage()
        self._write_old_formal()
        transaction = self._begin()
        previous_path = Path(transaction["previous_dir"])
        real_rmtree = shutil.rmtree
        failed = False

        def partially_remove_then_fail(path, *args, **kwargs):
            nonlocal failed
            path = Path(path)
            if not failed and path.resolve(strict=False) == previous_path.resolve(strict=False):
                failed = True
                (path / "keep-me.txt").unlink()
                raise OSError("synthetic partial cleanup interruption")
            return real_rmtree(path, *args, **kwargs)

        with mock.patch.object(
            portable_release.shutil, "rmtree", side_effect=partially_remove_then_fail
        ):
            with self.assertRaises(OSError):
                portable_release.finalize_promotion(
                    project_root=self.project,
                    transaction_path=self.transaction,
                    expected_git_commit=self.commit,
                )

        self.assertTrue(failed)
        self.assertTrue(previous_path.exists())
        self.assertFalse((previous_path / "keep-me.txt").exists())
        self.assertEqual(
            json.loads(self.transaction.read_text(encoding="utf-8"))["phase"],
            "finalized",
        )
        evidence = portable_release.finalize_promotion(
            project_root=self.project,
            transaction_path=self.transaction,
            expected_git_commit=self.commit,
        )
        self.assertEqual(evidence["status"], "finalized")
        self.assertFalse(previous_path.exists())
        self.assertFalse(self.transaction.exists())

    def test_finalize_replays_verified_receipt_after_journal_is_removed(self) -> None:
        self._stage()
        self._write_old_formal()
        transaction = self._begin()
        first = portable_release.finalize_promotion(
            project_root=self.project,
            transaction_path=self.transaction,
            expected_git_commit=self.commit,
            expected_transaction_id=transaction["transaction_id"],
        )
        self.assertFalse(self.transaction.exists())

        replay = portable_release.finalize_promotion(
            project_root=self.project,
            transaction_path=self.transaction,
            expected_git_commit=self.commit,
            expected_transaction_id=transaction["transaction_id"],
        )

        self.assertEqual(replay["transaction_id"], first["transaction_id"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["status"], "finalized")

    def test_all_mutating_commands_share_the_same_process_lock(self) -> None:
        project = portable_release._project_root(self.project)

        with portable_release._promotion_lock(project):
            with self.assertRaises(portable_release.ReleaseError):
                self._stage()

        self.assertFalse(self.candidate.exists())

    def test_tree_hash_includes_required_empty_directories(self) -> None:
        with_empty = self.root / "with-empty-directory"
        without_empty = self.root / "without-empty-directory"
        (with_empty / "required-empty").mkdir(parents=True)
        without_empty.mkdir()

        with_inventory = portable_release.directory_inventory(with_empty)
        without_inventory = portable_release.directory_inventory(without_empty)

        self.assertEqual(with_inventory["file_count"], 0)
        self.assertEqual(with_inventory["directory_count"], 1)
        self.assertEqual(without_inventory["directory_count"], 0)
        self.assertNotEqual(
            with_inventory["tree_sha256"], without_inventory["tree_sha256"]
        )

    def test_symlinked_promotion_lock_is_rejected_without_touching_target(self) -> None:
        lock_path = self.project / "build" / portable_release.LOCK_FILE_NAME
        external = self.root / "outside-lock-target"
        external.write_bytes(b"")
        try:
            lock_path.symlink_to(external)
        except (OSError, NotImplementedError):
            self.skipTest("This platform does not permit creating a test symlink")

        with self.assertRaises(portable_release.ReleaseError):
            self._stage()

        self.assertEqual(external.read_bytes(), b"")
        self.assertFalse(self.candidate.exists())

    def test_cli_stage_begin_finalize_and_receipt_replay(self) -> None:
        tool = ROOT / "tools" / "portable_release.py"

        stage = subprocess.run(
            [
                sys.executable,
                str(tool),
                "stage",
                "--project-root",
                str(self.project),
                "--app-exe",
                str(self.app_source),
                "--sidecar-dir",
                str(self.sidecar_source),
                "--candidate-dir",
                str(self.candidate),
                "--git-commit",
                self.commit,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(stage.returncode, 0, stage.stderr)
        json.loads(stage.stdout)
        self._write_old_formal()

        begin = subprocess.run(
            [
                sys.executable,
                str(tool),
                "begin",
                "--project-root",
                str(self.project),
                "--candidate-dir",
                str(self.candidate),
                "--portable-dir",
                str(self.formal),
                "--backup-dir",
                str(self.backup),
                "--transaction",
                str(self.transaction),
                "--git-commit",
                self.commit,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(begin.returncode, 0, begin.stderr)
        transaction_id = json.loads(begin.stdout)["transaction_id"]

        finalize_command = [
            sys.executable,
            str(tool),
            "finalize",
            "--project-root",
            str(self.project),
            "--transaction",
            str(self.transaction),
            "--git-commit",
            self.commit,
            "--transaction-id",
            transaction_id,
        ]
        finalized = subprocess.run(
            finalize_command, check=False, capture_output=True, text=True
        )
        self.assertEqual(finalized.returncode, 0, finalized.stderr)
        self.assertEqual(json.loads(finalized.stdout)["status"], "finalized")

        replayed = subprocess.run(
            finalize_command, check=False, capture_output=True, text=True
        )
        self.assertEqual(replayed.returncode, 0, replayed.stderr)
        self.assertTrue(json.loads(replayed.stdout)["replayed"])

    def test_finalize_receipt_rejects_another_transaction_id(self) -> None:
        self._stage()
        self._write_old_formal()
        transaction = self._begin()
        portable_release.finalize_promotion(
            project_root=self.project,
            transaction_path=self.transaction,
            expected_git_commit=self.commit,
            expected_transaction_id=transaction["transaction_id"],
        )

        with self.assertRaises(portable_release.ReleaseError):
            portable_release.finalize_promotion(
                project_root=self.project,
                transaction_path=self.transaction,
                expected_git_commit=self.commit,
                expected_transaction_id="b" * 32,
            )

    def test_finalize_receipt_rejects_tampered_durable_evidence(self) -> None:
        self._stage()
        self._write_old_formal()
        transaction = self._begin()
        finalized = portable_release.finalize_promotion(
            project_root=self.project,
            transaction_path=self.transaction,
            expected_git_commit=self.commit,
            expected_transaction_id=transaction["transaction_id"],
        )
        evidence_path = Path(finalized["evidence_path"])
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["outcome"]["formal_inventory"]["total_bytes"] += 1
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

        with self.assertRaises(portable_release.ReleaseError):
            portable_release.finalize_promotion(
                project_root=self.project,
                transaction_path=self.transaction,
                expected_git_commit=self.commit,
                expected_transaction_id=transaction["transaction_id"],
            )

    def test_hardlinked_promotion_lock_is_rejected_without_touching_target(self) -> None:
        lock_path = self.project / "build" / portable_release.LOCK_FILE_NAME
        external = self.root / "outside-hardlink-target"
        external.write_bytes(b"")
        try:
            os.link(external, lock_path)
        except (OSError, NotImplementedError):
            self.skipTest("This platform does not permit creating a test hard link")

        with self.assertRaises(portable_release.ReleaseError):
            self._stage()

        self.assertEqual(external.read_bytes(), b"")
        self.assertFalse(self.candidate.exists())

    def test_rollback_rejects_a_stale_transaction_identity(self) -> None:
        self._stage()
        self._write_old_formal()
        transaction = self._begin()
        live_inventory = portable_release.directory_inventory(self.formal)

        with self.assertRaises(portable_release.ReleaseError):
            portable_release.rollback_promotion(
                project_root=self.project,
                transaction_path=self.transaction,
                reason="stale caller must not roll back another promotion",
                expected_git_commit=self.commit,
                expected_transaction_id="b" * 32,
            )

        self.assertEqual(
            portable_release.directory_inventory(self.formal), live_inventory
        )
        self.assertTrue(Path(transaction["previous_dir"]).exists())
        self.assertTrue(self.transaction.exists())

    def test_cli_rollback_requires_and_uses_exact_transaction_identity(self) -> None:
        self._stage()
        self._write_old_formal()
        old_inventory = portable_release.directory_inventory(self.formal)
        transaction = self._begin()
        tool = ROOT / "tools" / "portable_release.py"

        rolled_back = subprocess.run(
            [
                sys.executable,
                str(tool),
                "rollback",
                "--project-root",
                str(self.project),
                "--transaction",
                str(self.transaction),
                "--reason",
                "CLI smoke failure",
                "--git-commit",
                self.commit,
                "--transaction-id",
                transaction["transaction_id"],
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
        self.assertEqual(json.loads(rolled_back.stdout)["status"], "rolled_back")
        self.assertEqual(portable_release.directory_inventory(self.formal), old_inventory)
        self.assertFalse(self.transaction.exists())

    def test_partial_backup_recovery_copy_can_be_resumed(self) -> None:
        self._stage()
        old_inventory = self._write_old_formal()
        transaction = self._begin()
        shutil.rmtree(Path(transaction["previous_dir"]))
        real_copytree = shutil.copytree
        interrupted = False

        def partial_copy(source, destination, *args, **kwargs):
            nonlocal interrupted
            destination = Path(destination)
            if not interrupted and ".ProductAtelier-Portable.recovery-" in destination.name:
                interrupted = True
                destination.mkdir()
                shutil.copy2(Path(source) / "Product Atelier.exe", destination / "Product Atelier.exe")
                raise OSError("synthetic partial backup recovery copy")
            return real_copytree(source, destination, *args, **kwargs)

        with mock.patch.object(
            portable_release.shutil, "copytree", side_effect=partial_copy
        ):
            with self.assertRaises(OSError):
                portable_release.rollback_promotion(
                    project_root=self.project,
                    transaction_path=self.transaction,
                    reason="interrupted backup recovery",
                    expected_git_commit=self.commit,
                    expected_transaction_id=transaction["transaction_id"],
                )

        self.assertTrue(interrupted)
        self.assertFalse(self.formal.exists())
        portable_release.rollback_promotion(
            project_root=self.project,
            transaction_path=self.transaction,
            reason="resume backup recovery",
            expected_git_commit=self.commit,
            expected_transaction_id=transaction["transaction_id"],
        )
        self.assertEqual(portable_release.directory_inventory(self.formal), old_inventory)
        self.assertFalse(self.transaction.exists())

    def test_unknown_partial_backup_recovery_data_is_preserved(self) -> None:
        self._stage()
        self._write_old_formal()
        transaction = self._begin()
        shutil.rmtree(Path(transaction["previous_dir"]))
        recovery = self.project / "release" / (
            ".ProductAtelier-Portable.recovery-" + transaction["transaction_id"]
        )
        recovery.mkdir()
        unknown = recovery / "unexpected-user-data.txt"
        unknown.write_text("preserve", encoding="utf-8")

        with self.assertRaises(portable_release.ReleaseError):
            portable_release.rollback_promotion(
                project_root=self.project,
                transaction_path=self.transaction,
                reason="unknown recovery content",
                expected_git_commit=self.commit,
                expected_transaction_id=transaction["transaction_id"],
            )

        self.assertTrue(unknown.is_file())
        self.assertTrue(self.formal.exists())
        self.assertTrue(self.transaction.exists())


if __name__ == "__main__":
    unittest.main()
