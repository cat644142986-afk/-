#!/usr/bin/env python3
"""Run the fixed two-launch candidate restart acceptance protocol."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import portable_release
from launch_and_shoot import (
    CANONICAL_CANDIDATE_RELATIVE,
    PROJECT_ROOT,
    CandidateLaunchSession,
    GracefulCloseBinding,
    LaunchSafetyError,
)


MANUAL_CDP_PORT = 9237
EXPECTED_RUN_COUNT = 2


@dataclass(frozen=True)
class CandidateLaunchSpec:
    executable: Path
    git_commit: str
    app_sha256: str
    tree_sha256: str


def load_current_candidate_spec(
    project_root: Path = PROJECT_ROOT,
) -> CandidateLaunchSpec:
    project = project_root.resolve(strict=True)
    identity_path = (
        project / "build" / portable_release.CANDIDATE_IDENTITY_FILE_NAME
    )
    try:
        payload = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LaunchSafetyError(
            f"Could not read the current candidate identity: {identity_path}: {error}"
        ) from error
    if not isinstance(payload, dict) or not isinstance(payload.get("git_commit"), str):
        raise LaunchSafetyError("Current candidate identity has no Git commit")

    candidate_dir = (project / CANONICAL_CANDIDATE_RELATIVE).resolve(strict=True)
    try:
        verified = portable_release.verify_candidate_identity(
            project_root=project,
            candidate_dir=candidate_dir,
            expected_git_commit=payload["git_commit"],
        )
    except portable_release.ReleaseError as error:
        raise LaunchSafetyError(
            f"Current candidate identity verification failed: {error}"
        ) from error
    candidate = verified["candidate"]
    return CandidateLaunchSpec(
        executable=candidate_dir / portable_release.APP_NAME,
        git_commit=payload["git_commit"],
        app_sha256=candidate["artifacts"]["app_sha256"],
        tree_sha256=candidate["inventory"]["tree_sha256"],
    )


def _emit(stream: TextIO, event: str, **payload: object) -> None:
    print(
        json.dumps({"event": event, **payload}, ensure_ascii=False, sort_keys=True),
        file=stream,
        flush=True,
    )


def _binding_payload(binding: GracefulCloseBinding) -> dict[str, object]:
    return {
        "app": {
            "pid": binding.app_identity.pid,
            "create_time": binding.app_identity.create_time,
        },
        "sidecars": [
            {
                "pid": tracked.identity.pid,
                "create_time": tracked.identity.create_time,
            }
            for tracked in binding.sidecars
        ],
        "webviews": [
            {"pid": identity.pid, "create_time": identity.create_time}
            for identity in binding.webviews
        ],
        "armed_at": binding.armed_at,
    }


def _runtime_payload(
    session: CandidateLaunchSession,
    *,
    run_index: int,
    armed: bool,
) -> dict[str, object]:
    process = session.process
    returncode = process.poll() if process is not None else None
    return {
        "run_index": run_index,
        "expected_run_count": EXPECTED_RUN_COUNT,
        "pid": session.pid,
        "data_dir": str(session.data_dir),
        "restart_count": session.restart_count,
        "armed": armed,
        "app_running": returncode is None,
        "returncode": returncode,
    }


def run_manual_restart_acceptance(
    input_stream: TextIO,
    output_stream: TextIO,
    *,
    spec: CandidateLaunchSpec | None = None,
    session_factory=CandidateLaunchSession,
) -> int:
    launch_spec = spec if spec is not None else load_current_candidate_spec()
    finished = False
    aborted = False
    data_dir: Path | None = None

    with session_factory(
        executable=launch_spec.executable,
        expected_git_commit=launch_spec.git_commit,
        expected_app_sha256=launch_spec.app_sha256,
        expected_tree_sha256=launch_spec.tree_sha256,
        cdp_port=MANUAL_CDP_PORT,
        seed_review_fixture=True,
    ) as session:
        run_index = 1
        armed = False
        data_dir = session.data_dir
        _emit(
            output_stream,
            "run_ready",
            **_runtime_payload(session, run_index=run_index, armed=armed),
        )

        for raw_command in input_stream:
            command = raw_command.strip().casefold()
            if not command:
                continue
            try:
                if command == "status":
                    _emit(
                        output_stream,
                        "status",
                        **_runtime_payload(
                            session,
                            run_index=run_index,
                            armed=armed,
                        ),
                    )
                elif command == "arm":
                    if armed:
                        raise LaunchSafetyError(
                            f"Run {run_index} graceful close is already armed"
                        )
                    binding = session.arm_graceful_close()
                    armed = True
                    _emit(
                        output_stream,
                        "armed",
                        run_index=run_index,
                        binding=_binding_payload(binding),
                    )
                elif command == "restart":
                    if run_index != 1:
                        raise LaunchSafetyError(
                            "Restart is allowed only after the first real UI close"
                        )
                    if not armed:
                        raise LaunchSafetyError(
                            "Arm run 1 before requesting its restart"
                        )
                    session.restart_with_same_data()
                    run_index = 2
                    armed = False
                    _emit(
                        output_stream,
                        "run_ready",
                        **_runtime_payload(
                            session,
                            run_index=run_index,
                            armed=armed,
                        ),
                    )
                elif command == "finish":
                    if run_index != EXPECTED_RUN_COUNT:
                        raise LaunchSafetyError(
                            "Finish is allowed only after both candidate launches"
                        )
                    if not armed:
                        raise LaunchSafetyError(
                            "Arm run 2 before requesting final cleanup"
                        )
                    returncode = session.complete_graceful_close()
                    finished = True
                    _emit(
                        output_stream,
                        "run_closed",
                        run_index=run_index,
                        returncode=returncode,
                    )
                    break
                elif command == "abort":
                    aborted = True
                    _emit(output_stream, "aborting", run_index=run_index)
                    break
                else:
                    raise LaunchSafetyError(
                        "Unknown command; use status, arm, restart, finish, or abort"
                    )
            except LaunchSafetyError as error:
                _emit(
                    output_stream,
                    "command_error",
                    command=command,
                    run_index=run_index,
                    message=str(error),
                )
        else:
            aborted = True
            _emit(output_stream, "input_closed", run_index=run_index)

    cleaned = data_dir is not None and not data_dir.exists()
    if finished:
        _emit(
            output_stream,
            "finished",
            runs_completed=EXPECTED_RUN_COUNT,
            isolated_data_cleaned=cleaned,
        )
        if not cleaned:
            raise LaunchSafetyError(
                "Manual acceptance finished but isolated data was not removed"
            )
        return 0

    _emit(
        output_stream,
        "aborted",
        isolated_data_cleaned=cleaned,
        requested=aborted,
    )
    return 130


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description=(
            "Launch the canonical candidate twice with one isolated ledger. "
            "Commands: status, arm, restart, finish, abort."
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    try:
        return run_manual_restart_acceptance(sys.stdin, sys.stdout)
    except KeyboardInterrupt:
        _emit(sys.stdout, "interrupted")
        return 130
    except Exception as error:
        _emit(sys.stdout, "failed", message=str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
