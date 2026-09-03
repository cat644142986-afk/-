from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import launch_and_shoot as launcher  # noqa: E402


class FakeSidecarProcess:
    def __init__(
        self,
        pid: int,
        parent_pid: int,
        executable: Path,
        create_time: float = 1000.0,
    ) -> None:
        self.pid = pid
        self._parent_pid = parent_pid
        self._executable = executable
        self._create_time = create_time
        self.terminated = False

    def _require_alive(self) -> None:
        if self.terminated:
            raise launcher.psutil.NoSuchProcess(self.pid)

    def create_time(self) -> float:
        self._require_alive()
        return self._create_time

    def ppid(self) -> int:
        self._require_alive()
        return self._parent_pid

    def exe(self) -> str:
        self._require_alive()
        return str(self._executable)

    def is_running(self) -> bool:
        return not self.terminated

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float) -> None:
        del timeout

    def kill(self) -> None:
        self.terminated = True


class FakeAppProcess:
    def __init__(self, pid: int = 42) -> None:
        self.pid = pid
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float) -> int:
        del timeout
        return 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class LaunchAndShootSafetyTests(unittest.TestCase):
    COMMIT = "a" * 40

    def _write_candidate(
        self,
        root: Path,
        commit: str = COMMIT,
    ) -> tuple[Path, Path, str, str]:
        previous_project_root = launcher.PROJECT_ROOT
        self.addCleanup(setattr, launcher, "PROJECT_ROOT", previous_project_root)
        launcher.PROJECT_ROOT = root
        (root / "package.json").write_text("{}", encoding="utf-8")
        tauri_config = root / "src-tauri" / "tauri.conf.json"
        tauri_config.parent.mkdir()
        tauri_config.write_text("{}", encoding="utf-8")
        executable = (
            root
            / "build"
            / "portable-candidate-current"
            / "Product Atelier.exe"
        )
        sidecar = executable.parent / "python-server" / "python-server.exe"
        sidecar.parent.mkdir(parents=True)
        executable.write_bytes(b"candidate")
        sidecar.write_bytes(b"sidecar")
        source_hashes = {"python/server.py": "B" * 64}
        fingerprint_rows = [f"{path}:{digest}" for path, digest in source_hashes.items()]
        manifest = {
            "contract_version": "test-contract",
            "ledger_schema_version": 8,
            "git_commit": commit,
            "source_fingerprint": hashlib.sha256(
                "\n".join(fingerprint_rows).encode("utf-8")
            ).hexdigest().upper(),
            "executable_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest().upper(),
            "source_hashes": source_hashes,
        }
        (sidecar.parent / "sidecar-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        app_hash = hashlib.sha256(executable.read_bytes()).hexdigest().upper()
        tree_hash = launcher.validate_candidate(executable.parent, commit)["inventory"][
            "tree_sha256"
        ]
        return executable, sidecar, app_hash, tree_hash

    @contextmanager
    def _mock_formal_gate(
        self,
        root: Path,
        executable: Path,
        app_hash: str,
        tree_hash: str,
        *,
        verifier_error: Exception | None = None,
        process_cleanup_errors: tuple[str, ...] = (),
        tree_close_errors: tuple[str, ...] = (),
        mutate_during_publication=None,
    ):
        isolated_path = root / f"{launcher.ISOLATED_DATA_PREFIX}formal"
        isolated_path.mkdir()
        location = launcher.IsolatedDataDirectory(isolated_path, root)
        final_output = root / "formal-evidence"
        app = FakeAppProcess()
        identity = launcher.ProcessIdentity(pid=app.pid, create_time=1000.0)
        events: list[str] = []
        state = {"promotion_lock_held": False}

        @contextmanager
        def fake_promotion_lock(project: Path):
            self.assertEqual(project, root)
            self.assertFalse(state["promotion_lock_held"])
            state["promotion_lock_held"] = True
            events.append("promotion-acquire")
            try:
                yield
            finally:
                self.assertTrue(state["promotion_lock_held"])
                events.append("promotion-release")
                state["promotion_lock_held"] = False

        file_locks = mock.Mock()
        file_locks.poll_change_errors.return_value = []

        def close_tree_locks() -> list[str]:
            self.assertTrue(state["promotion_lock_held"])
            events.append("tree-close")
            return list(tree_close_errors)

        file_locks.close.side_effect = close_tree_locks

        def verify(_args):
            events.append("verify")
            if verifier_error is not None:
                raise verifier_error
            return {"status": "staged", "passed": True}

        def cleanup_processes(*_args) -> list[str]:
            self.assertTrue(state["promotion_lock_held"])
            events.append("process-cleanup")
            return list(process_cleanup_errors)

        def cleanup_data(*_args) -> None:
            self.assertTrue(state["promotion_lock_held"])
            events.append("data-cleanup")

        def finalize(_result, session, expected_final: Path) -> dict[str, object]:
            self.assertEqual(expected_final, final_output)
            self.assertTrue(session.publication_ready)
            self.assertTrue(session.publication_protections_held)
            self.assertTrue(state["promotion_lock_held"])
            self.assertFalse(file_locks.close.called)
            self.assertIn("process-cleanup", events)
            self.assertIn("data-cleanup", events)
            events.append("publish")
            if mutate_during_publication is not None:
                mutate_during_publication()
            expected_final.mkdir()
            return {
                "status": "finalized",
                "passed": True,
                "final_output_dir": str(expected_final),
            }

        verifier_module = SimpleNamespace(
            DEFAULT_PROFILES=("default",),
            DEFAULT_SIZES=((1280, 720),),
            DEFAULT_SURFACES=("result-review",),
            run_formal_webview_verification=verify,
        )
        seed_module = SimpleNamespace(seed_feedback_checkpoint=lambda _path: None)

        with (
            mock.patch.dict(
                sys.modules,
                {
                    "verify_formal_webview": verifier_module,
                    "seed_feedback_checkpoint": seed_module,
                },
            ),
            mock.patch.object(
                launcher.portable_release,
                "_promotion_lock",
                side_effect=fake_promotion_lock,
            ),
            mock.patch.object(
                launcher,
                "acquire_candidate_tree_locks",
                return_value=file_locks,
            ),
            mock.patch.object(
                launcher,
                "create_isolated_data_directory",
                return_value=location,
            ),
            mock.patch.object(launcher.subprocess, "Popen", return_value=app) as popen,
            mock.patch.object(
                launcher,
                "capture_launched_app_identity",
                return_value=identity,
            ),
            mock.patch.object(
                launcher,
                "cleanup_launched_processes",
                side_effect=cleanup_processes,
            ),
            mock.patch.object(
                launcher,
                "cleanup_isolated_data_directory",
                side_effect=cleanup_data,
            ),
            mock.patch.object(
                launcher,
                "finalize_staged_verification",
                side_effect=finalize,
            ) as finalize_mock,
        ):
            yield SimpleNamespace(
                events=events,
                state=state,
                final_output=final_output,
                isolated_path=isolated_path,
                popen=popen,
                finalize=finalize_mock,
                invoke=lambda: launcher.launch_verify_and_finalize(
                    executable=executable,
                    expected_git_commit=self.COMMIT,
                    expected_app_sha256=app_hash,
                    expected_tree_sha256=tree_hash,
                    cdp_port=9333,
                    monitor_index=0,
                    expected_dpi=96,
                    output_dir=final_output,
                ),
            )

    def test_candidate_path_is_explicit_absolute_and_rejects_formal_release(self) -> None:
        with self.assertRaisesRegex(launcher.LaunchSafetyError, "absolute"):
            launcher.resolve_candidate_executable("candidate/Product Atelier.exe")

        with tempfile.TemporaryDirectory() as temporary_dir:
            formal_exe = (
                Path(temporary_dir)
                / "release"
                / "ProductAtelier-Portable"
                / "Product Atelier.exe"
            )
            formal_exe.parent.mkdir(parents=True)
            formal_exe.write_bytes(b"formal")
            with self.assertRaisesRegex(launcher.LaunchSafetyError, "formal"):
                launcher.resolve_candidate_executable(formal_exe)

    def test_candidate_identity_rejects_an_alternate_executable_in_the_same_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            executable, _, app_hash, _ = self._write_candidate(
                Path(temporary_dir).resolve()
            )
            alternate = executable.with_name("alternate.exe")
            alternate.write_bytes(executable.read_bytes())
            tree_hash = launcher.validate_candidate(
                executable.parent,
                self.COMMIT,
            )["inventory"]["tree_sha256"]

            with self.assertRaisesRegex(launcher.LaunchSafetyError, "canonical"):
                launcher.validate_candidate_identity(
                    alternate,
                    self.COMMIT,
                    app_hash,
                    tree_hash,
                )

    def test_child_environment_is_isolated_without_mutating_parent(self) -> None:
        variable_names = (
            "PRODUCT_ATELIER_CANDIDATE_ISOLATION",
            "PRODUCT_ATELIER_DATA_DIR",
            "PRODUCT_ATELIER_LEGACY_CONFIG",
            "PRODUCT_ATELIER_KNOWLEDGE_BASE",
            "PRODUCT_ATELIER_WEBVIEW_DATA_DIR",
            "WEBVIEW2_USER_DATA_FOLDER",
        )
        previous = {name: os.environ.get(name) for name in variable_names}
        with tempfile.TemporaryDirectory() as temporary_dir:
            data_dir = Path(temporary_dir).resolve()
            environment = launcher.build_child_environment(data_dir)
            child_paths = {
                name: Path(environment[name])
                for name in variable_names
                if name != "PRODUCT_ATELIER_CANDIDATE_ISOLATION"
            }
            self.assertEqual(environment["PRODUCT_ATELIER_CANDIDATE_ISOLATION"], "1")
            self.assertEqual(child_paths["PRODUCT_ATELIER_DATA_DIR"], data_dir)
            self.assertTrue(all(path.is_absolute() for path in child_paths.values()))
            self.assertTrue(all(path.is_relative_to(data_dir) for path in child_paths.values()))
            self.assertFalse(child_paths["PRODUCT_ATELIER_LEGACY_CONFIG"].exists())
            knowledge_base = child_paths["PRODUCT_ATELIER_KNOWLEDGE_BASE"]
            self.assertTrue(knowledge_base.is_dir())
            self.assertEqual(list(knowledge_base.iterdir()), [])
            webview_data = child_paths["WEBVIEW2_USER_DATA_FOLDER"]
            self.assertTrue(webview_data.is_dir())
            self.assertEqual(list(webview_data.iterdir()), [])
            self.assertEqual(
                {name: os.environ.get(name) for name in variable_names},
                previous,
            )

    def test_child_environment_strips_inherited_webview_arguments_case_insensitively(self) -> None:
        inherited = {
            "WebView2_Additional_Browser_Arguments": "--unsafe-inherited-flag",
            "webview2_USER_data_FOLDER": r"C:\unsafe-webview-profile",
            "UNCHANGED": "value",
        }
        with (
            tempfile.TemporaryDirectory() as temporary_dir,
            mock.patch.object(launcher.os, "environ", inherited),
        ):
            environment = launcher.build_child_environment(
                Path(temporary_dir).resolve()
            )

        self.assertFalse(
            any(
                name.casefold()
                == launcher.WEBVIEW_ARGUMENTS_VARIABLE.casefold()
                for name in environment
            )
        )
        self.assertEqual(environment["UNCHANGED"], "value")
        self.assertEqual(
            inherited["WebView2_Additional_Browser_Arguments"],
            "--unsafe-inherited-flag",
        )
        self.assertNotEqual(
            environment["WEBVIEW2_USER_DATA_FOLDER"],
            inherited["webview2_USER_data_FOLDER"],
        )

    def test_child_environment_replaces_all_inherited_product_atelier_keys(self) -> None:
        inherited = {
            "PrOdUcT_AtElIeR_DaTa_DiR": r"C:\unsafe-data",
            "PRODUCT_ATELIER_WEBVIEW_DATA_DIR": r"C:\unsafe-webview",
            "product_atelier_unknown_legacy_key": "unsafe-legacy-value",
            "UNCHANGED": "value",
        }
        expected_isolation_keys = {
            "PRODUCT_ATELIER_CANDIDATE_ISOLATION",
            "PRODUCT_ATELIER_DATA_DIR",
            "PRODUCT_ATELIER_LEGACY_CONFIG",
            "PRODUCT_ATELIER_KNOWLEDGE_BASE",
            "PRODUCT_ATELIER_WEBVIEW_DATA_DIR",
        }
        with (
            tempfile.TemporaryDirectory() as temporary_dir,
            mock.patch.object(launcher.os, "environ", inherited),
        ):
            data_dir = Path(temporary_dir).resolve()
            environment = launcher.build_child_environment(data_dir)

        actual_isolation_keys = {
            name
            for name in environment
            if name.casefold().startswith("product_atelier_")
        }
        self.assertEqual(actual_isolation_keys, expected_isolation_keys)
        self.assertEqual(environment["PRODUCT_ATELIER_DATA_DIR"], str(data_dir))
        self.assertEqual(environment["UNCHANGED"], "value")
        self.assertEqual(
            inherited["product_atelier_unknown_legacy_key"],
            "unsafe-legacy-value",
        )

    def test_child_environment_sets_only_the_exact_requested_cdp_argument(self) -> None:
        inherited = {
            "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": "--remote-debugging-port=1 --other",
        }
        with (
            tempfile.TemporaryDirectory() as temporary_dir,
            mock.patch.object(launcher.os, "environ", inherited),
        ):
            environment = launcher.build_child_environment(
                Path(temporary_dir).resolve(),
                cdp_port=9222,
            )

        self.assertEqual(
            environment[launcher.WEBVIEW_ARGUMENTS_VARIABLE],
            "--remote-debugging-port=9222",
        )

    def test_cdp_port_rejects_bool_non_integer_and_out_of_range_values(self) -> None:
        for value in (True, False, "9222", 0, 65536, -1):
            with self.subTest(value=value):
                with self.assertRaises(launcher.LaunchSafetyError):
                    launcher.validate_cdp_port(value)  # type: ignore[arg-type]
        self.assertEqual(launcher.validate_cdp_port(1), 1)
        self.assertEqual(launcher.validate_cdp_port(65535), 65535)
        self.assertIsNone(launcher.validate_cdp_port(None))

    def test_output_path_is_absolute_and_cannot_target_formal_release(self) -> None:
        with self.assertRaisesRegex(launcher.LaunchSafetyError, "absolute"):
            launcher.resolve_output_path("capture.png")
        formal_output = ROOT / "release" / "ProductAtelier-Portable" / "capture.png"
        with self.assertRaisesRegex(launcher.LaunchSafetyError, "formal"):
            launcher.resolve_output_path(formal_output)

        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            missing_formal_output = (
                root / "release" / "ProductAtelier-Portable" / "capture.png"
            )
            with self.assertRaisesRegex(launcher.LaunchSafetyError, "formal"):
                launcher.resolve_output_path(missing_formal_output)
            with self.assertRaisesRegex(launcher.LaunchSafetyError, "parent"):
                launcher.resolve_output_path(root / "missing" / "capture.png")
            existing = root / "existing.png"
            existing.write_bytes(b"evidence")
            with self.assertRaisesRegex(launcher.LaunchSafetyError, "overwrite"):
                launcher.resolve_output_path(existing)

    def test_candidate_identity_rejects_stale_commit_and_sidecar_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            executable, sidecar, app_hash, tree_hash = self._write_candidate(
                Path(temporary_dir).resolve()
            )
            with self.assertRaisesRegex(launcher.LaunchSafetyError, "manifest commit"):
                launcher.validate_candidate_identity(
                    executable, "b" * 40, app_hash, tree_hash
                )

            sidecar.write_bytes(b"tampered")
            with self.assertRaisesRegex(launcher.LaunchSafetyError, "hash"):
                launcher.validate_candidate_identity(
                    executable, self.COMMIT, app_hash, tree_hash
                )

    def test_candidate_path_rejects_a_valid_tree_at_any_alternate_location(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            executable, _, _, _ = self._write_candidate(root)
            alternate_root = root / "alternate-candidate"
            alternate_root.mkdir()
            alternate = alternate_root / executable.name
            alternate.write_bytes(executable.read_bytes())

            with self.assertRaisesRegex(launcher.LaunchSafetyError, "canonical path"):
                launcher.resolve_candidate_executable(alternate)

    def test_candidate_path_rejects_a_reparse_like_canonical_root_before_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            executable, _, _, _ = self._write_candidate(root)
            candidate_root = executable.parent
            with mock.patch.object(
                launcher,
                "_is_link_like",
                side_effect=lambda path: path == candidate_root,
            ):
                with self.assertRaisesRegex(
                    launcher.LaunchSafetyError,
                    "regular directory",
                ):
                    launcher.resolve_candidate_executable(executable)

    def test_candidate_identity_rejects_invalid_or_mismatched_app_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            executable, _, app_hash, tree_hash = self._write_candidate(
                Path(temporary_dir).resolve()
            )
            with self.assertRaisesRegex(launcher.LaunchSafetyError, "64-character"):
                launcher.validate_candidate_identity(
                    executable, self.COMMIT, "invalid", tree_hash
                )
            with self.assertRaisesRegex(launcher.LaunchSafetyError, "app executable hash"):
                launcher.validate_candidate_identity(
                    executable, self.COMMIT, "F" * 64, tree_hash
                )
            self.assertEqual(len(app_hash), 64)

    def test_candidate_identity_rejects_any_tree_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            executable, sidecar, app_hash, tree_hash = self._write_candidate(
                Path(temporary_dir).resolve()
            )
            internal = sidecar.parent / "_internal"
            internal.mkdir()
            (internal / "injected.dll").write_bytes(b"unexpected")
            with self.assertRaisesRegex(launcher.LaunchSafetyError, "directory tree"):
                launcher.validate_candidate_identity(
                    executable, self.COMMIT, app_hash, tree_hash
                )

    def test_screenshot_output_cannot_modify_candidate_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            executable, _, _, _ = self._write_candidate(Path(temporary_dir).resolve())
            with self.assertRaisesRegex(launcher.LaunchSafetyError, "outside"):
                launcher.assert_output_outside_candidate(
                    executable.parent / "capture.png",
                    executable,
                )

    def test_expected_git_commit_must_be_full_hash(self) -> None:
        with self.assertRaisesRegex(launcher.LaunchSafetyError, "40-character"):
            launcher.validate_expected_git_commit("abc123")

    def test_session_exposes_expected_git_commit_as_read_only(self) -> None:
        session = launcher.CandidateLaunchSession(
            executable=Path("candidate.exe"),
            expected_git_commit=self.COMMIT,
            expected_app_sha256="A" * 64,
            expected_tree_sha256="B" * 64,
        )
        self.assertEqual(session.expected_git_commit, self.COMMIT)
        with self.assertRaises(AttributeError):
            session.expected_git_commit = "b" * 40  # type: ignore[misc]

    def test_receipt_process_create_time_rejects_invalid_values_fail_closed(self) -> None:
        invalid_values = (
            None,
            "not-a-time",
            object(),
            10**1000,
            float("nan"),
            float("inf"),
            -1,
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    launcher.LaunchSafetyError,
                    "process create time is invalid",
                ):
                    launcher._receipt_process_create_time(value)
        self.assertEqual(launcher._receipt_process_create_time(1000.0), 1000.0)

    def test_source_contains_no_global_taskkill(self) -> None:
        source = (ROOT / "tools" / "launch_and_shoot.py").read_text(encoding="utf-8")
        self.assertNotIn("taskkill", source.casefold())
        self.assertNotIn('"/im"', source.casefold())

    @unittest.skipUnless(os.name == "nt", "Windows file-share semantics only")
    def test_candidate_tree_locks_block_file_and_directory_mutation_until_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            app = root / "Product Atelier.exe"
            nested = root / "python-server"
            nested.mkdir()
            sidecar = nested / "python-server.exe"
            app.write_bytes(b"app")
            sidecar.write_bytes(b"sidecar")
            expected_tree = launcher.directory_inventory(root)["tree_sha256"]

            locks = launcher.acquire_candidate_tree_locks(root, expected_tree)
            injected = nested / "injected.dll"
            injection_was_blocked = False
            try:
                with self.assertRaises(OSError):
                    app.write_bytes(b"replaced")
                with self.assertRaises(OSError):
                    sidecar.unlink()
                with self.assertRaises(OSError):
                    nested.rename(root / "renamed-python-server")
                try:
                    injected.write_bytes(b"injected")
                except OSError:
                    injection_was_blocked = True
            finally:
                lock_errors = locks.close()

            if injection_was_blocked:
                self.assertEqual(lock_errors, [])
            else:
                self.assertTrue(
                    any("tree changed" in error for error in lock_errors),
                    lock_errors,
                )

            app.write_bytes(b"replaced")
            injected.unlink(missing_ok=True)
            sidecar.unlink()

    def test_candidate_tree_lock_rejects_a_mismatched_expected_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            (root / "artifact.bin").write_bytes(b"artifact")
            with self.assertRaisesRegex(
                launcher.LaunchSafetyError,
                "changed before launch locking|changed while its locks were acquired",
            ):
                launcher.acquire_candidate_tree_locks(root, "F" * 64)

    def test_sidecar_filter_requires_exact_path_and_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            expected = root / "candidate" / "python-server" / "python-server.exe"
            wrong_path = root / "other" / "python-server.exe"
            processes = [
                FakeSidecarProcess(101, 42, expected),
                FakeSidecarProcess(102, 999, expected),
                FakeSidecarProcess(103, 42, wrong_path),
                FakeSidecarProcess(104, 42, Path("python-server.exe")),
            ]
            matches = launcher.matching_sidecars(
                expected_executable=expected,
                expected_parent_pid=42,
                processes=processes,
            )
            self.assertEqual([process.identity.pid for process in matches], [101])

    def test_app_timeout_uses_exact_process_handle_kill(self) -> None:
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="candidate", timeout=5),
            0,
        ]
        launcher._stop_launched_app(process)
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()

    def test_app_identity_rejects_pid_reuse(self) -> None:
        app = FakeAppProcess()
        captured = launcher.ProcessIdentity(pid=app.pid, create_time=1000.0)
        live_process = mock.Mock()
        live_process.is_running.return_value = True
        live_process.create_time.return_value = 2000.0
        with mock.patch.object(launcher.psutil, "Process", return_value=live_process):
            self.assertFalse(launcher.launched_app_identity_is_current(app, captured))
            with self.assertRaisesRegex(launcher.LaunchSafetyError, "PID identity"):
                launcher.assert_launched_app_identity(app, captured)

    def test_captured_app_identity_requires_the_canonical_executable_path(self) -> None:
        app = FakeAppProcess()
        expected = Path(tempfile.gettempdir()).resolve() / "Product Atelier.exe"
        live_process = mock.Mock()
        live_process.is_running.return_value = True
        live_process.create_time.return_value = 1000.0
        live_process.exe.return_value = str(expected.with_name("alternate.exe"))

        with (
            mock.patch.object(launcher.psutil, "Process", return_value=live_process),
            self.assertRaisesRegex(launcher.LaunchSafetyError, "canonical candidate app"),
        ):
            launcher.capture_launched_app_identity(app, expected)

    def test_capture_rechecks_app_identity_before_and_after_pixels(self) -> None:
        app = FakeAppProcess()
        identity = launcher.ProcessIdentity(pid=app.pid, create_time=1000.0)
        screenshot = SimpleNamespace(
            find_window_by_pid=mock.Mock(return_value=(1, (0, 0, 10, 10), "Candidate")),
            capture_region=mock.Mock(return_value=(b"pixels", 10, 10)),
            save_png=mock.Mock(
                side_effect=lambda _data, _width, _height, path: Path(path).write_bytes(
                    b"png"
                )
            ),
        )
        with (
            tempfile.TemporaryDirectory() as temporary_dir,
            mock.patch.dict(sys.modules, {"screenshot": screenshot}),
            mock.patch.object(launcher, "assert_launched_app_identity") as assert_identity,
            mock.patch.object(launcher, "assert_window_owned_by_pid") as assert_owner,
        ):
            output = Path(temporary_dir) / "capture.png"
            pending = launcher.capture_launched_window(
                app_process=app,
                app_identity=identity,
                output_path=output,
                wait_seconds=0,
                padding=0,
            )
            self.assertFalse(output.exists())
            self.assertTrue(pending.is_file())
            self.assertTrue(pending.name.startswith(".incomplete-"))
        self.assertEqual(assert_identity.call_count, 4)
        self.assertEqual(assert_owner.call_count, 2)
        self.assertEqual(
            [call.args for call in assert_owner.call_args_list],
            [(1, app.pid), (1, app.pid)],
        )
        screenshot.find_window_by_pid.assert_called_once_with(app.pid)
        screenshot.save_png.assert_called_once()

    def test_capture_rejects_window_owner_change_before_writing_png(self) -> None:
        app = FakeAppProcess()
        identity = launcher.ProcessIdentity(pid=app.pid, create_time=1000.0)
        screenshot = SimpleNamespace(
            find_window_by_pid=mock.Mock(return_value=(1, (0, 0, 10, 10), "Candidate")),
            capture_region=mock.Mock(return_value=(b"pixels", 10, 10)),
            save_png=mock.Mock(),
        )
        with (
            tempfile.TemporaryDirectory() as temporary_dir,
            mock.patch.dict(sys.modules, {"screenshot": screenshot}),
            mock.patch.object(launcher, "assert_launched_app_identity"),
            mock.patch.object(
                launcher,
                "assert_window_owned_by_pid",
                side_effect=[None, launcher.LaunchSafetyError("window owner changed")],
            ),
        ):
            output = Path(temporary_dir) / "capture.png"
            with self.assertRaisesRegex(launcher.LaunchSafetyError, "owner changed"):
                launcher.capture_launched_window(
                    app_process=app,
                    app_identity=identity,
                    output_path=output,
                    wait_seconds=0,
                    padding=0,
                )
            self.assertFalse(output.exists())
            self.assertEqual(list(output.parent.glob(".incomplete-*.png")), [])
        screenshot.capture_region.assert_called_once()
        screenshot.save_png.assert_not_called()

    def test_pending_screenshot_publishes_atomically_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            pending = root / ".incomplete-fixture.png"
            output = root / "capture.png"
            pending.write_bytes(b"png")

            self.assertEqual(
                launcher.publish_pending_screenshot(pending, output),
                output,
            )
            self.assertEqual(output.read_bytes(), b"png")
            self.assertFalse(pending.exists())

            second_pending = root / ".incomplete-second.png"
            second_pending.write_bytes(b"other")
            with self.assertRaisesRegex(launcher.LaunchSafetyError, "overwrite"):
                launcher.publish_pending_screenshot(second_pending, output)
            self.assertEqual(output.read_bytes(), b"png")
            self.assertEqual(second_pending.read_bytes(), b"other")

    def test_sidecar_create_time_change_prevents_termination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            expected = Path(temporary_dir).resolve() / "python-server.exe"
            process = FakeSidecarProcess(101, 42, expected, create_time=1000.0)
            tracked = launcher.capture_matching_sidecar(
                process,
                expected_executable=expected,
                expected_parent_pid=42,
            )
            self.assertIsNotNone(tracked)
            process._create_time = 2000.0
            launcher._stop_matching_sidecar(
                tracked,
                expected_executable=expected,
                expected_parent_pid=42,
            )
            self.assertFalse(process.terminated)

    def test_armed_sidecar_pid_reuse_is_not_terminated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            expected = Path(temporary_dir).resolve() / "python-server.exe"
            process = FakeSidecarProcess(101, 42, expected, create_time=1000.0)
            tracked = launcher.TrackedSidecar(
                process=process,
                identity=launcher.ProcessIdentity(pid=101, create_time=1000.0),
            )
            process._create_time = 2000.0

            self.assertFalse(
                launcher.tracked_sidecar_is_current(
                    tracked,
                    expected_executable=expected,
                )
            )
            launcher._stop_matching_sidecar(
                tracked,
                expected_executable=expected,
                expected_parent_pid=None,
            )

            self.assertFalse(process.terminated)

    def test_armed_sidecar_access_denied_fails_closed(self) -> None:
        expected = Path(tempfile.gettempdir()).resolve() / "python-server.exe"
        process = mock.Mock()
        process.pid = 101
        process.create_time.side_effect = launcher.psutil.AccessDenied(101)
        tracked = launcher.TrackedSidecar(
            process=process,
            identity=launcher.ProcessIdentity(pid=101, create_time=1000.0),
        )

        with self.assertRaisesRegex(
            launcher.LaunchSafetyError,
            "Could not revalidate armed sidecar",
        ):
            launcher.tracked_sidecar_is_current(
                tracked,
                expected_executable=expected,
            )
        process.terminate.assert_not_called()

    def test_isolated_cleanup_refuses_path_outside_recorded_temp_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            temp_root = root / "selected-temp"
            temp_root.mkdir()
            outside = root / f"{launcher.ISOLATED_DATA_PREFIX}outside"
            outside.mkdir()
            sentinel = outside / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            location = launcher.IsolatedDataDirectory(outside, temp_root)
            with self.assertRaisesRegex(launcher.LaunchSafetyError, "unsafe"):
                launcher.cleanup_isolated_data_directory(location)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_isolated_cleanup_refuses_link_like_root_or_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            isolated_data = root / f"{launcher.ISOLATED_DATA_PREFIX}link-check"
            isolated_data.mkdir()
            location = launcher.IsolatedDataDirectory(isolated_data, root)

            for link_like_path in (root, isolated_data):
                with self.subTest(link_like_path=link_like_path):
                    with (
                        mock.patch.object(
                            launcher,
                            "_is_link_like",
                            side_effect=lambda path, target=link_like_path: path == target,
                        ),
                        mock.patch.object(launcher.shutil, "rmtree") as remove_tree,
                    ):
                        with self.assertRaisesRegex(
                            launcher.LaunchSafetyError,
                            "must be a regular directory",
                        ):
                            launcher.cleanup_isolated_data_directory(location)
                    remove_tree.assert_not_called()
                    self.assertTrue(isolated_data.is_dir())

    def test_cleanup_revalidates_quarantine_before_recursive_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            isolated_data = root / f"{launcher.ISOLATED_DATA_PREFIX}exchange"
            isolated_data.mkdir()
            sentinel = isolated_data / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            location = launcher.IsolatedDataDirectory(isolated_data, root)

            def becomes_link_like_after_rename(path: Path) -> bool:
                return path.exists() and path.name.startswith(
                    f"{launcher.ISOLATED_DATA_PREFIX}cleanup-"
                )

            with (
                mock.patch.object(
                    launcher,
                    "_is_link_like",
                    side_effect=becomes_link_like_after_rename,
                ),
                mock.patch.object(launcher.shutil, "rmtree") as remove_tree,
            ):
                with self.assertRaisesRegex(
                    launcher.LaunchSafetyError,
                    "preserved at",
                ):
                    launcher.cleanup_isolated_data_directory(location)

            remove_tree.assert_not_called()
            self.assertFalse(isolated_data.exists())
            quarantines = list(
                root.glob(f"{launcher.ISOLATED_DATA_PREFIX}cleanup-*")
            )
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(
                (quarantines[0] / "keep.txt").read_text(encoding="utf-8"),
                "keep",
            )

    def test_isolated_cleanup_retries_transient_windows_directory_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            isolated_data = root / f"{launcher.ISOLATED_DATA_PREFIX}transient-lock"
            isolated_data.mkdir()
            (isolated_data / "sentinel.txt").write_text("test", encoding="utf-8")
            location = launcher.IsolatedDataDirectory(isolated_data, root)
            real_replace = launcher.os.replace
            attempts = 0

            def transient_replace(source: Path, destination: Path) -> None:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError(13, "directory is still in use", str(source))
                real_replace(source, destination)

            with (
                mock.patch.object(launcher.os, "replace", side_effect=transient_replace),
                mock.patch.object(launcher.time, "sleep") as sleep,
            ):
                launcher.cleanup_isolated_data_directory(location)

            self.assertEqual(attempts, 2)
            sleep.assert_called_once()
            self.assertFalse(isolated_data.exists())
            self.assertEqual(
                list(root.glob(f"{launcher.ISOLATED_DATA_PREFIX}cleanup-*")),
                [],
            )

    def test_sidecar_discovery_failure_still_stops_launched_app(self) -> None:
        app = FakeAppProcess()
        identity = launcher.ProcessIdentity(pid=app.pid, create_time=1000.0)
        expected_sidecar = Path(tempfile.gettempdir()).resolve() / "python-server.exe"
        with (
            mock.patch.object(
                launcher,
                "launched_app_identity_is_current",
                return_value=True,
            ),
            mock.patch.object(
                launcher.psutil,
                "process_iter",
                side_effect=RuntimeError("synthetic discovery fault"),
            ),
        ):
            errors = launcher.cleanup_launched_processes(app, identity, expected_sidecar)
        self.assertTrue(app.terminated)
        self.assertGreaterEqual(len(errors), 1)
        self.assertTrue(all("discovery" in error for error in errors))

    def test_late_sidecar_is_captured_after_app_exit_and_stopped(self) -> None:
        app = FakeAppProcess()
        identity = launcher.ProcessIdentity(pid=app.pid, create_time=1000.0)
        expected_sidecar = Path(tempfile.gettempdir()).resolve() / "python-server.exe"
        late_sidecar = FakeSidecarProcess(
            101,
            app.pid,
            expected_sidecar,
            create_time=1001.0,
        )
        processes: list[FakeSidecarProcess] = []

        def stop_app(_process) -> None:
            app.terminate()
            processes.append(late_sidecar)

        with (
            mock.patch.object(
                launcher,
                "launched_app_identity_is_current",
                return_value=True,
            ),
            mock.patch.object(
                launcher.psutil,
                "process_iter",
                side_effect=lambda: iter(processes),
            ),
            mock.patch.object(
                launcher,
                "_stop_launched_app",
                side_effect=stop_app,
            ),
        ):
            errors = launcher.cleanup_launched_processes(app, identity, expected_sidecar)

        self.assertEqual(errors, [])
        self.assertTrue(late_sidecar.terminated)

    def test_unarmed_graceful_app_exit_does_not_discover_and_kill_sidecar(self) -> None:
        app = FakeAppProcess()
        identity = launcher.ProcessIdentity(pid=app.pid, create_time=1000.0)
        expected_sidecar = Path(tempfile.gettempdir()).resolve() / "python-server.exe"
        sidecar = FakeSidecarProcess(
            101,
            app.pid,
            expected_sidecar,
            create_time=1001.0,
        )
        app.returncode = 0

        with mock.patch.object(
            launcher.psutil,
            "process_iter",
            side_effect=lambda: iter((sidecar,)),
        ):
            errors = launcher.cleanup_launched_processes(app, identity, expected_sidecar)

        self.assertEqual(
            errors,
            ["candidate app identity changed before sidecar cleanup"],
        )
        self.assertFalse(sidecar.terminated)

    def test_webview_process_filter_requires_exact_profile_path(self) -> None:
        data_root = Path(tempfile.gettempdir()).resolve() / "isolated-webview"

        def webview_process(pid: int, name: str, profile: Path):
            process = mock.Mock()
            process.pid = pid
            process.name.return_value = name
            process.cmdline.return_value = [
                "msedgewebview2.exe",
                f"--user-data-dir={profile}",
            ]
            process.create_time.return_value = 1000.0 + pid
            process.exe.return_value = r"C:\Program Files\WebView2\msedgewebview2.exe"
            process.is_running.return_value = True
            return process

        expected = webview_process(
            101,
            "msedgewebview2.exe",
            data_root / launcher.WEBVIEW_RUNTIME_DATA_DIRECTORY_NAME,
        )
        other_profile = webview_process(102, "msedgewebview2.exe", data_root.parent / "other")
        nested_profile = webview_process(
            103,
            "msedgewebview2.exe",
            data_root / "unexpected-child",
        )
        other_name = webview_process(104, "other.exe", data_root)

        matches = launcher.matching_webview_processes(
            data_root,
            processes=(expected, other_profile, nested_profile, other_name),
        )

        self.assertEqual([identity.pid for identity in matches], [101])

    def test_webview_process_filter_rejects_ambiguous_profile_arguments(self) -> None:
        data_root = Path(tempfile.gettempdir()).resolve() / "isolated-webview"
        process = mock.Mock()
        process.pid = 101
        process.name.return_value = launcher.WEBVIEW_PROCESS_NAME
        process.cmdline.return_value = [
            launcher.WEBVIEW_PROCESS_NAME,
            f"--user-data-dir={data_root}",
            f"--user-data-dir={data_root.parent / 'other'}",
        ]

        with self.assertRaisesRegex(
            launcher.LaunchSafetyError,
            "ambiguous user-data profile",
        ):
            launcher.matching_webview_processes(
                data_root,
                processes=(process,),
            )

    def test_webview_process_inspection_access_denied_fails_closed(self) -> None:
        data_root = Path(tempfile.gettempdir()).resolve() / "isolated-webview"
        process = mock.Mock()
        process.pid = 101
        process.name.return_value = launcher.WEBVIEW_PROCESS_NAME
        process.cmdline.side_effect = launcher.psutil.AccessDenied(101)

        with self.assertRaisesRegex(
            launcher.LaunchSafetyError,
            "Could not prove isolated WebView process identity",
        ):
            launcher.matching_webview_processes(
                data_root,
                processes=(process,),
            )

    def test_webview_shutdown_timeout_reports_the_bound_profile_residual(self) -> None:
        data_root = Path(tempfile.gettempdir()).resolve() / "isolated-webview"
        residual = launcher.ProcessIdentity(pid=101, create_time=1000.0)

        with (
            mock.patch.object(
                launcher,
                "matching_webview_processes",
                return_value=[residual],
            ),
            mock.patch.object(launcher.time, "monotonic", side_effect=[0.0, 1.0]),
            self.assertRaisesRegex(
                launcher.LaunchSafetyError,
                "PID 101@1000.0",
            ),
        ):
            launcher.wait_for_webview_processes_to_exit(
                data_root,
                timeout=0.5,
                poll_interval=0.1,
            )

    @unittest.skipUnless(os.name == "nt", "Windows extended-length paths are Windows-only")
    def test_webview_process_filter_accepts_extended_length_profile_path(self) -> None:
        data_root = Path(tempfile.gettempdir()).resolve() / "isolated-webview"
        process = mock.Mock()
        process.pid = 101
        process.name.return_value = launcher.WEBVIEW_PROCESS_NAME
        process.cmdline.return_value = [
            launcher.WEBVIEW_PROCESS_NAME,
            rf"--user-data-dir=\\?\{data_root}\{launcher.WEBVIEW_RUNTIME_DATA_DIRECTORY_NAME}",
        ]
        process.is_running.return_value = True
        process.create_time.return_value = 1000.0

        matches = launcher.matching_webview_processes(
            data_root,
            processes=(process,),
        )

        self.assertEqual(matches, [launcher.ProcessIdentity(pid=101, create_time=1000.0)])

    def test_session_restart_reuses_bound_isolated_data_before_final_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            executable, _, app_hash, tree_hash = self._write_candidate(root)
            isolated_path = root / f"{launcher.ISOLATED_DATA_PREFIX}restart"
            isolated_path.mkdir()
            location = launcher.IsolatedDataDirectory(isolated_path, root)
            apps = [FakeAppProcess(42), FakeAppProcess(43)]
            identities = [
                launcher.ProcessIdentity(pid=42, create_time=1000.0),
                launcher.ProcessIdentity(pid=43, create_time=2000.0),
            ]
            sidecar_process = FakeSidecarProcess(
                101,
                42,
                executable.parent / "python-server" / "python-server.exe",
                create_time=1001.0,
            )
            tracked_sidecar = launcher.TrackedSidecar(
                process=sidecar_process,
                identity=launcher.ProcessIdentity(pid=101, create_time=1001.0),
            )
            webview_identity = launcher.ProcessIdentity(pid=202, create_time=1002.0)
            launches: list[tuple[Path, dict[str, str]]] = []
            events: list[str] = []

            def popen(command, *, cwd, env):
                app = apps[len(launches)]
                launches.append((Path(cwd), dict(env)))
                events.append(f"launch-{app.pid}")
                return app

            def cleanup_processes(app, *_args) -> list[str]:
                events.append(f"stop-{app.pid}")
                app.terminate()
                return []

            sidecar_matches = iter(((tracked_sidecar,), ()))

            def matching_sidecars(**_kwargs):
                return list(next(sidecar_matches))

            def cleanup_data(actual: launcher.IsolatedDataDirectory) -> None:
                self.assertEqual(actual, location)
                self.assertTrue((actual.path / "restart-sentinel.txt").is_file())
                events.append("data-cleanup")

            file_locks = mock.Mock()
            file_locks.poll_change_errors.return_value = []
            file_locks.close.side_effect = lambda: events.append("locks-close") or []

            with (
                mock.patch.object(
                    launcher,
                    "acquire_candidate_tree_locks",
                    return_value=file_locks,
                ),
                mock.patch.object(
                    launcher,
                    "create_isolated_data_directory",
                    return_value=location,
                ),
                mock.patch.object(launcher.subprocess, "Popen", side_effect=popen),
                mock.patch.object(
                    launcher,
                    "capture_launched_app_identity",
                    side_effect=identities,
                ),
                mock.patch.object(launcher, "assert_launched_app_identity"),
                mock.patch.object(
                    launcher,
                    "cleanup_launched_processes",
                    side_effect=cleanup_processes,
                ),
                mock.patch.object(
                    launcher,
                    "matching_sidecars",
                    side_effect=matching_sidecars,
                ),
                mock.patch.object(
                    launcher,
                    "matching_webview_processes",
                    return_value=[webview_identity],
                ),
                mock.patch.object(
                    launcher,
                    "wait_for_webview_processes_to_exit",
                    return_value=None,
                    create=True,
                ) as wait_webview,
                mock.patch.object(
                    launcher,
                    "cleanup_isolated_data_directory",
                    side_effect=cleanup_data,
                ),
            ):
                with launcher.CandidateLaunchSession(
                    executable=executable,
                    expected_git_commit=self.COMMIT,
                    expected_app_sha256=app_hash,
                    expected_tree_sha256=tree_hash,
                ) as session:
                    first_data_dir = session.data_dir
                    (first_data_dir / "restart-sentinel.txt").write_text(
                        "preserve", encoding="utf-8"
                    )
                    session.arm_graceful_close()
                    apps[0].returncode = 0
                    session.restart_with_same_data()
                    self.assertEqual(session.data_dir, first_data_dir)
                    self.assertEqual(session.pid, 43)
                    self.assertTrue((first_data_dir / "restart-sentinel.txt").is_file())

            self.assertEqual(len(launches), 2)
            self.assertEqual(launches[0], launches[1])
            self.assertTrue(sidecar_process.terminated)
            wait_webview.assert_called_once_with(isolated_path / "webview2-user-data")
            self.assertNotIn("stop-42", events)
            self.assertLess(events.index("stop-43"), events.index("data-cleanup"))
            self.assertLess(events.index("data-cleanup"), events.index("locks-close"))

    def test_session_restart_rechecks_isolated_identity_immediately_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            isolated_path = root / f"{launcher.ISOLATED_DATA_PREFIX}restart-exchange"
            knowledge_path = isolated_path / "no-knowledge-vault"
            webview_path = isolated_path / "webview2-user-data"
            knowledge_path.mkdir(parents=True)
            webview_path.mkdir()
            location = launcher.IsolatedDataDirectory(isolated_path, root)

            session = launcher.CandidateLaunchSession(
                executable=Path("candidate.exe"),
                expected_git_commit=self.COMMIT,
                expected_app_sha256="A" * 64,
                expected_tree_sha256="B" * 64,
            )
            session._entered = True
            session._isolated_location = location
            session.data_dir = isolated_path
            session.webview_data_dir = webview_path
            session.knowledge_base_dir = knowledge_path
            session.legacy_config_path = isolated_path / "no-legacy-config.json"
            session.data_dir_identity = launcher._path_identity(
                isolated_path,
                "isolated data directory",
            )
            session.webview_data_dir_identity = launcher._path_identity(
                webview_path,
                "WebView2 data directory",
            )
            session.knowledge_base_dir_identity = launcher._path_identity(
                knowledge_path,
                "isolated knowledge directory",
            )
            session._candidate_snapshot = mock.sentinel.snapshot

            displaced_webview = isolated_path / "webview2-user-data-original"

            def exchange_webview(*_args, **_kwargs) -> dict[str, str]:
                webview_path.rename(displaced_webview)
                webview_path.mkdir()
                return {}

            with (
                mock.patch.object(session, "complete_graceful_close", return_value=0),
                mock.patch.object(session, "_candidate_protection_errors", return_value=[]),
                mock.patch.object(
                    launcher,
                    "rebuild_child_environment",
                    side_effect=exchange_webview,
                ),
                mock.patch.object(session, "_launch_runtime") as launch_runtime,
                self.assertRaisesRegex(
                    launcher.LaunchSafetyError,
                    "WebView2 data directory identity changed",
                ),
            ):
                session.restart_with_same_data()

            launch_runtime.assert_not_called()

    def test_graceful_close_waits_for_delayed_natural_exit(self) -> None:
        session = launcher.CandidateLaunchSession(
            executable=Path("candidate.exe"),
            expected_git_commit=self.COMMIT,
            expected_app_sha256="A" * 64,
            expected_tree_sha256="B" * 64,
        )
        process = mock.Mock()
        process.pid = 42
        process.poll.side_effect = [None, 0]
        identity = launcher.ProcessIdentity(pid=42, create_time=1000.0)
        session.process = process
        session.process_identity = identity
        session._graceful_close_binding = launcher.GracefulCloseBinding(
            app_identity=identity,
            sidecars=(),
            webviews=(),
            armed_at=1001.0,
        )

        with (
            mock.patch.object(launcher.time, "monotonic", side_effect=[0.0, 0.1]),
            mock.patch.object(launcher.time, "sleep") as sleep,
            mock.patch.object(
                session,
                "_complete_armed_app_exit",
                return_value=0,
            ) as complete,
        ):
            returncode = session.complete_graceful_close(
                timeout=1.0,
                poll_interval=0.1,
            )

        self.assertEqual(returncode, 0)
        sleep.assert_called_once_with(0.1)
        complete.assert_called_once_with(require_success=True)

    def test_graceful_close_timeout_preserves_binding_for_retry(self) -> None:
        session = launcher.CandidateLaunchSession(
            executable=Path("candidate.exe"),
            expected_git_commit=self.COMMIT,
            expected_app_sha256="A" * 64,
            expected_tree_sha256="B" * 64,
        )
        process = mock.Mock()
        process.pid = 42
        process.poll.return_value = None
        identity = launcher.ProcessIdentity(pid=42, create_time=1000.0)
        binding = launcher.GracefulCloseBinding(
            app_identity=identity,
            sidecars=(),
            webviews=(),
            armed_at=1001.0,
        )
        session.process = process
        session.process_identity = identity
        session._graceful_close_binding = binding

        with (
            mock.patch.object(launcher.time, "monotonic", side_effect=[0.0, 1.0]),
            self.assertRaisesRegex(launcher.LaunchSafetyError, "still running"),
        ):
            session.complete_graceful_close(timeout=0.5, poll_interval=0.1)

        self.assertIs(session._graceful_close_binding, binding)
        process.poll.return_value = 0
        with mock.patch.object(
            session,
            "_complete_armed_app_exit",
            return_value=0,
        ) as complete:
            self.assertEqual(
                session.complete_graceful_close(timeout=0.5, poll_interval=0.1),
                0,
            )
        complete.assert_called_once_with(require_success=True)

    def test_graceful_close_rejects_nonzero_app_exit(self) -> None:
        session = launcher.CandidateLaunchSession(
            executable=Path("candidate.exe"),
            expected_git_commit=self.COMMIT,
            expected_app_sha256="A" * 64,
            expected_tree_sha256="B" * 64,
        )
        process = FakeAppProcess()
        process.returncode = 7
        identity = launcher.ProcessIdentity(pid=process.pid, create_time=1000.0)
        binding = launcher.GracefulCloseBinding(
            app_identity=identity,
            sidecars=(),
            webviews=(),
            armed_at=1001.0,
        )
        session.process = process
        session.process_identity = identity
        session._graceful_close_binding = binding

        with self.assertRaisesRegex(launcher.LaunchSafetyError, "returncode=7"):
            session.complete_graceful_close(timeout=0.5, poll_interval=0.1)

        self.assertIs(session._graceful_close_binding, binding)

    def test_abort_after_armed_app_exit_cleans_only_bound_children(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            isolated_path = root / f"{launcher.ISOLATED_DATA_PREFIX}armed-abort"
            knowledge_path = isolated_path / "no-knowledge-vault"
            webview_path = isolated_path / "webview2-user-data"
            knowledge_path.mkdir(parents=True)
            webview_path.mkdir()
            sidecar_path = root / "candidate" / "python-server.exe"
            sidecar = FakeSidecarProcess(
                101,
                42,
                sidecar_path,
                create_time=1001.0,
            )
            tracked = launcher.TrackedSidecar(
                process=sidecar,
                identity=launcher.ProcessIdentity(pid=101, create_time=1001.0),
            )
            app = FakeAppProcess()
            app.returncode = 9
            app_identity = launcher.ProcessIdentity(pid=42, create_time=1000.0)
            session = launcher.CandidateLaunchSession(
                executable=Path("candidate.exe"),
                expected_git_commit=self.COMMIT,
                expected_app_sha256="A" * 64,
                expected_tree_sha256="B" * 64,
            )
            session.process = app
            session.process_identity = app_identity
            session.sidecar_identity = launcher.VerifiedSidecarIdentity(
                path=sidecar_path,
                sha256="C" * 64,
                manifest_sha256="D" * 64,
            )
            session._isolated_location = launcher.IsolatedDataDirectory(
                isolated_path,
                root,
            )
            session.data_dir = isolated_path
            session.webview_data_dir = webview_path
            session.knowledge_base_dir = knowledge_path
            session.legacy_config_path = isolated_path / "no-legacy-config.json"
            session.data_dir_identity = launcher._path_identity(
                isolated_path,
                "isolated data directory",
            )
            session.webview_data_dir_identity = launcher._path_identity(
                webview_path,
                "WebView2 data directory",
            )
            session.knowledge_base_dir_identity = launcher._path_identity(
                knowledge_path,
                "isolated knowledge directory",
            )
            session._graceful_close_binding = launcher.GracefulCloseBinding(
                app_identity=app_identity,
                sidecars=(tracked,),
                webviews=(launcher.ProcessIdentity(pid=202, create_time=1002.0),),
                armed_at=1003.0,
            )

            with (
                mock.patch.object(launcher, "matching_sidecars", return_value=[]),
                mock.patch.object(
                    launcher,
                    "wait_for_webview_processes_to_exit",
                ) as wait_webview,
            ):
                errors = session._stop_current_runtime()

            self.assertEqual(errors, [])
            self.assertTrue(sidecar.terminated)
            self.assertIsNone(session.process)
            self.assertIsNone(session._graceful_close_binding)
            wait_webview.assert_called_once_with(webview_path)

    def test_webview_residual_preserves_isolated_data_during_final_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            isolated_path = root / f"{launcher.ISOLATED_DATA_PREFIX}webview-residual"
            knowledge_path = isolated_path / "no-knowledge-vault"
            webview_path = isolated_path / "webview2-user-data"
            knowledge_path.mkdir(parents=True)
            webview_path.mkdir()
            app = FakeAppProcess()
            app.returncode = 0
            app_identity = launcher.ProcessIdentity(pid=42, create_time=1000.0)
            session = launcher.CandidateLaunchSession(
                executable=Path("candidate.exe"),
                expected_git_commit=self.COMMIT,
                expected_app_sha256="A" * 64,
                expected_tree_sha256="B" * 64,
            )
            session.process = app
            session.process_identity = app_identity
            session.sidecar_identity = launcher.VerifiedSidecarIdentity(
                path=root / "candidate" / "python-server.exe",
                sha256="C" * 64,
                manifest_sha256="D" * 64,
            )
            session._isolated_location = launcher.IsolatedDataDirectory(
                isolated_path,
                root,
            )
            session.data_dir = isolated_path
            session.webview_data_dir = webview_path
            session.knowledge_base_dir = knowledge_path
            session.legacy_config_path = isolated_path / "no-legacy-config.json"
            session.data_dir_identity = launcher._path_identity(
                isolated_path,
                "isolated data directory",
            )
            session.webview_data_dir_identity = launcher._path_identity(
                webview_path,
                "WebView2 data directory",
            )
            session.knowledge_base_dir_identity = launcher._path_identity(
                knowledge_path,
                "isolated knowledge directory",
            )
            session._graceful_close_binding = launcher.GracefulCloseBinding(
                app_identity=app_identity,
                sidecars=(),
                webviews=(launcher.ProcessIdentity(pid=202, create_time=1002.0),),
                armed_at=1003.0,
            )

            with (
                mock.patch.object(launcher, "matching_sidecars", return_value=[]),
                mock.patch.object(
                    launcher,
                    "wait_for_webview_processes_to_exit",
                    side_effect=launcher.LaunchSafetyError("WebView residual"),
                ),
                mock.patch.object(
                    launcher,
                    "cleanup_isolated_data_directory",
                ) as cleanup_data,
            ):
                errors = session._cleanup_runtime()

            self.assertTrue(any("WebView residual" in error for error in errors))
            self.assertTrue(any("isolated data preserved" in error for error in errors))
            self.assertTrue(isolated_path.is_dir())
            cleanup_data.assert_not_called()

    def test_graceful_close_reports_unbound_sidecar_without_stopping_it(self) -> None:
        expected = Path(tempfile.gettempdir()).resolve() / "python-server.exe"
        app = FakeAppProcess()
        app.returncode = 0
        app_identity = launcher.ProcessIdentity(pid=42, create_time=1000.0)
        bound_process = FakeSidecarProcess(101, 42, expected, create_time=1001.0)
        bound_process.terminated = True
        bound = launcher.TrackedSidecar(
            process=bound_process,
            identity=launcher.ProcessIdentity(pid=101, create_time=1001.0),
        )
        unbound_process = FakeSidecarProcess(102, 42, expected, create_time=1002.0)
        unbound = launcher.TrackedSidecar(
            process=unbound_process,
            identity=launcher.ProcessIdentity(pid=102, create_time=1002.0),
        )
        session = launcher.CandidateLaunchSession(
            executable=Path("candidate.exe"),
            expected_git_commit=self.COMMIT,
            expected_app_sha256="A" * 64,
            expected_tree_sha256="B" * 64,
        )
        session.process = app
        session.process_identity = app_identity
        session.sidecar_identity = launcher.VerifiedSidecarIdentity(
            path=expected,
            sha256="C" * 64,
            manifest_sha256="D" * 64,
        )
        session.webview_data_dir = Path(tempfile.gettempdir()).resolve() / "webview"
        session._graceful_close_binding = launcher.GracefulCloseBinding(
            app_identity=app_identity,
            sidecars=(bound,),
            webviews=(launcher.ProcessIdentity(pid=202, create_time=1003.0),),
            armed_at=1004.0,
        )

        with (
            mock.patch.object(launcher, "matching_sidecars", return_value=[unbound]),
            self.assertRaisesRegex(launcher.LaunchSafetyError, "unbound candidate sidecar"),
        ):
            session.complete_graceful_close(timeout=0.5, poll_interval=0.1)

        self.assertFalse(unbound_process.terminated)

    def test_armed_restart_refuses_to_terminate_a_live_app(self) -> None:
        session = launcher.CandidateLaunchSession(
            executable=Path("candidate.exe"),
            expected_git_commit=self.COMMIT,
            expected_app_sha256="A" * 64,
            expected_tree_sha256="B" * 64,
        )
        session._entered = True
        session.process = FakeAppProcess()
        session.process_identity = launcher.ProcessIdentity(pid=42, create_time=1000.0)
        session._graceful_close_binding = launcher.GracefulCloseBinding(
            app_identity=session.process_identity,
            sidecars=(),
            webviews=(),
            armed_at=1001.0,
        )

        with (
            mock.patch.object(launcher.time, "monotonic", side_effect=[0.0, 1.0]),
            self.assertRaisesRegex(launcher.LaunchSafetyError, "still running"),
        ):
            session.restart_with_same_data(timeout=0.5, poll_interval=0.1)

        self.assertFalse(session.process.terminated)

    def test_cleanup_refuses_quarantine_inode_exchange_before_recursive_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            isolated_data = root / f"{launcher.ISOLATED_DATA_PREFIX}inode-exchange"
            isolated_data.mkdir()
            (isolated_data / "original.txt").write_text("original", encoding="utf-8")
            location = launcher.IsolatedDataDirectory(isolated_data, root)
            real_replace = launcher.os.replace

            def exchange_after_replace(source: Path, destination: Path) -> None:
                real_replace(source, destination)
                for child in destination.iterdir():
                    child.unlink()
                destination.rmdir()
                destination.mkdir()
                (destination / "replacement.txt").write_text(
                    "replacement", encoding="utf-8"
                )

            with (
                mock.patch.object(
                    launcher.os,
                    "replace",
                    side_effect=exchange_after_replace,
                ),
                mock.patch.object(launcher.shutil, "rmtree") as remove_tree,
                self.assertRaisesRegex(launcher.LaunchSafetyError, "identity changed"),
            ):
                launcher.cleanup_isolated_data_directory(location)

            remove_tree.assert_not_called()
            quarantines = list(
                root.glob(f"{launcher.ISOLATED_DATA_PREFIX}cleanup-*")
            )
            self.assertEqual(len(quarantines), 1)
            self.assertTrue((quarantines[0] / "replacement.txt").is_file())

    def test_launch_holds_the_promotion_lock_for_the_complete_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            executable, _, app_hash, tree_hash = self._write_candidate(root)
            isolated_path = root / f"{launcher.ISOLATED_DATA_PREFIX}lock-scope"
            isolated_path.mkdir()
            location = launcher.IsolatedDataDirectory(isolated_path, root)
            app = FakeAppProcess()
            identity = launcher.ProcessIdentity(pid=app.pid, create_time=1000.0)
            lock_held = False

            @contextmanager
            def fake_promotion_lock(project: Path):
                nonlocal lock_held
                self.assertEqual(project, root)
                self.assertFalse(lock_held)
                lock_held = True
                try:
                    yield
                finally:
                    lock_held = False

            file_locks = mock.Mock()
            file_locks.poll_change_errors.return_value = []

            def close_tree_locks() -> list[str]:
                self.assertTrue(lock_held)
                return []

            file_locks.close.side_effect = close_tree_locks

            def cleanup_processes(*_args) -> list[str]:
                self.assertTrue(lock_held)
                return []

            def cleanup_data(*_args) -> None:
                self.assertTrue(lock_held)

            with (
                mock.patch.object(
                    launcher.portable_release,
                    "_promotion_lock",
                    side_effect=fake_promotion_lock,
                ),
                mock.patch.object(
                    launcher,
                    "acquire_candidate_tree_locks",
                    return_value=file_locks,
                ),
                mock.patch.object(
                    launcher,
                    "create_isolated_data_directory",
                    return_value=location,
                ),
                mock.patch.object(launcher.subprocess, "Popen", return_value=app),
                mock.patch.object(
                    launcher,
                    "capture_launched_app_identity",
                    return_value=identity,
                ),
                mock.patch.object(
                    launcher,
                    "cleanup_launched_processes",
                    side_effect=cleanup_processes,
                ),
                mock.patch.object(
                    launcher,
                    "cleanup_isolated_data_directory",
                    side_effect=cleanup_data,
                ),
            ):
                with launcher.CandidateLaunchSession(
                    executable=executable,
                    expected_git_commit=self.COMMIT,
                    expected_app_sha256=app_hash,
                    expected_tree_sha256=tree_hash,
                ) as session:
                    self.assertTrue(lock_held)
                    self.assertEqual(session.pid, app.pid)

            self.assertFalse(lock_held)

    def test_formal_gate_publishes_after_cleanup_while_both_locks_are_held(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            executable, _, app_hash, tree_hash = self._write_candidate(root)
            with self._mock_formal_gate(
                root,
                executable,
                app_hash,
                tree_hash,
            ) as gate:
                result = gate.invoke()

            self.assertEqual(result["status"], "finalized")
            gate.popen.assert_called_once()
            gate.finalize.assert_called_once()
            self.assertTrue(gate.final_output.is_dir())
            self.assertFalse(gate.state["promotion_lock_held"])
            self.assertLess(
                gate.events.index("process-cleanup"),
                gate.events.index("data-cleanup"),
            )
            self.assertLess(
                gate.events.index("data-cleanup"),
                gate.events.index("publish"),
            )
            self.assertLess(
                gate.events.index("publish"),
                gate.events.index("tree-close"),
            )
            self.assertLess(
                gate.events.index("tree-close"),
                gate.events.index("promotion-release"),
            )

    def test_formal_gate_verifier_or_runtime_cleanup_failure_never_publishes(self) -> None:
        failure_cases = (
            {"verifier_error": RuntimeError("synthetic verifier failure")},
            {"process_cleanup_errors": ("synthetic cleanup failure",)},
        )
        for case in failure_cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as temporary_dir:
                    root = Path(temporary_dir).resolve()
                    executable, _, app_hash, tree_hash = self._write_candidate(root)
                    with self._mock_formal_gate(
                        root,
                        executable,
                        app_hash,
                        tree_hash,
                        **case,
                    ) as gate:
                        with self.assertRaisesRegex(
                            Exception,
                            "synthetic (verifier|cleanup) failure",
                        ):
                            gate.invoke()

                    gate.popen.assert_called_once()
                    gate.finalize.assert_not_called()
                    self.assertFalse(gate.final_output.exists())
                    self.assertEqual(
                        list(root.glob(f".{gate.final_output.name}.failed-*")),
                        [],
                    )

    def test_formal_gate_quarantines_publication_when_tree_lock_close_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            executable, _, app_hash, tree_hash = self._write_candidate(root)
            with self._mock_formal_gate(
                root,
                executable,
                app_hash,
                tree_hash,
                tree_close_errors=("synthetic tree close failure",),
            ) as gate:
                with self.assertRaisesRegex(
                    launcher.LaunchSafetyError,
                    "evidence was quarantined.*synthetic tree close failure",
                ):
                    gate.invoke()

            self.assertFalse(gate.final_output.exists())
            quarantines = list(
                root.glob(f".{gate.final_output.name}.failed-*")
            )
            self.assertEqual(len(quarantines), 1)
            self.assertTrue(quarantines[0].is_dir())

    def test_formal_gate_reverifies_after_publication_before_releasing_locks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            executable, _, app_hash, tree_hash = self._write_candidate(root)

            def mutate_candidate() -> None:
                executable.write_bytes(b"candidate-mutated-during-publication")

            with self._mock_formal_gate(
                root,
                executable,
                app_hash,
                tree_hash,
                mutate_during_publication=mutate_candidate,
            ) as gate:
                with self.assertRaisesRegex(
                    launcher.LaunchSafetyError,
                    "evidence was quarantined.*candidate identity after run",
                ):
                    gate.invoke()

            self.assertFalse(gate.final_output.exists())
            quarantines = list(
                root.glob(f".{gate.final_output.name}.failed-*")
            )
            self.assertEqual(len(quarantines), 1)
            self.assertLess(
                gate.events.index("publish"),
                gate.events.index("tree-close"),
            )
            self.assertLess(
                gate.events.index("tree-close"),
                gate.events.index("promotion-release"),
            )

    def test_seed_fixture_runs_after_empty_isolation_and_before_environment_and_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            executable, _, app_hash, tree_hash = self._write_candidate(root)
            isolated_path = root / f"{launcher.ISOLATED_DATA_PREFIX}seed-order"
            isolated_path.mkdir()
            location = launcher.IsolatedDataDirectory(isolated_path, root)
            app = FakeAppProcess()
            identity = launcher.ProcessIdentity(pid=app.pid, create_time=1000.0)
            events: list[str] = []

            def create_isolated() -> launcher.IsolatedDataDirectory:
                self.assertEqual(list(isolated_path.iterdir()), [])
                events.append("create")
                return location

            def seed_fixture(path: Path) -> None:
                self.assertEqual(path, isolated_path)
                self.assertEqual(list(path.iterdir()), [])
                events.append("seed")
                (path / "fixture.json").write_text("{}", encoding="utf-8")

            real_build_environment = launcher.build_child_environment

            def build_environment(path: Path, cdp_port: int | None) -> dict[str, str]:
                self.assertEqual(path, isolated_path)
                self.assertEqual(cdp_port, 9333)
                self.assertTrue((path / "fixture.json").is_file())
                events.append("environment")
                return real_build_environment(path, cdp_port)

            def popen(*_args, **_kwargs) -> FakeAppProcess:
                events.append("popen")
                return app

            file_locks = mock.Mock()
            file_locks.poll_change_errors.return_value = []
            file_locks.close.side_effect = lambda: events.append("locks-close") or []

            seed_module = SimpleNamespace(seed_feedback_checkpoint=seed_fixture)
            with (
                mock.patch.dict(sys.modules, {"seed_feedback_checkpoint": seed_module}),
                mock.patch.object(
                    launcher,
                    "acquire_candidate_tree_locks",
                    return_value=file_locks,
                ),
                mock.patch.object(
                    launcher,
                    "create_isolated_data_directory",
                    side_effect=create_isolated,
                ),
                mock.patch.object(
                    launcher,
                    "build_child_environment",
                    side_effect=build_environment,
                ),
                mock.patch.object(launcher.subprocess, "Popen", side_effect=popen),
                mock.patch.object(
                    launcher,
                    "capture_launched_app_identity",
                    return_value=identity,
                ),
                mock.patch.object(
                    launcher,
                    "cleanup_launched_processes",
                    side_effect=lambda *_args: events.append("process-cleanup") or [],
                ),
                mock.patch.object(
                    launcher,
                    "cleanup_isolated_data_directory",
                    side_effect=lambda *_args: events.append("data-cleanup"),
                ),
            ):
                with launcher.CandidateLaunchSession(
                    executable=executable,
                    expected_git_commit=self.COMMIT,
                    expected_app_sha256=app_hash,
                    expected_tree_sha256=tree_hash,
                    cdp_port=9333,
                    seed_review_fixture=True,
                ) as session:
                    self.assertEqual(session.pid, app.pid)

            self.assertLess(events.index("create"), events.index("seed"))
            self.assertLess(events.index("seed"), events.index("environment"))
            self.assertLess(events.index("environment"), events.index("popen"))
            self.assertLess(events.index("popen"), events.index("process-cleanup"))
            self.assertLess(events.index("process-cleanup"), events.index("data-cleanup"))
            self.assertLess(events.index("data-cleanup"), events.index("locks-close"))

    def test_cleanup_failure_discards_pending_screenshot_without_publishing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            executable, _, app_hash, tree_hash = self._write_candidate(root)
            output = root / "capture.png"
            isolated_path = root / f"{launcher.ISOLATED_DATA_PREFIX}pending-failure"
            isolated_path.mkdir()
            location = launcher.IsolatedDataDirectory(isolated_path, root)
            app = FakeAppProcess()
            identity = launcher.ProcessIdentity(pid=app.pid, create_time=1000.0)
            file_locks = mock.Mock()
            file_locks.poll_change_errors.return_value = []
            file_locks.close.return_value = []

            def capture(**_kwargs) -> Path:
                pending = root / ".incomplete-cleanup-failure.png"
                pending.write_bytes(b"png")
                return pending

            with (
                mock.patch.object(
                    launcher,
                    "acquire_candidate_tree_locks",
                    return_value=file_locks,
                ),
                mock.patch.object(
                    launcher,
                    "create_isolated_data_directory",
                    return_value=location,
                ),
                mock.patch.object(launcher.subprocess, "Popen", return_value=app),
                mock.patch.object(
                    launcher,
                    "capture_launched_app_identity",
                    return_value=identity,
                ),
                mock.patch.object(
                    launcher,
                    "capture_launched_window",
                    side_effect=capture,
                ),
                mock.patch.object(
                    launcher,
                    "cleanup_launched_processes",
                    return_value=["synthetic cleanup fault"],
                ),
                mock.patch.object(
                    launcher,
                    "cleanup_isolated_data_directory",
                ) as cleanup_data,
            ):
                with self.assertRaisesRegex(
                    launcher.LaunchSafetyError,
                    "synthetic cleanup fault",
                ):
                    launcher.launch_and_capture(
                        executable=executable,
                        output_path=output,
                        expected_git_commit=self.COMMIT,
                        expected_app_sha256=app_hash,
                        expected_tree_sha256=tree_hash,
                        wait_seconds=0,
                        padding=0,
                        cdp_port=None,
                        seed_review_fixture=False,
                    )

            cleanup_data.assert_not_called()
            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".incomplete-*.png")), [])
            self.assertTrue(isolated_path.exists())

    def test_interrupt_still_cleans_only_the_launched_process_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            executable, sidecar, app_hash, tree_hash = self._write_candidate(root)
            output = root / "capture.png"
            isolated_data = root / f"{launcher.ISOLATED_DATA_PREFIX}fixture"
            isolated_data.mkdir()
            isolated_location = launcher.IsolatedDataDirectory(
                path=isolated_data,
                temp_root=root,
            )

            app = FakeAppProcess()
            identity = launcher.ProcessIdentity(pid=app.pid, create_time=1000.0)
            exact_child = FakeSidecarProcess(101, app.pid, sidecar)
            unrelated_parent = FakeSidecarProcess(102, 999, sidecar)
            unrelated_path = FakeSidecarProcess(103, app.pid, root / "other.exe")
            processes = [exact_child, unrelated_parent, unrelated_path]

            with (
                mock.patch.object(
                    launcher,
                    "create_isolated_data_directory",
                    return_value=isolated_location,
                ),
                mock.patch("subprocess.Popen", return_value=app) as popen,
                mock.patch.object(
                    launcher,
                    "capture_launched_app_identity",
                    return_value=identity,
                ),
                mock.patch.object(
                    launcher,
                    "launched_app_identity_is_current",
                    return_value=True,
                ),
                mock.patch.object(
                    launcher,
                    "capture_launched_window",
                    side_effect=KeyboardInterrupt(),
                ),
                mock.patch.object(
                    launcher.psutil,
                    "process_iter",
                    side_effect=lambda: iter(processes),
                ),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    launcher.launch_and_capture(
                        executable=executable,
                        output_path=output,
                        expected_git_commit=self.COMMIT,
                        expected_app_sha256=app_hash,
                        expected_tree_sha256=tree_hash,
                        wait_seconds=0,
                        padding=0,
                    )

            self.assertTrue(app.terminated)
            self.assertTrue(exact_child.terminated)
            self.assertFalse(unrelated_parent.terminated)
            self.assertFalse(unrelated_path.terminated)
            self.assertFalse(isolated_data.exists())
            child_env = popen.call_args.kwargs["env"]
            for variable_name in (
                "PRODUCT_ATELIER_DATA_DIR",
                "PRODUCT_ATELIER_LEGACY_CONFIG",
                "PRODUCT_ATELIER_KNOWLEDGE_BASE",
                "PRODUCT_ATELIER_WEBVIEW_DATA_DIR",
                "WEBVIEW2_USER_DATA_FOLDER",
            ):
                self.assertTrue(Path(child_env[variable_name]).is_relative_to(isolated_data))

    def test_interrupt_with_cleanup_failure_is_explicit_and_preserves_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            executable, _, app_hash, tree_hash = self._write_candidate(root)
            output = root / "capture.png"
            isolated_data = root / f"{launcher.ISOLATED_DATA_PREFIX}preserved"
            isolated_data.mkdir()
            location = launcher.IsolatedDataDirectory(isolated_data, root)
            app = FakeAppProcess()
            identity = launcher.ProcessIdentity(pid=app.pid, create_time=1000.0)
            with (
                mock.patch.object(
                    launcher,
                    "create_isolated_data_directory",
                    return_value=location,
                ),
                mock.patch("subprocess.Popen", return_value=app),
                mock.patch.object(
                    launcher,
                    "capture_launched_app_identity",
                    return_value=identity,
                ),
                mock.patch.object(
                    launcher,
                    "capture_launched_window",
                    side_effect=KeyboardInterrupt(),
                ),
                mock.patch.object(
                    launcher,
                    "cleanup_launched_processes",
                    return_value=["synthetic cleanup fault"],
                ),
            ):
                with self.assertRaisesRegex(
                    launcher.LaunchSafetyError,
                    r"KeyboardInterrupt.*synthetic cleanup fault.*preserved",
                ):
                    launcher.launch_and_capture(
                        executable=executable,
                        output_path=output,
                        expected_git_commit=self.COMMIT,
                        expected_app_sha256=app_hash,
                        expected_tree_sha256=tree_hash,
                        wait_seconds=0,
                        padding=0,
                    )
            self.assertTrue(isolated_data.exists())


if __name__ == "__main__":
    unittest.main()
