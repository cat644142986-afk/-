from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import launch_and_shoot as launcher  # noqa: E402
import manual_restart_acceptance as manual  # noqa: E402


class FakeManualSession:
    latest: "FakeManualSession | None" = None

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.calls: list[str] = []
        self.data_dir = Path(tempfile.mkdtemp(prefix="manual-acceptance-test-"))
        self.process = SimpleNamespace(poll=lambda: None)
        self._pid = 41
        self.restart_count = 0
        self.armed = False
        self.exited = False
        type(self).latest = self

    @property
    def pid(self) -> int:
        return self._pid

    def __enter__(self) -> "FakeManualSession":
        self.calls.append("enter")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        del exc_type, exc_value, traceback
        self.calls.append("exit")
        self.exited = True
        self.data_dir.rmdir()
        return False

    def arm_graceful_close(self) -> launcher.GracefulCloseBinding:
        self.calls.append(f"arm-{self._pid}")
        self.armed = True
        return launcher.GracefulCloseBinding(
            app_identity=launcher.ProcessIdentity(
                pid=self._pid,
                create_time=float(self._pid),
            ),
            sidecars=(),
            webviews=(
                launcher.ProcessIdentity(
                    pid=self._pid + 100,
                    create_time=float(self._pid + 100),
                ),
            ),
            armed_at=2000.0 + self._pid,
        )

    def restart_with_same_data(self) -> "FakeManualSession":
        if not self.armed:
            raise launcher.LaunchSafetyError("not armed")
        self.calls.append("restart")
        self.armed = False
        self._pid = 42
        self.restart_count = 1
        return self

    def complete_graceful_close(self) -> int:
        if not self.armed:
            raise launcher.LaunchSafetyError("not armed")
        self.calls.append("complete")
        self.armed = False
        return 0


class InterruptingInput:
    def __iter__(self):
        raise KeyboardInterrupt


class ManualRestartAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeManualSession.latest = None
        self.spec = manual.CandidateLaunchSpec(
            executable=Path("candidate.exe"),
            git_commit="a" * 40,
            app_sha256="B" * 64,
            tree_sha256="C" * 64,
        )

    def _events(self, output: io.StringIO) -> list[dict[str, object]]:
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_fixed_two_run_protocol_finishes_and_cleans_data(self) -> None:
        output = io.StringIO()

        result = manual.run_manual_restart_acceptance(
            io.StringIO("status\narm\nrestart\narm\nfinish\n"),
            output,
            spec=self.spec,
            session_factory=FakeManualSession,
        )

        self.assertEqual(result, 0)
        session = FakeManualSession.latest
        self.assertIsNotNone(session)
        self.assertEqual(
            session.calls,
            ["enter", "arm-41", "restart", "arm-42", "complete", "exit"],
        )
        self.assertEqual(
            [event["event"] for event in self._events(output)],
            [
                "run_ready",
                "status",
                "armed",
                "run_ready",
                "armed",
                "run_closed",
                "finished",
            ],
        )
        self.assertTrue(self._events(output)[-1]["isolated_data_cleaned"])

    def test_finish_before_second_armed_run_is_rejected_without_passing(self) -> None:
        output = io.StringIO()

        result = manual.run_manual_restart_acceptance(
            io.StringIO("finish\nabort\n"),
            output,
            spec=self.spec,
            session_factory=FakeManualSession,
        )

        self.assertEqual(result, 130)
        events = self._events(output)
        self.assertEqual(events[1]["event"], "command_error")
        self.assertNotIn("finished", [event["event"] for event in events])
        self.assertEqual(events[-1]["event"], "aborted")

    def test_eof_aborts_and_never_reports_finished(self) -> None:
        output = io.StringIO()

        result = manual.run_manual_restart_acceptance(
            io.StringIO(""),
            output,
            spec=self.spec,
            session_factory=FakeManualSession,
        )

        self.assertEqual(result, 130)
        self.assertEqual(
            [event["event"] for event in self._events(output)],
            ["run_ready", "input_closed", "aborted"],
        )

    def test_keyboard_interrupt_still_leaves_the_session_context(self) -> None:
        output = io.StringIO()

        with self.assertRaises(KeyboardInterrupt):
            manual.run_manual_restart_acceptance(
                InterruptingInput(),  # type: ignore[arg-type]
                output,
                spec=self.spec,
                session_factory=FakeManualSession,
            )

        session = FakeManualSession.latest
        self.assertIsNotNone(session)
        self.assertTrue(session.exited)
        self.assertFalse(session.data_dir.exists())

    def test_main_reports_keyboard_interrupt_as_aborted(self) -> None:
        output = io.StringIO()
        with (
            mock.patch.object(
                manual,
                "run_manual_restart_acceptance",
                side_effect=KeyboardInterrupt,
            ),
            mock.patch.object(manual.sys, "stdout", output),
        ):
            result = manual.main([])

        self.assertEqual(result, 130)
        self.assertEqual(self._events(output), [{"event": "interrupted"}])

    def test_cli_rejects_external_data_directory(self) -> None:
        with (
            mock.patch.object(manual.sys, "stderr", io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            manual.main(["--data-dir", "D:\\unsafe"])

    def test_current_candidate_spec_uses_verified_canonical_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            project = Path(temporary_dir).resolve()
            candidate_dir = project / manual.CANONICAL_CANDIDATE_RELATIVE
            candidate_dir.mkdir(parents=True)
            identity_path = (
                project
                / "build"
                / manual.portable_release.CANDIDATE_IDENTITY_FILE_NAME
            )
            identity_path.write_text(
                json.dumps({"git_commit": "a" * 40}),
                encoding="utf-8",
            )
            verified = {
                "candidate": {
                    "artifacts": {"app_sha256": "B" * 64},
                    "inventory": {"tree_sha256": "C" * 64},
                }
            }
            with mock.patch.object(
                manual.portable_release,
                "verify_candidate_identity",
                return_value=verified,
            ) as verify:
                spec = manual.load_current_candidate_spec(project)

        self.assertEqual(
            spec,
            manual.CandidateLaunchSpec(
                executable=candidate_dir / manual.portable_release.APP_NAME,
                git_commit="a" * 40,
                app_sha256="B" * 64,
                tree_sha256="C" * 64,
            ),
        )
        verify.assert_called_once_with(
            project_root=project,
            candidate_dir=candidate_dir,
            expected_git_commit="a" * 40,
        )


if __name__ == "__main__":
    unittest.main()
