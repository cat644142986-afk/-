# ruff: noqa: I001

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import verify_formal_webview as verifier


GIT_COMMIT = "a" * 40
APP_SHA256 = "B" * 64
TREE_SHA256 = "C" * 64


class FakeProcess:
    def __init__(
        self,
        pid: int,
        executable: Path,
        *,
        create_time: float,
        parent_pid: int = 0,
        name: str | None = None,
        command_line: list[str] | None = None,
        environment: dict[str, str] | None = None,
        running: bool = True,
    ) -> None:
        self.pid = pid
        self._executable = executable
        self._create_time = create_time
        self._parent_pid = parent_pid
        self._name = name or executable.name
        self._command_line = command_line or [str(executable)]
        self._environment = environment or {}
        self._running = running

    def create_time(self) -> float:
        return self._create_time

    def exe(self) -> str:
        return str(self._executable)

    def is_running(self) -> bool:
        return self._running

    def environ(self) -> dict[str, str]:
        return dict(self._environment)

    def name(self) -> str:
        return self._name

    def cmdline(self) -> list[str]:
        return list(self._command_line)

    def ppid(self) -> int:
        return self._parent_pid


class FormalWebViewVerifierTests(unittest.TestCase):
    def make_layout(self, root: Path) -> tuple[Path, Path, Path, Path]:
        project = root / "project"
        candidate_root = project / verifier.CANDIDATE_RELATIVE_ROOT
        candidate_root.mkdir(parents=True)
        executable = candidate_root / verifier.APP_NAME
        executable.write_bytes(b"candidate-app")
        (project / "release" / "ProductAtelier-Portable").mkdir(parents=True)

        isolation_root = Path(tempfile.mkdtemp(prefix=verifier.ISOLATED_DATA_PREFIX)).resolve()
        self.addCleanup(shutil.rmtree, isolation_root, True)
        (isolation_root / verifier.WEBVIEW_DATA_DIRECTORY_NAME).mkdir()
        (isolation_root / verifier.KNOWLEDGE_DIRECTORY_NAME).mkdir()
        evidence_parent = root / "evidence"
        evidence_parent.mkdir()
        return project, executable, isolation_root, evidence_parent

    def bind_candidate(self, project: Path, executable: Path) -> verifier.CandidateBinding:
        candidate_info = {
            "inventory": {
                "file_count": 1,
                "directory_count": 0,
                "total_bytes": executable.stat().st_size,
                "tree_sha256": TREE_SHA256,
            },
            "artifacts": {
                "app_sha256": APP_SHA256,
                "git_commit": GIT_COMMIT,
            },
        }
        with patch.object(verifier, "validate_candidate", return_value=candidate_info):
            return verifier.validate_candidate_binding(
                executable,
                expected_git_commit=GIT_COMMIT,
                expected_app_sha256=APP_SHA256,
                expected_tree_sha256=TREE_SHA256,
                project_root=project,
            )

    def isolated_environment(
        self,
        isolation: verifier.IsolationBinding,
    ) -> dict[str, str]:
        return {
            "PRODUCT_ATELIER_CANDIDATE_ISOLATION": "1",
            "PRODUCT_ATELIER_DATA_DIR": str(isolation.data_root),
            "PRODUCT_ATELIER_LEGACY_CONFIG": str(isolation.legacy_config_path),
            "PRODUCT_ATELIER_KNOWLEDGE_BASE": str(isolation.knowledge_root),
            "PRODUCT_ATELIER_WEBVIEW_DATA_DIR": str(isolation.webview_data_root),
            "WEBVIEW2_USER_DATA_FOLDER": str(isolation.webview_data_root),
        }

    def proof_for_target(self, port: int = 9222) -> verifier.BrowserProof:
        app = verifier.ProcessIdentity(101, 100.0, Path("D:/candidate/Product Atelier.exe"))
        browser = verifier.ProcessIdentity(202, 101.0, Path("D:/runtime/msedgewebview2.exe"))
        return verifier.BrowserProof(
            cdp_port=port,
            identity=browser,
            app_identity=app,
            listener_addresses=(f"127.0.0.1:{port}",),
            ancestry=(app,),
            command_line_proof={"remote_debugging_port": str(port)},
        )

    def test_parse_size_rejects_malformed_and_unsupported_dimensions(self) -> None:
        self.assertEqual(verifier.parse_size("1280x720"), (1280, 720))
        with self.assertRaises(argparse.ArgumentTypeError):
            verifier.parse_size("1280")
        with self.assertRaises(argparse.ArgumentTypeError):
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

    def test_candidate_binding_requires_canonical_app_and_explicit_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, executable, _data_root, _evidence_parent = self.make_layout(root)
            binding = self.bind_candidate(project, executable)
            self.assertEqual(binding.executable, executable.resolve())
            self.assertEqual(binding.git_commit, GIT_COMMIT)
            self.assertEqual(binding.app_sha256, APP_SHA256)
            self.assertEqual(binding.tree_sha256, TREE_SHA256)

            other = root / "other" / verifier.APP_NAME
            other.parent.mkdir()
            other.write_bytes(b"other")
            with self.assertRaisesRegex(verifier.VerificationError, "canonical path"):
                verifier.validate_candidate_binding(
                    other,
                    expected_git_commit=GIT_COMMIT,
                    expected_app_sha256=APP_SHA256,
                    expected_tree_sha256=TREE_SHA256,
                    project_root=project,
                )

            formal = project / verifier.FORMAL_RELEASE_RELATIVE_ROOT / verifier.APP_NAME
            formal.write_bytes(b"formal")
            with self.assertRaisesRegex(verifier.VerificationError, "formal portable"):
                verifier.validate_candidate_binding(
                    formal,
                    expected_git_commit=GIT_COMMIT,
                    expected_app_sha256=APP_SHA256,
                    expected_tree_sha256=TREE_SHA256,
                    project_root=project,
                )

            candidate_info = {
                "inventory": {"tree_sha256": TREE_SHA256},
                "artifacts": {"app_sha256": APP_SHA256},
            }
            with (
                patch.object(verifier, "validate_candidate", return_value=candidate_info),
                self.assertRaisesRegex(verifier.VerificationError, "App SHA-256"),
            ):
                verifier.validate_candidate_binding(
                    executable,
                    expected_git_commit=GIT_COMMIT,
                    expected_app_sha256="D" * 64,
                    expected_tree_sha256=TREE_SHA256,
                    project_root=project,
                )
            with (
                patch.object(verifier, "validate_candidate", return_value=candidate_info),
                self.assertRaisesRegex(verifier.VerificationError, "tree SHA-256"),
            ):
                verifier.validate_candidate_binding(
                    executable,
                    expected_git_commit=GIT_COMMIT,
                    expected_app_sha256=APP_SHA256,
                    expected_tree_sha256="E" * 64,
                    project_root=project,
                )

    def test_isolation_binding_rejects_real_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, executable, data_root, _evidence_parent = self.make_layout(root)
            candidate = self.bind_candidate(project, executable)
            isolation = verifier.validate_isolation_binding(data_root, candidate)
            snapshot = verifier.credential_isolation_snapshot(isolation)
            self.assertTrue(snapshot["legacy_config_absent"])
            self.assertFalse(snapshot["config_exists"])

            (data_root / "config.json").write_text(
                json.dumps({"api_key": "fixture-not-a-real-key"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(verifier.VerificationError, "credential fields"):
                verifier.validate_isolation_binding(data_root, candidate)

            real_appdata = root / "AppData" / "Roaming"
            nested_real_data = real_appdata / "ProductAtelier" / "verification"
            (nested_real_data / verifier.WEBVIEW_DATA_DIRECTORY_NAME).mkdir(parents=True)
            (nested_real_data / verifier.KNOWLEDGE_DIRECTORY_NAME).mkdir()
            with (
                patch.dict(verifier.os.environ, {"APPDATA": str(real_appdata)}),
                self.assertRaisesRegex(verifier.VerificationError, "real Product Atelier APPDATA"),
            ):
                verifier.validate_isolation_binding(nested_real_data, candidate)

    def test_isolation_requires_launcher_temp_child_prefix_and_binds_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, executable, data_root, _evidence_parent = self.make_layout(root)
            candidate = self.bind_candidate(project, executable)
            binding = verifier.validate_isolation_binding(data_root, candidate)
            self.assertEqual(binding.data_root.parent, binding.temp_root)
            self.assertTrue(binding.data_root.name.startswith(verifier.ISOLATED_DATA_PREFIX))
            self.assertGreater(binding.data_root_identity.st_ino, 0)

            with (
                patch.object(
                    verifier,
                    "_is_link_like",
                    side_effect=lambda path: verifier._same_path(path, data_root),
                ),
                self.assertRaisesRegex(verifier.VerificationError, "reparse point"),
            ):
                verifier.validate_isolation_binding(data_root, candidate)

            wrong_prefix = Path(tempfile.mkdtemp(prefix="not-product-atelier-"))
            self.addCleanup(shutil.rmtree, wrong_prefix, True)
            (wrong_prefix / verifier.WEBVIEW_DATA_DIRECTORY_NAME).mkdir()
            (wrong_prefix / verifier.KNOWLEDGE_DIRECTORY_NAME).mkdir()
            with self.assertRaisesRegex(verifier.VerificationError, "launcher-created direct child"):
                verifier.validate_isolation_binding(wrong_prefix, candidate)

            nested = data_root / f"{verifier.ISOLATED_DATA_PREFIX}nested"
            (nested / verifier.WEBVIEW_DATA_DIRECTORY_NAME).mkdir(parents=True)
            (nested / verifier.KNOWLEDGE_DIRECTORY_NAME).mkdir()
            with self.assertRaisesRegex(verifier.VerificationError, "launcher-created direct child"):
                verifier.validate_isolation_binding(nested, candidate)

    def test_isolation_rejects_bidirectional_protected_overlap_and_identity_swap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, executable, data_root, _evidence_parent = self.make_layout(root)
            candidate = self.bind_candidate(project, executable)
            binding = verifier.validate_isolation_binding(data_root, candidate)

            for overlapping in (
                replace(candidate, candidate_root=data_root / "candidate"),
                replace(candidate, candidate_root=data_root.parent),
            ):
                with (
                    self.subTest(candidate_root=overlapping.candidate_root),
                    self.assertRaisesRegex(verifier.VerificationError, "either direction"),
                ):
                    verifier.validate_isolation_binding(data_root, overlapping)

            formal_inside_isolation = replace(candidate, project_root=data_root)
            with self.assertRaisesRegex(verifier.VerificationError, "formal release"):
                verifier.validate_isolation_binding(data_root, formal_inside_isolation)

            with (
                patch.dict(verifier.os.environ, {"APPDATA": str(data_root)}),
                self.assertRaisesRegex(verifier.VerificationError, "real Product Atelier APPDATA"),
            ):
                verifier.validate_isolation_binding(data_root, candidate)

            original = data_root.with_name(f"{data_root.name}-original")
            os.rename(data_root, original)
            self.addCleanup(shutil.rmtree, original, True)
            (data_root / verifier.WEBVIEW_DATA_DIRECTORY_NAME).mkdir(parents=True)
            (data_root / verifier.KNOWLEDGE_DIRECTORY_NAME).mkdir()
            with self.assertRaisesRegex(verifier.VerificationError, "identity changed"):
                verifier.assert_isolation_binding(binding, candidate)

    def test_app_identity_binds_pid_create_time_executable_and_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, executable, data_root, _evidence_parent = self.make_layout(root)
            candidate = self.bind_candidate(project, executable)
            isolation = verifier.validate_isolation_binding(data_root, candidate)
            process = FakeProcess(
                101,
                executable,
                create_time=1234.5,
                environment=self.isolated_environment(isolation),
            )
            identity = verifier.validate_app_process_identity(
                101,
                1234.5,
                candidate,
                isolation,
                process_factory=lambda _pid: process,
            )
            self.assertEqual(identity.pid, 101)
            self.assertEqual(identity.executable, executable.resolve())

            with self.assertRaisesRegex(verifier.VerificationError, "reused"):
                verifier.validate_app_process_identity(
                    101,
                    1200.0,
                    candidate,
                    isolation,
                    process_factory=lambda _pid: process,
                )

            unsafe_process = FakeProcess(
                101,
                executable,
                create_time=1234.5,
                environment={},
            )
            with self.assertRaisesRegex(
                verifier.VerificationError, "candidate isolation mode"
            ):
                verifier.validate_app_process_identity(
                    101,
                    1234.5,
                    candidate,
                    isolation,
                    process_factory=lambda _pid: unsafe_process,
                )

            incomplete_process = FakeProcess(
                101,
                executable,
                create_time=1234.5,
                environment={"PRODUCT_ATELIER_CANDIDATE_ISOLATION": "1"},
            )
            with self.assertRaisesRegex(verifier.VerificationError, "isolated environment"):
                verifier.validate_app_process_identity(
                    101,
                    1234.5,
                    candidate,
                    isolation,
                    process_factory=lambda _pid: incomplete_process,
                )

    def test_cdp_listener_has_webview_command_line_and_app_ancestry_proof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, executable, data_root, _evidence_parent = self.make_layout(root)
            candidate = self.bind_candidate(project, executable)
            isolation = verifier.validate_isolation_binding(data_root, candidate)
            app = FakeProcess(
                101,
                executable,
                create_time=100.0,
                environment=self.isolated_environment(isolation),
            )
            app_identity = verifier.validate_app_process_identity(
                101,
                100.0,
                candidate,
                isolation,
                process_factory=lambda _pid: app,
            )
            browser_exe = root / "runtime" / "msedgewebview2.exe"
            browser_exe.parent.mkdir()
            browser_exe.write_bytes(b"webview")
            browser = FakeProcess(
                202,
                browser_exe,
                create_time=101.0,
                parent_pid=101,
                name="msedgewebview2.exe",
                command_line=[
                    str(browser_exe),
                    "--remote-debugging-port=9222",
                    f"--user-data-dir={isolation.webview_data_root}",
                    f"--webview-exe-name={candidate.executable.name}",
                ],
            )
            processes = {101: app, 202: browser}
            connection = SimpleNamespace(
                laddr=("127.0.0.1", 9222),
                status="LISTEN",
                pid=202,
            )
            proof = verifier.prove_cdp_browser_process(
                9222,
                app_identity,
                candidate,
                isolation,
                connections=[connection],
                process_factory=processes.__getitem__,
            )
            self.assertEqual(proof.identity.pid, 202)
            self.assertEqual(proof.ancestry[-1].pid, 101)
            self.assertEqual(proof.listener_addresses, ("127.0.0.1:9222",))
            self.assertEqual(
                proof.command_line_proof["user_data_dir"],
                str(isolation.webview_data_root),
            )
            browser_without_optional_name = FakeProcess(
                202,
                browser_exe,
                create_time=101.0,
                parent_pid=101,
                name="msedgewebview2.exe",
                command_line=browser.cmdline()[:-1],
            )
            optional_name_proof = verifier.prove_cdp_browser_process(
                9222,
                app_identity,
                candidate,
                isolation,
                connections=[connection],
                process_factory={101: app, 202: browser_without_optional_name}.__getitem__,
            )
            self.assertEqual(optional_name_proof.command_line_proof["webview_exe_name"], "")
            self.assertEqual(optional_name_proof.command_line_proof["ancestry_app_pid"], "101")

            ambiguous_browser = FakeProcess(
                202,
                browser_exe,
                create_time=101.0,
                parent_pid=101,
                name="msedgewebview2.exe",
                command_line=[*browser.cmdline(), "--remote-debugging-port=9222"],
            )
            with self.assertRaisesRegex(verifier.VerificationError, "unambiguously"):
                verifier.prove_cdp_browser_process(
                    9222,
                    app_identity,
                    candidate,
                    isolation,
                    connections=[connection],
                    process_factory={101: app, 202: ambiguous_browser}.__getitem__,
                )

            for exposed in (
                SimpleNamespace(laddr=("0.0.0.0", 9222), status="LISTEN", pid=None),
                SimpleNamespace(laddr=("localhost", 9222), status="LISTEN", pid=202),
                SimpleNamespace(laddr=("127.0.0.2", 9222), status="LISTEN", pid=202),
            ):
                with (
                    self.subTest(address=exposed.laddr[0]),
                    self.assertRaisesRegex(verifier.VerificationError, "beyond loopback"),
                ):
                    verifier.prove_cdp_browser_process(
                        9222,
                        app_identity,
                        candidate,
                        isolation,
                        connections=[exposed],
                        process_factory=processes.__getitem__,
                    )

            unrelated_exe = root / "unrelated.exe"
            unrelated_exe.write_bytes(b"unrelated")
            unrelated = FakeProcess(303, unrelated_exe, create_time=90.0)
            detached_browser = FakeProcess(
                202,
                browser_exe,
                create_time=101.0,
                parent_pid=303,
                name="msedgewebview2.exe",
                command_line=browser.cmdline(),
            )
            detached_processes = {202: detached_browser, 303: unrelated}
            with self.assertRaisesRegex(verifier.VerificationError, "ancestry"):
                verifier.prove_cdp_browser_process(
                    9222,
                    app_identity,
                    candidate,
                    isolation,
                    connections=[connection],
                    process_factory=detached_processes.__getitem__,
                )

    def test_cdp_browser_rejects_wrong_webview_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, executable, data_root, _evidence_parent = self.make_layout(root)
            candidate = self.bind_candidate(project, executable)
            isolation = verifier.validate_isolation_binding(data_root, candidate)
            browser_exe = root / "runtime" / "msedgewebview2.exe"
            browser_exe.parent.mkdir()
            browser_exe.write_bytes(b"webview")
            app_identity = verifier.ProcessIdentity(101, 100.0, executable.resolve())
            browser = FakeProcess(
                202,
                browser_exe,
                create_time=101.0,
                parent_pid=101,
                name="msedgewebview2.exe",
                command_line=[
                    str(browser_exe),
                    "--remote-debugging-port=9222",
                    f"--user-data-dir={root / 'wrong-profile'}",
                    f"--webview-exe-name={candidate.executable.name}",
                ],
            )
            app = FakeProcess(101, executable, create_time=100.0)
            connection = SimpleNamespace(laddr=("127.0.0.1", 9222), status="LISTEN", pid=202)
            with self.assertRaisesRegex(verifier.VerificationError, "isolated user-data"):
                verifier.prove_cdp_browser_process(
                    9222,
                    app_identity,
                    candidate,
                    isolation,
                    connections=[connection],
                    process_factory={101: app, 202: browser}.__getitem__,
                )

    def test_cdp_target_is_bound_to_tauri_origin_and_listener(self) -> None:
        target = {
            "id": "abc",
            "type": "page",
            "url": "http://tauri.localhost/",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/abc",
        }
        proof = self.proof_for_target()
        self.assertIs(verifier.validate_cdp_target(target, 9222, proof), target)
        with self.assertRaisesRegex(verifier.VerificationError, "expected listener"):
            verifier.validate_cdp_target(
                {**target, "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/page/abc"},
                9222,
                proof,
            )
        with self.assertRaisesRegex(verifier.VerificationError, "Tauri origin"):
            verifier.validate_cdp_target(
                {**target, "url": "http://example.invalid/"},
                9222,
                proof,
            )
        for websocket_url in (
            "ws://localhost:9222/devtools/page/abc",
            "ws://127.0.0.2:9222/devtools/page/abc",
            "ws://user@127.0.0.1:9222/devtools/page/abc",
        ):
            with (
                self.subTest(websocket_url=websocket_url),
                self.assertRaisesRegex(verifier.VerificationError, "loopback-only"),
            ):
                verifier.validate_cdp_target(
                    {**target, "webSocketDebuggerUrl": websocket_url},
                    9222,
                    proof,
                )
        with self.assertRaisesRegex(verifier.VerificationError, "proven listener PID"):
            verifier.validate_cdp_target(
                target,
                9222,
                replace(proof, listener_addresses=("::1:9222",)),
            )

    def test_output_directory_rejects_candidate_release_and_isolated_trees(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, executable, data_root, evidence_parent = self.make_layout(root)
            candidate = self.bind_candidate(project, executable)
            isolation = verifier.validate_isolation_binding(data_root, candidate)

            output = verifier.prepare_evidence_staging(
                evidence_parent / "formal-webview-run",
                candidate,
                isolation,
            )
            self.assertTrue(output.staging_dir.is_dir())
            self.assertIn(".formal-webview-run.incomplete-", output.staging_dir.name)
            self.assertFalse(output.final_output_dir.exists())
            verifier.write_new_file(output.staging_dir / "one.txt", b"one")
            with self.assertRaisesRegex(verifier.VerificationError, "overwrite"):
                verifier.write_new_file(output.staging_dir / "one.txt", b"two")

            for unsafe in (
                candidate.candidate_root / "evidence",
                candidate.project_root / verifier.FORMAL_RELEASE_RELATIVE_ROOT / "evidence",
                isolation.data_root / "evidence",
            ):
                with (
                    self.subTest(unsafe=unsafe),
                    self.assertRaisesRegex(verifier.VerificationError, "outside candidate"),
                ):
                    verifier.prepare_evidence_staging(unsafe, candidate, isolation)

    def test_output_parent_and_staging_identity_are_revalidated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, executable, data_root, evidence_parent = self.make_layout(root)
            candidate = self.bind_candidate(project, executable)
            isolation = verifier.validate_isolation_binding(data_root, candidate)

            with (
                patch.object(
                    verifier,
                    "_is_link_like",
                    side_effect=lambda path: verifier._same_path(path, evidence_parent),
                ),
                self.assertRaisesRegex(verifier.VerificationError, "reparse point"),
            ):
                verifier.prepare_evidence_staging(
                    evidence_parent / "reparse-refused",
                    candidate,
                    isolation,
                )

            binding = verifier.prepare_evidence_staging(
                evidence_parent / "identity-run",
                candidate,
                isolation,
            )
            original = evidence_parent / "staging-original"
            os.rename(binding.staging_dir, original)
            binding.staging_dir.mkdir()
            with self.assertRaisesRegex(verifier.VerificationError, "identity changed"):
                verifier.assert_evidence_staging(binding, candidate, isolation)

            second_parent = root / "evidence-parent-two"
            second_parent.mkdir()
            parent_binding = verifier.prepare_evidence_staging(
                second_parent / "parent-identity-run",
                candidate,
                isolation,
            )
            original_parent = root / "evidence-parent-two-original"
            os.rename(second_parent, original_parent)
            second_parent.mkdir()
            with self.assertRaisesRegex(verifier.VerificationError, "parent identity changed"):
                verifier.assert_evidence_staging(parent_binding, candidate, isolation)

    def test_format_v3_receipt_hashes_pngs_and_requires_launcher_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, executable, data_root, evidence_parent = self.make_layout(root)
            candidate = self.bind_candidate(project, executable)
            isolation = verifier.validate_isolation_binding(data_root, candidate)
            binding = verifier.prepare_evidence_staging(
                evidence_parent / "receipt-run",
                candidate,
                isolation,
            )
            png = b"\x89PNG\r\n\x1a\nfixture"
            png_name = "dpi-96-960x600-light-single.png"
            verifier.write_new_file(binding.staging_dir / png_name, png)
            png_sha256 = hashlib.sha256(png).hexdigest().upper()
            report = {
                "format_version": 3,
                "passed": True,
                "candidate": verifier._candidate_payload(candidate),
                "app_process": {"pid": 101, "create_time": 100.0},
                "isolation": verifier._isolation_payload(isolation),
                "browser_proof": {"cdp_port": 9222},
                "cases": [{
                    "index": 1,
                    "passed": True,
                    "screenshot": {
                        "relative_path": png_name,
                        "bytes": len(png),
                        "sha256": png_sha256,
                    },
                }],
                "memory": {},
                "console_failures": [],
                "final_identity": {
                    "candidate_unchanged": True,
                    "isolation_unchanged": True,
                },
            }
            tampered_report = {
                **report,
                "cases": [{
                    **report["cases"][0],
                    "screenshot": {
                        **report["cases"][0]["screenshot"],
                        "sha256": "D" * 64,
                    },
                }],
            }
            with self.assertRaisesRegex(verifier.VerificationError, "identity changed"):
                verifier.stage_verification_receipt(
                    binding,
                    tampered_report,
                    candidate,
                    isolation,
                )
            self.assertFalse((binding.staging_dir / verifier.RECEIPT_NAME).exists())
            result = verifier.stage_verification_receipt(
                binding,
                report,
                candidate,
                isolation,
            )
            self.assertEqual(result["status"], "staged")
            self.assertFalse(binding.final_output_dir.exists())
            receipt_path = Path(result["receipt_path"])
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["format_version"], 3)
            self.assertTrue(receipt["passed"])
            self.assertEqual(receipt["app_process"]["pid"], 101)
            self.assertIn("candidate_root_identity", receipt["candidate"])
            self.assertIn("data_root", receipt["isolation"]["identities"])
            self.assertEqual(
                receipt["evidence"]["output_parent_identity"],
                verifier._filesystem_identity_payload(binding.output_parent_identity),
            )
            self.assertEqual(receipt["publication"]["state"], "staged")
            self.assertTrue(receipt["publication"]["requires_launcher_finalize"])
            self.assertEqual(receipt["evidence"]["screenshots"], [{
                "relative_path": png_name,
                "bytes": len(png),
                "size_bytes": len(png),
                "sha256": png_sha256,
            }])
            self.assertEqual(
                result["receipt_sha256"],
                hashlib.sha256(receipt_path.read_bytes()).hexdigest().upper(),
            )

    def test_failed_staging_is_quarantined_without_final_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, executable, data_root, evidence_parent = self.make_layout(root)
            candidate = self.bind_candidate(project, executable)
            isolation = verifier.validate_isolation_binding(data_root, candidate)
            binding = verifier.prepare_evidence_staging(
                evidence_parent / "failed-run",
                candidate,
                isolation,
            )
            failed = verifier.mark_evidence_failed(
                binding,
                verifier.VerificationError("fixture failure"),
            )
            self.assertIn(".failed-run.failed-", failed.name)
            marker_path = failed / verifier.FAILURE_MARKER_NAME
            self.assertTrue(marker_path.is_file())
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(marker["status"], "failed")
            self.assertNotIn("fixture failure", marker["error"])
            self.assertFalse(binding.staging_dir.exists())
            self.assertFalse(binding.final_output_dir.exists())

    def test_parser_requires_candidate_process_and_isolation_identity(self) -> None:
        args = verifier.build_parser().parse_args([
            "--exe", "D:/candidate/Product Atelier.exe",
            "--expected-git-commit", GIT_COMMIT,
            "--expected-app-sha256", APP_SHA256,
            "--expected-tree-sha256", TREE_SHA256,
            "--pid", "101",
            "--expected-create-time", "1234.5",
            "--cdp-port", "9222",
            "--isolated-data-dir", "D:/isolated",
            "--monitor-index", "0",
            "--expected-dpi", "96",
            "--output-dir", "D:/evidence/run",
        ])
        self.assertEqual(args.expected_create_time, 1234.5)
        self.assertEqual(args.exe.name, verifier.APP_NAME)

    def test_infinite_canvas_library_surface_covers_all_default_window_sizes(self) -> None:
        self.assertIn("infinite-canvas-library", verifier.DEFAULT_SURFACES)
        cases = verifier.matrix_cases(
            verifier.DEFAULT_SIZES,
            ("light",),
            ("infinite-canvas-library",),
        )
        self.assertEqual(
            [size for size, _profile, _surface in cases],
            list(verifier.DEFAULT_SIZES),
        )

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
        self.assertIn("#btn-send-result-canvas", selectors)
        self.assertIn("#btn-spatial-new", selectors)
        self.assertIn("#btn-spatial-empty-new", selectors)
        self.assertIn("#btn-spatial-rename", selectors)
        self.assertIn("#spatial-rename-form", selectors)
        self.assertIn("[data-remove-asset-id]", selectors)
        self.assertIn("[data-memory-action]", selectors)
        self.assertIn("[data-job-action='send-canvas']", selectors)
        self.assertIn("[data-job-action='open-video-canvas']", selectors)
        self.assertIn("[data-job-action='retry-failed']", selectors)
        self.assertIn("[data-purge-asset]", selectors)
        self.assertIn("[data-spatial-open]", selectors)
        self.assertIn("[data-spatial-rename]", selectors)
        self.assertIn("[data-spatial-action]", selectors)
        self.assertIn("[data-spatial-video-form]", selectors)
        self.assertEqual(verifier.READ_ONLY_GUARD_EVENT_TYPES, ("click", "submit"))

    def test_read_only_guard_blocks_drop_and_form_submission(self) -> None:
        class RecordingClient:
            def __init__(self) -> None:
                self.expression = ""

            def evaluate(self, expression: str) -> dict[str, object]:
                self.expression = expression
                return {"installed": True, "eventTypes": ["click", "submit", "drop"]}

        client = RecordingClient()
        result = verifier.install_read_only_guard(client)
        self.assertEqual(result["eventTypes"], ["click", "submit", "drop"])
        self.assertIn("eventTypes.forEach", client.expression)
        self.assertIn("document.addEventListener('drop', guardDrop, true)", client.expression)

    def test_persistence_guard_blocks_storage_writes_and_restores_snapshot(self) -> None:
        class RecordingClient:
            def __init__(self) -> None:
                self.expressions: list[str] = []

            def evaluate(self, expression: str) -> dict[str, object]:
                self.expressions.append(expression)
                if "guard.restore()" in expression:
                    return {
                        "installed": True,
                        "restored": True,
                        "storageMatchesSnapshot": True,
                        "blockedOperationCount": 2,
                    }
                return {
                    "installed": True,
                    "blockedOperationCount": 0,
                    "blockedOperations": [],
                }

        client = RecordingClient()
        installed = verifier.install_persistence_guard(client)
        restored = verifier.restore_persistence_guard(client)
        self.assertTrue(installed["installed"])
        self.assertTrue(restored["restored"])
        source = client.expressions[0]
        self.assertIn("Storage.prototype.setItem = function", source)
        self.assertIn("Storage.prototype.removeItem = function", source)
        self.assertIn("Storage.prototype.clear = function", source)
        self.assertIn("restoreStorage(local, initial.local)", source)
        self.assertIn("storageMatchesSnapshot", source)

    def test_appearance_profiles_are_transient_dom_state(self) -> None:
        class RecordingClient:
            def __init__(self) -> None:
                self.expression = ""

            def evaluate(self, expression: str) -> dict[str, object]:
                self.expression = expression
                return {"ok": True, "transient": True, "theme": "dark", "contrast": "standard"}

        client = RecordingClient()
        with (
            patch.object(verifier, "dismiss_layers"),
            patch.object(verifier, "click"),
            patch.object(verifier, "wait_for"),
        ):
            result = verifier.apply_profile(client, "dark")
        self.assertTrue(result["transient"])
        self.assertIn("root.dataset.theme = 'dark'", client.expression)
        self.assertIn("root.dataset.contrast = 'standard'", client.expression)
        self.assertNotIn("themeInput.click", client.expression)
        self.assertNotIn("contrastInput.click", client.expression)
        self.assertNotIn("localStorage", client.expression)

    def test_infinite_canvas_library_surface_stops_before_editor_load(self) -> None:
        client = object()
        stable_state = {
            "entry": "\u65e0\u9650\u753b\u5e03",
            "pageVisible": True,
            "libraryState": "empty",
            "canvasCount": 0,
            "cardCount": 0,
            "status": "0 \u4e2a\u753b\u5e03 \u00b7 \u5df2\u540c\u6b65",
            "editorHidden": True,
            "runtimeState": "not-loaded",
        }
        with (
            patch.object(verifier, "dismiss_layers") as dismiss,
            patch.object(verifier, "click", return_value={"ok": True}) as click,
            patch.object(verifier, "wait_for_stable", return_value=stable_state) as stable,
        ):
            result = verifier.open_surface(client, "infinite-canvas-library")

        dismiss.assert_called_once_with(client)
        click.assert_called_once_with(client, "[data-page='canvas']")
        stable.assert_called_once_with(client, verifier.INFINITE_CANVAS_READY_EXPRESSION)
        self.assertEqual(result["surface"], "infinite-canvas-library")
        self.assertEqual(result["libraryState"], "empty")
        self.assertEqual(result["runtimeState"], "not-loaded")
        expression = verifier.INFINITE_CANVAS_READY_EXPRESSION
        self.assertIn("#page-canvas", expression)
        self.assertIn("#spatial-library", expression)
        self.assertIn("#spatial-editor", expression)
        self.assertIn("const emptyReady", expression)
        self.assertIn("const listReady", expression)
        self.assertIn("cardCount === count", expression)
        self.assertIn("statusText.endsWith", expression)
        self.assertIn("dataset.spatialRuntime !== 'loaded'", expression)

    def test_non_windows_runtime_fails_only_when_windows_features_are_requested(self) -> None:
        with (
            patch.object(verifier.os, "name", "posix"),
            self.assertRaisesRegex(verifier.VerificationError, "Windows only"),
        ):
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
