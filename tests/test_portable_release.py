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
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import portable_release  # noqa: E402


class PortableReleaseTests(unittest.TestCase):
    def test_formal_entrypoint_runs_schema_migration_gate_before_promotion(self) -> None:
        script = (ROOT / "tools" / "dev.ps1").read_text(encoding="utf-8-sig")
        candidate_smoke = script.index('Write-Host "[9/11] Smoking the isolated candidate..."')
        migration_gate = script.index('"verify_packaged_schema_upgrade.py"')
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
        self.candidate_identity_sha256: str | None = None

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
        result = portable_release.stage_candidate(
            project_root=self.project,
            app_exe=self.app_source,
            sidecar_dir=self.sidecar_source,
            candidate_dir=self.candidate,
            expected_git_commit=self.commit,
        )
        self.candidate_identity_sha256 = result["identity_receipt"]["sha256"]
        return result

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

    def _begin(self, candidate_identity_sha256: str | None = None) -> dict:
        return portable_release.begin_promotion(
            project_root=self.project,
            candidate_dir=self.candidate,
            portable_dir=self.formal,
            backup_dir=self.backup,
            transaction_path=self.transaction,
            expected_git_commit=self.commit,
            expected_candidate_identity_sha256=(
                candidate_identity_sha256
                or self.candidate_identity_sha256
                or ("0" * 64)
            ),
        )

    def _candidate_identity_path(self) -> Path:
        return (
            self.project
            / "build"
            / portable_release.CANDIDATE_IDENTITY_FILE_NAME
        )

    def _assert_begin_rejected_before_formal_inventory(self) -> None:
        old_inventory = self._write_old_formal()
        inventoried_roots: list[Path] = []
        real_inventory = portable_release.directory_inventory

        def inventory_with_trace(root: str | Path) -> dict:
            inventoried_roots.append(Path(root).resolve(strict=False))
            return real_inventory(root)

        with mock.patch.object(
            portable_release,
            "directory_inventory",
            side_effect=inventory_with_trace,
        ), self.assertRaises(portable_release.ReleaseError):
            self._begin()

        self.assertNotIn(self.formal.resolve(strict=False), inventoried_roots)
        self.assertEqual(
            portable_release.directory_inventory(self.formal), old_inventory
        )
        self.assertFalse(self.backup.exists())
        self.assertFalse(self.transaction.exists())

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

    def test_candidate_identity_rejects_app_tampering_before_formal_inventory(self) -> None:
        self._stage()
        (self.candidate / portable_release.APP_NAME).write_bytes(
            b"tampered-tauri-shell"
        )

        self._assert_begin_rejected_before_formal_inventory()

    def test_candidate_identity_rejects_sidecar_tampering_before_formal_inventory(self) -> None:
        self._stage()
        sidecar_path = self.candidate / portable_release.SIDECAR_EXE
        sidecar_path.write_bytes(b"tampered-python-sidecar")
        manifest_path = self.candidate / portable_release.SIDECAR_MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["executable_sha256"] = hashlib.sha256(
            sidecar_path.read_bytes()
        ).hexdigest().upper()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        self._assert_begin_rejected_before_formal_inventory()

    def test_candidate_identity_rejects_manifest_tampering_before_formal_inventory(self) -> None:
        self._stage()
        manifest_path = self.candidate / portable_release.SIDECAR_MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["built_at"] = "2026-09-03T12:00:00Z"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        self._assert_begin_rejected_before_formal_inventory()

    def test_candidate_identity_rejects_static_file_tampering_before_formal_inventory(self) -> None:
        static_source = self.sidecar_source / "static" / "runtime.txt"
        static_source.parent.mkdir()
        static_source.write_text("staged-static-content", encoding="utf-8")
        self._stage()
        (self.candidate / "python-server" / "static" / "runtime.txt").write_text(
            "tampered-static-content",
            encoding="utf-8",
        )

        self._assert_begin_rejected_before_formal_inventory()

    def test_new_stage_replaces_the_previous_candidate_identity_receipt(self) -> None:
        self._stage()
        receipt_path = self._candidate_identity_path()
        first_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(
            first_receipt["candidate"],
            portable_release.validate_candidate(self.candidate, self.commit),
        )

        self.app_source.write_bytes(b"replacement-tauri-shell")
        self._stage()
        second_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

        self.assertEqual(
            second_receipt["format_version"],
            portable_release.CANDIDATE_IDENTITY_FORMAT_VERSION,
        )
        self.assertEqual(
            second_receipt["kind"],
            "product-atelier-portable-candidate-identity",
        )
        self.assertIsInstance(second_receipt["created_at_utc"], str)
        self.assertTrue(second_receipt["created_at_utc"])
        self.assertEqual(second_receipt["project_root"], str(self.project.resolve()))
        self.assertEqual(second_receipt["candidate_dir"], str(self.candidate.resolve()))
        self.assertEqual(second_receipt["git_commit"], self.commit)
        self.assertEqual(
            second_receipt["candidate"],
            portable_release.validate_candidate(self.candidate, self.commit),
        )
        self.assertNotEqual(
            first_receipt["candidate"]["artifacts"]["app_sha256"],
            second_receipt["candidate"]["artifacts"]["app_sha256"],
        )

    def test_begin_rejects_a_same_commit_restage_after_candidate_review(self) -> None:
        reviewed = self._stage()
        reviewed_identity_sha256 = reviewed["identity_receipt"]["sha256"]
        reviewed_app_sha256 = reviewed["artifacts"]["app_sha256"]

        self.app_source.write_bytes(b"same-commit-candidate-not-reviewed")
        replacement = self._stage()
        self.assertNotEqual(
            reviewed_app_sha256,
            replacement["artifacts"]["app_sha256"],
        )
        self.assertNotEqual(
            reviewed_identity_sha256,
            replacement["identity_receipt"]["sha256"],
        )
        old_inventory = self._write_old_formal()

        with mock.patch.object(
            portable_release,
            "_canonical_portable_path",
            side_effect=AssertionError("formal path was resolved before identity rejection"),
        ) as canonical_portable:
            with self.assertRaisesRegex(
                portable_release.ReleaseError,
                "changed after candidate review",
            ):
                self._begin(reviewed_identity_sha256)

        canonical_portable.assert_not_called()
        self.assertEqual(
            portable_release.directory_inventory(self.formal),
            old_inventory,
        )
        self.assertFalse(self.backup.exists())
        self.assertFalse(self.transaction.exists())

    def test_missing_candidate_identity_receipt_fails_closed(self) -> None:
        self._stage()
        self._candidate_identity_path().unlink()

        self._assert_begin_rejected_before_formal_inventory()

    def test_verify_candidate_identity_requires_the_published_receipt(self) -> None:
        self._stage()
        self._candidate_identity_path().unlink()

        with self.assertRaises(portable_release.ReleaseError):
            portable_release.verify_candidate_identity(
                project_root=self.project,
                candidate_dir=self.candidate,
                expected_git_commit=self.commit,
            )

    def test_verify_candidate_identity_rejects_exact_receipt_drift(self) -> None:
        reviewed = self._stage()
        reviewed_identity_sha256 = reviewed["identity_receipt"]["sha256"]
        self.app_source.write_bytes(b"same-commit-replacement")
        replacement = self._stage()
        self.assertNotEqual(
            reviewed_identity_sha256,
            replacement["identity_receipt"]["sha256"],
        )

        with self.assertRaisesRegex(
            portable_release.ReleaseError,
            "changed after candidate review",
        ):
            portable_release.verify_candidate_identity(
                project_root=self.project,
                candidate_dir=self.candidate,
                expected_git_commit=self.commit,
                expected_candidate_identity_sha256=reviewed_identity_sha256,
            )

    def test_corrupt_candidate_identity_receipt_fails_closed(self) -> None:
        self._stage()
        self._candidate_identity_path().write_text(
            '{"format_version":',
            encoding="utf-8",
        )

        self._assert_begin_rejected_before_formal_inventory()

    def test_old_candidate_identity_receipt_format_fails_closed(self) -> None:
        self._stage()
        receipt_path = self._candidate_identity_path()
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["format_version"] = (
            portable_release.CANDIDATE_IDENTITY_FORMAT_VERSION - 1
        )
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

        self._assert_begin_rejected_before_formal_inventory()

    def test_candidate_identity_receipt_fstat_drift_fails_closed(self) -> None:
        self._stage()
        receipt_metadata = self._candidate_identity_path().lstat()
        receipt_identity = (receipt_metadata.st_dev, receipt_metadata.st_ino)
        real_fstat = os.fstat
        receipt_fstat_calls = 0

        def drifting_receipt_fstat(descriptor: int):
            nonlocal receipt_fstat_calls
            metadata = real_fstat(descriptor)
            if (metadata.st_dev, metadata.st_ino) != receipt_identity:
                return metadata
            receipt_fstat_calls += 1
            if receipt_fstat_calls != 2:
                return metadata
            return SimpleNamespace(
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino,
                st_mode=metadata.st_mode,
                st_nlink=metadata.st_nlink,
                st_size=metadata.st_size,
                st_mtime=metadata.st_mtime,
                st_mtime_ns=metadata.st_mtime_ns + 1,
                st_ctime=metadata.st_ctime,
                st_ctime_ns=metadata.st_ctime_ns,
                st_file_attributes=getattr(metadata, "st_file_attributes", 0),
                st_reparse_tag=getattr(metadata, "st_reparse_tag", 0),
            )

        with mock.patch.object(
            portable_release.os,
            "fstat",
            side_effect=drifting_receipt_fstat,
        ):
            self._assert_begin_rejected_before_formal_inventory()

        self.assertEqual(receipt_fstat_calls, 2)

    @unittest.skipUnless(os.name == "nt", "Windows hard-link policy only")
    def test_hardlinked_candidate_identity_receipt_fails_closed(self) -> None:
        self._stage()
        receipt_path = self._candidate_identity_path()
        receipt_bytes = receipt_path.read_bytes()
        external = self.root / "external-candidate-identity.json"
        external.write_bytes(receipt_bytes)
        receipt_path.unlink()
        try:
            os.link(external, receipt_path)
        except (OSError, NotImplementedError):
            self.skipTest("This filesystem does not permit creating a hard link")

        self._assert_begin_rejected_before_formal_inventory()
        self.assertEqual(external.read_bytes(), receipt_bytes)

    def test_symlinked_candidate_identity_receipt_fails_closed(self) -> None:
        self._stage()
        receipt_path = self._candidate_identity_path()
        receipt_bytes = receipt_path.read_bytes()
        external = self.root / "external-candidate-identity.json"
        external.write_bytes(receipt_bytes)
        receipt_path.unlink()
        try:
            receipt_path.symlink_to(external)
        except (OSError, NotImplementedError):
            self.skipTest("This platform does not permit creating a file symlink")

        self._assert_begin_rejected_before_formal_inventory()
        self.assertEqual(external.read_bytes(), receipt_bytes)

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

    def test_post_swap_validation_failure_restores_the_previous_candidate(self) -> None:
        previous = self._stage()
        previous_inventory = portable_release.directory_inventory(self.candidate)
        self.app_source.write_bytes(b"new candidate app")
        real_validate = portable_release.validate_candidate
        validation_calls = 0

        def fail_installed_validation(path, expected_git_commit):
            nonlocal validation_calls
            validation_calls += 1
            if validation_calls == 2:
                raise portable_release.ReleaseError("synthetic installed validation failure")
            return real_validate(path, expected_git_commit)

        with mock.patch.object(
            portable_release,
            "validate_candidate",
            side_effect=fail_installed_validation,
        ):
            with self.assertRaisesRegex(
                portable_release.ReleaseError,
                "synthetic installed validation failure",
            ):
                self._stage()

        self.assertEqual(validation_calls, 2)
        self.assertEqual(
            portable_release.directory_inventory(self.candidate),
            previous_inventory,
        )
        self.assertEqual(previous["inventory"], previous_inventory)
        failed = list(
            (self.project / "build").glob(".portable-candidate-current.failed-stage-*")
        )
        self.assertEqual(len(failed), 1)
        self.assertEqual(
            (failed[0] / "Product Atelier.exe").read_bytes(),
            b"new candidate app",
        )

    def test_initial_post_swap_failure_leaves_no_canonical_candidate(self) -> None:
        real_validate = portable_release.validate_candidate
        validation_calls = 0

        def fail_installed_validation(path, expected_git_commit):
            nonlocal validation_calls
            validation_calls += 1
            if validation_calls == 2:
                raise portable_release.ReleaseError("synthetic initial validation failure")
            return real_validate(path, expected_git_commit)

        with mock.patch.object(
            portable_release,
            "validate_candidate",
            side_effect=fail_installed_validation,
        ):
            with self.assertRaisesRegex(
                portable_release.ReleaseError,
                "synthetic initial validation failure",
            ):
                self._stage()

        self.assertEqual(validation_calls, 2)
        self.assertFalse(self.candidate.exists())
        failed = list(
            (self.project / "build").glob(".portable-candidate-current.failed-stage-*")
        )
        self.assertEqual(len(failed), 1)
        self.assertTrue((failed[0] / "Product Atelier.exe").is_file())

    def test_partial_previous_cleanup_keeps_the_committed_candidate_and_receipt(self) -> None:
        self._stage()
        previous_inventory = portable_release.directory_inventory(self.candidate)
        self.app_source.write_bytes(b"replacement candidate app")
        real_remove = portable_release._remove_generated_tree
        cleanup_failed = False

        def fail_previous_cleanup(path, allowed_root, prefix):
            nonlocal cleanup_failed
            if not cleanup_failed and prefix.startswith(".portable-candidate-current.previous-"):
                cleanup_failed = True
                (Path(path) / "Start.bat").unlink()
                raise OSError("synthetic partial previous cleanup failure")
            return real_remove(path, allowed_root, prefix)

        with mock.patch.object(
            portable_release,
            "_remove_generated_tree",
            side_effect=fail_previous_cleanup,
        ):
            staged = self._stage()

        self.assertTrue(cleanup_failed)
        self.assertNotEqual(
            portable_release.directory_inventory(self.candidate), previous_inventory
        )
        self.assertEqual(
            portable_release.validate_candidate(self.candidate, self.commit),
            {
                "inventory": staged["inventory"],
                "artifacts": staged["artifacts"],
            },
        )
        self.assertEqual(
            (self.candidate / portable_release.APP_NAME).read_bytes(),
            b"replacement candidate app",
        )
        self.assertEqual(len(staged.get("cleanup_warnings", [])), 1)
        self.assertIn("synthetic partial previous cleanup failure", staged["cleanup_warnings"][0])
        self.assertEqual(
            json.loads(self._candidate_identity_path().read_text(encoding="utf-8"))[
                "candidate"
            ],
            {
                "inventory": staged["inventory"],
                "artifacts": staged["artifacts"],
            },
        )
        previous_orphans = list(
            (self.project / "build").glob(".portable-candidate-current.previous-*")
        )
        self.assertEqual(len(previous_orphans), 1)
        self.assertFalse((previous_orphans[0] / "Start.bat").exists())
        self.assertFalse(
            list((self.project / "build").glob(".portable-candidate-current.failed-stage-*"))
        )

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
                expected_candidate_identity_sha256=(
                    self.candidate_identity_sha256 or ("0" * 64)
                ),
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

    def test_directory_inventory_rejects_a_raw_root_symlink_before_resolving_it(self) -> None:
        real_release = self.root / "real-release-root"
        real_release.mkdir()
        (real_release / "artifact.bin").write_bytes(b"artifact")
        linked_release = self.root / "linked-release-root"
        try:
            linked_release.symlink_to(real_release, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("This platform does not permit creating a directory symlink")

        with self.assertRaisesRegex(
            portable_release.ReleaseError,
            "regular non-reparse directory",
        ):
            portable_release.directory_inventory(linked_release)

    @unittest.skipUnless(os.name == "nt", "Windows junction policy only")
    def test_validate_candidate_rejects_a_raw_root_junction(self) -> None:
        self._stage()
        linked_candidate = self.root / "candidate-junction"
        creation = subprocess.run(
            [
                "cmd.exe",
                "/d",
                "/c",
                "mklink",
                "/J",
                str(linked_candidate),
                str(self.candidate),
            ],
            check=False,
            capture_output=True,
        )
        if creation.returncode != 0:
            self.skipTest("This filesystem does not permit creating a junction")

        try:
            with self.assertRaisesRegex(
                portable_release.ReleaseError,
                "regular non-reparse directory",
            ):
                portable_release.validate_candidate(linked_candidate, self.commit)
        finally:
            os.rmdir(linked_candidate)

    def test_directory_inventory_checks_raw_root_reparse_state_before_resolve(self) -> None:
        release = self.root / "raw-root-reparse-check"
        release.mkdir()
        with (
            mock.patch.object(portable_release, "_is_link_like", return_value=True),
            mock.patch.object(Path, "resolve") as resolve,
        ):
            with self.assertRaisesRegex(
                portable_release.ReleaseError,
                "regular non-reparse directory",
            ):
                portable_release.directory_inventory(release)
        resolve.assert_not_called()

    def test_directory_inventory_rejects_any_reparse_tagged_file(self) -> None:
        release = self.root / "reparse-release"
        release.mkdir()
        artifact = release / "artifact.bin"
        artifact.write_bytes(b"artifact")
        real_lstat = Path.lstat

        def reparse_lstat(path: Path):
            metadata = real_lstat(path)
            if path == artifact:
                return SimpleNamespace(
                    st_mode=metadata.st_mode,
                    st_nlink=metadata.st_nlink,
                    st_file_attributes=(
                        int(getattr(metadata, "st_file_attributes", 0) or 0)
                        | 0x400
                    ),
                )
            return metadata

        with mock.patch.object(Path, "lstat", autospec=True, side_effect=reparse_lstat):
            with self.assertRaisesRegex(
                portable_release.ReleaseError,
                "reparse point",
            ):
                portable_release.directory_inventory(release)

    @unittest.skipUnless(os.name == "nt", "Windows hard-link policy only")
    def test_directory_inventory_rejects_a_hardlinked_file(self) -> None:
        release = self.root / "hardlink-release"
        release.mkdir()
        external = self.root / "external-hardlink-target.bin"
        external.write_bytes(b"artifact")
        artifact = release / "artifact.bin"
        try:
            os.link(external, artifact)
        except (OSError, NotImplementedError):
            self.skipTest("This filesystem does not permit creating a hard link")

        with self.assertRaisesRegex(
            portable_release.ReleaseError,
            "hard link",
        ):
            portable_release.directory_inventory(release)

    def test_stable_hash_rejects_handle_metadata_drift(self) -> None:
        artifact = self.root / "handle-drift.bin"
        artifact.write_bytes(b"stable bytes")
        real_fstat = os.fstat
        fstat_calls = 0

        def drifting_fstat(descriptor: int):
            nonlocal fstat_calls
            metadata = real_fstat(descriptor)
            fstat_calls += 1
            if fstat_calls != 2:
                return metadata
            return SimpleNamespace(
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino,
                st_mode=metadata.st_mode,
                st_nlink=metadata.st_nlink,
                st_size=metadata.st_size + 1,
                st_mtime=metadata.st_mtime,
                st_mtime_ns=metadata.st_mtime_ns,
                st_ctime=metadata.st_ctime,
                st_ctime_ns=metadata.st_ctime_ns,
                st_file_attributes=getattr(metadata, "st_file_attributes", 0),
                st_reparse_tag=getattr(metadata, "st_reparse_tag", 0),
            )

        with mock.patch.object(
            portable_release.os,
            "fstat",
            side_effect=drifting_fstat,
        ):
            with self.assertRaisesRegex(
                portable_release.ReleaseError,
                "changed while it was being hashed",
            ):
                portable_release._stable_file_record(artifact, "Release artifact")

    def test_stable_hash_rejects_path_metadata_drift(self) -> None:
        artifact = self.root / "path-drift.bin"
        artifact.write_bytes(b"stable bytes")
        real_lstat = Path.lstat
        artifact_lstat_calls = 0

        def drifting_lstat(path: Path):
            nonlocal artifact_lstat_calls
            metadata = real_lstat(path)
            if path != artifact:
                return metadata
            artifact_lstat_calls += 1
            if artifact_lstat_calls != 3:
                return metadata
            return SimpleNamespace(
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino,
                st_mode=metadata.st_mode,
                st_nlink=metadata.st_nlink,
                st_size=metadata.st_size,
                st_mtime=metadata.st_mtime,
                st_mtime_ns=metadata.st_mtime_ns + 1,
                st_ctime=metadata.st_ctime,
                st_ctime_ns=metadata.st_ctime_ns,
                st_file_attributes=getattr(metadata, "st_file_attributes", 0),
                st_reparse_tag=getattr(metadata, "st_reparse_tag", 0),
            )

        with mock.patch.object(Path, "lstat", autospec=True, side_effect=drifting_lstat):
            with self.assertRaisesRegex(
                portable_release.ReleaseError,
                "changed while it was being hashed",
            ):
                portable_release._stable_file_record(artifact, "Release artifact")

    def test_directory_inventory_registers_a_fail_closed_walk_error_handler(self) -> None:
        release = self.root / "walk-error-release"
        release.mkdir()
        blocked = release / "blocked"
        observed: dict[str, object] = {}

        def failing_walk(
            root: Path,
            *,
            topdown: bool,
            onerror: object,
            followlinks: bool,
        ) -> list[tuple[str, list[str], list[str]]]:
            observed.update(
                root=root,
                topdown=topdown,
                onerror=onerror,
                followlinks=followlinks,
            )
            error = PermissionError(13, "synthetic access denied", str(blocked))
            self.assertTrue(callable(onerror))
            onerror(error)  # type: ignore[operator]
            return []

        with mock.patch.object(portable_release.os, "walk", side_effect=failing_walk):
            with self.assertRaisesRegex(
                portable_release.ReleaseError,
                r"Could not enumerate release tree.*blocked",
            ) as raised:
                portable_release.directory_inventory(release)

        self.assertIsInstance(raised.exception.__cause__, PermissionError)
        self.assertEqual(observed["root"], release.resolve())
        self.assertTrue(observed["topdown"])
        self.assertFalse(observed["followlinks"])

    def test_directory_inventory_rejects_a_partially_unreadable_tree(self) -> None:
        release = self.root / "partially-unreadable-release"
        blocked = release / "blocked"
        blocked.mkdir(parents=True)
        (release / "visible.txt").write_text("must not be certified alone", encoding="utf-8")
        (blocked / "hidden.txt").write_text("must not be omitted", encoding="utf-8")
        real_scandir = os.scandir
        blocked_resolved = blocked.resolve()

        def deny_blocked(path: str | os.PathLike[str]):
            if Path(path).resolve(strict=False) == blocked_resolved:
                raise PermissionError(13, "synthetic access denied", str(blocked))
            return real_scandir(path)

        with mock.patch.object(portable_release.os, "scandir", side_effect=deny_blocked):
            with self.assertRaisesRegex(
                portable_release.ReleaseError,
                r"Could not enumerate release tree.*blocked",
            ) as raised:
                portable_release.directory_inventory(release)

        self.assertIsInstance(raised.exception.__cause__, PermissionError)

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
        stage_result = json.loads(stage.stdout)
        candidate_identity_sha256 = stage_result["identity_receipt"]["sha256"]
        verified = subprocess.run(
            [
                sys.executable,
                str(tool),
                "verify-identity",
                "--project-root",
                str(self.project),
                "--candidate-dir",
                str(self.candidate),
                "--git-commit",
                self.commit,
                "--candidate-identity-sha256",
                candidate_identity_sha256,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(
            json.loads(verified.stdout)["identity_receipt"]["sha256"],
            candidate_identity_sha256,
        )
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
                "--candidate-identity-sha256",
                candidate_identity_sha256,
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
