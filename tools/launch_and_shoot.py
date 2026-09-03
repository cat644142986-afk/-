#!/usr/bin/env python3
"""Launch an explicit portable candidate, capture it, and clean up only its processes."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import psutil

import portable_release
from portable_release import APP_NAME, ReleaseError, directory_inventory, validate_candidate


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORMAL_RELEASE_MARKER = ("release", "productatelier-portable")
CANONICAL_CANDIDATE_RELATIVE = Path("build") / "portable-candidate-current"
SIDECAR_RELATIVE_PATH = Path("python-server") / "python-server.exe"
ISOLATED_DATA_PREFIX = "ProductAtelier-launch-and-shoot-"
WEBVIEW_ARGUMENTS_VARIABLE = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"
WEBVIEW_PROCESS_NAME = "msedgewebview2.exe"
WEBVIEW_USER_DATA_ARGUMENT = "--user-data-dir"
WEBVIEW_RUNTIME_DATA_DIRECTORY_NAME = "EBWebView"
VERIFICATION_RECEIPT_NAME = "verification-receipt.json"
LAUNCHER_FINALIZATION_NAME = "launcher-finalization.json"
CLEANUP_RENAME_RETRY_ATTEMPTS = 51
CLEANUP_RENAME_RETRY_INTERVAL_SECONDS = 0.1
CLEANUP_RETRYABLE_ERRNOS = frozenset({errno.EACCES, errno.EBUSY, errno.EPERM})
CLEANUP_RETRYABLE_WINERRORS = frozenset({5, 32, 33})
WEBVIEW_SHUTDOWN_TIMEOUT_SECONDS = 5.0
WEBVIEW_SHUTDOWN_POLL_INTERVAL_SECONDS = 0.1
NATURAL_APP_EXIT_TIMEOUT_SECONDS = 45.0
NATURAL_APP_EXIT_POLL_INTERVAL_SECONDS = 0.1


class LaunchSafetyError(RuntimeError):
    """Raised when a requested launch could affect a non-candidate installation."""


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    create_time: float


@dataclass(frozen=True)
class TrackedSidecar:
    process: psutil.Process
    identity: ProcessIdentity


@dataclass(frozen=True)
class GracefulCloseBinding:
    app_identity: ProcessIdentity
    sidecars: tuple[TrackedSidecar, ...]
    webviews: tuple[ProcessIdentity, ...]
    armed_at: float


@dataclass(frozen=True)
class IsolatedDataDirectory:
    path: Path
    temp_root: Path


@dataclass(frozen=True)
class VerifiedSidecarIdentity:
    path: Path
    sha256: str
    manifest_sha256: str


@dataclass(frozen=True)
class CandidateSnapshot:
    executable: Path
    candidate_dir: Path
    git_commit: str
    app_sha256: str
    tree_sha256: str
    sidecar: VerifiedSidecarIdentity


@dataclass(frozen=True)
class StagedVerificationReceipt:
    staging_dir: Path
    final_output_dir: Path
    receipt_path: Path
    receipt_sha256: str
    payload: dict[str, Any]
    screenshot_names: tuple[str, ...]


@dataclass
class CandidateFileLocks:
    handles: list[int]
    change_notification_handle: int | None = None
    change_observed: bool = False

    def poll_change_errors(self) -> list[str]:
        if os.name != "nt" or self.change_notification_handle is None:
            return []
        import ctypes
        from ctypes import wintypes

        wait_for_single_object = ctypes.WinDLL(
            "kernel32", use_last_error=True
        ).WaitForSingleObject
        wait_for_single_object.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        wait_for_single_object.restype = wintypes.DWORD
        wait_object_0 = 0x00000000
        wait_timeout = 0x00000102
        wait_failed = 0xFFFFFFFF
        result = wait_for_single_object(self.change_notification_handle, 0)
        if result == wait_object_0:
            self.change_observed = True
        elif result == wait_failed:
            return [
                "could not query candidate tree change notification: "
                f"{ctypes.WinError(ctypes.get_last_error())}"
            ]
        elif result != wait_timeout:
            return [f"unexpected candidate tree notification wait result: {result}"]
        if self.change_observed:
            return ["candidate tree changed while the launch lock was held"]
        return []

    def close(self) -> list[str]:
        if os.name != "nt":
            self.handles.clear()
            return []
        import ctypes
        from ctypes import wintypes

        close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        errors: list[str] = self.poll_change_errors()
        while self.handles:
            handle = self.handles.pop()
            if not close_handle(handle):
                errors.append(str(ctypes.WinError(ctypes.get_last_error())))
        errors.extend(
            error for error in self.poll_change_errors() if error not in errors
        )
        if self.change_notification_handle is not None:
            close_notification = ctypes.WinDLL(
                "kernel32", use_last_error=True
            ).FindCloseChangeNotification
            close_notification.argtypes = [wintypes.HANDLE]
            close_notification.restype = wintypes.BOOL
            if not close_notification(self.change_notification_handle):
                errors.append(str(ctypes.WinError(ctypes.get_last_error())))
            self.change_notification_handle = None
        return errors


def _candidate_tree_paths(root: Path) -> list[tuple[Path, bool]]:
    _require_regular_directory(root, "Candidate root")

    def fail_on_walk_error(error: OSError) -> None:
        raise LaunchSafetyError(
            f"Could not enumerate candidate tree at {error.filename or root}: {error}"
        ) from error

    paths: list[tuple[Path, bool]] = [(root, True)]
    for current_root, directory_names, file_names in os.walk(
        root,
        topdown=True,
        onerror=fail_on_walk_error,
        followlinks=False,
    ):
        directory_names.sort()
        file_names.sort()
        current = Path(current_root)
        for name in directory_names:
            directory = current / name
            _require_regular_directory(directory, "Candidate directory")
            paths.append((directory, True))
        for name in file_names:
            path = current / name
            try:
                metadata = path.lstat()
            except OSError as error:
                raise LaunchSafetyError(
                    f"Candidate file is unavailable: {path}: {error}"
                ) from error
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
            if (
                stat.S_ISLNK(metadata.st_mode)
                or bool(attributes & reparse_flag)
                or not stat.S_ISREG(metadata.st_mode)
            ):
                raise LaunchSafetyError(f"Candidate file must be regular: {path}")
            if os.name == "nt" and metadata.st_nlink != 1:
                raise LaunchSafetyError(f"Candidate file may not be a hard link: {path}")
            paths.append((path, False))
    return paths


def acquire_candidate_file_locks(paths: Iterable[Path]) -> CandidateFileLocks:
    """Deny writes and replacement for explicit regular candidate files."""
    if os.name != "nt":
        return CandidateFileLocks([])

    import ctypes
    from ctypes import wintypes

    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    generic_read = 0x80000000
    file_share_read = 0x00000001
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_flag_open_reparse_point = 0x00200000
    invalid_handle = ctypes.c_void_p(-1).value
    locks = CandidateFileLocks([])
    try:
        for path in paths:
            if _is_link_like(path):
                raise LaunchSafetyError(f"Candidate file may not be a reparse point: {path}")
            handle = create_file(
                str(path),
                generic_read,
                file_share_read,
                None,
                open_existing,
                file_attribute_normal | file_flag_open_reparse_point,
                None,
            )
            if handle == invalid_handle:
                raise LaunchSafetyError(
                    f"Could not lock candidate file {path}: "
                    f"{ctypes.WinError(ctypes.get_last_error())}"
                )
            locks.handles.append(int(handle))
    except BaseException:
        locks.close()
        raise
    return locks


def acquire_candidate_tree_locks(
    candidate_root: Path,
    expected_tree_sha256: str,
) -> CandidateFileLocks:
    """Lock every candidate directory and file, then reverify its inventory."""
    expected_hash = validate_expected_sha256(expected_tree_sha256, "candidate tree")
    if os.name != "nt":
        inventory = directory_inventory(candidate_root)
        if inventory["tree_sha256"] != expected_hash:
            raise LaunchSafetyError(
                "Candidate directory tree changed before launch locking"
            )
        return CandidateFileLocks([])

    import ctypes
    from ctypes import wintypes

    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    generic_read = 0x80000000
    file_read_attributes = 0x00000080
    file_share_read = 0x00000001
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    invalid_handle = ctypes.c_void_p(-1).value

    locks = CandidateFileLocks([])

    def open_entry(path: Path, is_directory: bool) -> None:
        flags = file_attribute_normal | file_flag_open_reparse_point
        access = generic_read
        if is_directory:
            flags |= file_flag_backup_semantics
            access = file_read_attributes
        handle = create_file(
            str(path),
            access,
            file_share_read,
            None,
            open_existing,
            flags,
            None,
        )
        if handle == invalid_handle:
            raise LaunchSafetyError(
                f"Could not lock candidate tree entry {path}: "
                f"{ctypes.WinError(ctypes.get_last_error())}"
            )
        locks.handles.append(int(handle))

    try:
        _require_regular_directory(candidate_root, "Candidate root")
        open_entry(candidate_root, True)

        find_first_change = ctypes.WinDLL(
            "kernel32", use_last_error=True
        ).FindFirstChangeNotificationW
        find_first_change.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD]
        find_first_change.restype = wintypes.HANDLE
        notify_filters = 0x00000001 | 0x00000002 | 0x00000004 | 0x00000008
        notify_filters |= 0x00000010 | 0x00000040 | 0x00000100
        notification = find_first_change(str(candidate_root), True, notify_filters)
        if notification == invalid_handle:
            raise LaunchSafetyError(
                "Could not monitor candidate tree changes: "
                f"{ctypes.WinError(ctypes.get_last_error())}"
            )
        locks.change_notification_handle = int(notification)

        for path, is_directory in _candidate_tree_paths(candidate_root)[1:]:
            open_entry(path, is_directory)

        inventory = directory_inventory(candidate_root)
        if inventory["tree_sha256"] != expected_hash:
            raise LaunchSafetyError(
                "Candidate directory tree changed while its locks were acquired"
            )
        change_errors = locks.poll_change_errors()
        if change_errors:
            raise LaunchSafetyError("; ".join(change_errors))
    except BaseException:
        locks.close()
        raise
    return locks


def _without_windows_extended_prefix(path: str | os.PathLike[str]) -> str:
    value = os.fspath(path)
    if os.name != "nt":
        return value
    folded = value.casefold()
    if folded.startswith("\\\\?\\unc\\"):
        return "\\\\" + value[8:]
    if folded.startswith("\\\\?\\"):
        return value[4:]
    return value


def _normalized_path(path: str | os.PathLike[str]) -> str:
    requested = Path(_without_windows_extended_prefix(path))
    if not requested.is_absolute():
        raise LaunchSafetyError(f"Path must be absolute: {path}")
    resolved = requested.resolve(strict=False)
    return os.path.normcase(str(resolved)).casefold()


def _normalized_lexical_path(path: str | os.PathLike[str]) -> str:
    requested = Path(_without_windows_extended_prefix(path))
    if not requested.is_absolute():
        raise LaunchSafetyError(f"Path must be absolute: {path}")
    return os.path.normcase(os.path.abspath(os.fspath(requested))).casefold()


def _contains_formal_release_marker(path: Path) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    marker_length = len(FORMAL_RELEASE_MARKER)
    return any(
        parts[index : index + marker_length] == FORMAL_RELEASE_MARKER
        for index in range(len(parts) - marker_length + 1)
    )


def resolve_candidate_executable(value: str | os.PathLike[str]) -> Path:
    """Accept only the canonical candidate executable beneath this checkout."""
    requested = Path(value)
    if not requested.is_absolute():
        raise LaunchSafetyError("Candidate executable path must be absolute")
    if _contains_formal_release_marker(requested):
        raise LaunchSafetyError("Refusing to launch the formal portable release")

    expected = Path(PROJECT_ROOT) / CANONICAL_CANDIDATE_RELATIVE / APP_NAME
    if _normalized_lexical_path(requested) != _normalized_lexical_path(expected):
        raise LaunchSafetyError(
            f"Candidate executable must be the canonical path: {expected}"
        )

    raw_candidate_root = requested.parent
    _require_regular_directory(raw_candidate_root, "Canonical candidate root")
    if _normalized_lexical_path(raw_candidate_root) != _normalized_path(raw_candidate_root):
        raise LaunchSafetyError(
            f"Canonical candidate root resolves through an alternate path: {raw_candidate_root}"
        )
    try:
        metadata = requested.lstat()
    except OSError as error:
        raise LaunchSafetyError(
            f"Candidate executable does not exist: {requested}"
        ) from error
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or bool(attributes & reparse_flag)
        or not stat.S_ISREG(metadata.st_mode)
    ):
        raise LaunchSafetyError(
            f"Candidate executable must be a regular non-reparse file: {requested}"
        )
    executable = requested.resolve(strict=True)
    if _normalized_path(executable) != _normalized_lexical_path(expected):
        raise LaunchSafetyError(
            f"Candidate executable resolves outside its canonical path: {requested}"
        )
    return executable


def resolve_output_path(value: str | os.PathLike[str]) -> Path:
    requested = Path(value)
    if not requested.is_absolute():
        raise LaunchSafetyError("Screenshot output path must be absolute")
    if _contains_formal_release_marker(requested):
        raise LaunchSafetyError("Refusing to write a screenshot into the formal portable release")
    try:
        output_parent = requested.parent.resolve(strict=True)
    except OSError as error:
        raise LaunchSafetyError(
            f"Screenshot output parent must already exist: {requested.parent}"
        ) from error
    if not output_parent.is_dir():
        raise LaunchSafetyError(f"Screenshot output parent is not a directory: {output_parent}")
    output_path = output_parent / requested.name
    if _contains_formal_release_marker(output_path):
        raise LaunchSafetyError("Refusing to write a screenshot into the formal portable release")
    if output_path.exists() or output_path.is_symlink():
        raise LaunchSafetyError(f"Refusing to overwrite existing screenshot evidence: {output_path}")
    return output_path


def assert_output_outside_candidate(output_path: Path, executable: Path) -> None:
    candidate_root = executable.parent.resolve(strict=True)
    resolved_output = output_path.resolve(strict=False)
    if resolved_output == candidate_root or resolved_output.is_relative_to(candidate_root):
        raise LaunchSafetyError("Screenshot output must stay outside the candidate directory")


def expected_sidecar_path(executable: Path) -> Path:
    sidecar = (executable.parent / SIDECAR_RELATIVE_PATH).resolve(strict=False)
    candidate_root = executable.parent.resolve(strict=True)
    if not sidecar.is_relative_to(candidate_root) or _contains_formal_release_marker(sidecar):
        raise LaunchSafetyError("Candidate sidecar resolves outside the candidate directory")
    if not sidecar.is_file():
        raise LaunchSafetyError(f"Candidate sidecar does not exist: {sidecar}")
    return sidecar


def validate_expected_git_commit(value: str) -> str:
    expected = value.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40}", expected):
        raise LaunchSafetyError("Expected Git commit must be a full 40-character hash")
    return expected.lower()


def validate_expected_sha256(value: str, label: str) -> str:
    expected = value.strip().upper()
    if not re.fullmatch(r"[0-9A-F]{64}", expected):
        raise LaunchSafetyError(f"Expected {label} SHA-256 must be a full 64-character hash")
    return expected


def validated_candidate_snapshot(
    executable: Path,
    expected_git_commit: str,
    expected_app_sha256: str,
    expected_tree_sha256: str,
) -> CandidateSnapshot:
    executable = resolve_candidate_executable(executable)
    expected_commit = validate_expected_git_commit(expected_git_commit)
    expected_app_hash = validate_expected_sha256(expected_app_sha256, "candidate app")
    expected_tree_hash = validate_expected_sha256(expected_tree_sha256, "candidate tree")
    try:
        candidate = validate_candidate(executable.parent, expected_commit)
    except (OSError, ReleaseError, ValueError, TypeError) as error:
        raise LaunchSafetyError(f"Candidate release validation failed: {error}") from error
    if candidate["artifacts"]["app_sha256"] != expected_app_hash:
        raise LaunchSafetyError("Candidate app executable hash does not match the expected SHA-256")
    if candidate["inventory"]["tree_sha256"] != expected_tree_hash:
        raise LaunchSafetyError("Candidate directory tree does not match the expected SHA-256")
    sidecar = expected_sidecar_path(executable)
    artifacts = candidate["artifacts"]
    return CandidateSnapshot(
        executable=executable,
        candidate_dir=executable.parent,
        git_commit=expected_commit,
        app_sha256=expected_app_hash,
        tree_sha256=expected_tree_hash,
        sidecar=VerifiedSidecarIdentity(
            path=sidecar,
            sha256=str(artifacts["sidecar_sha256"]),
            manifest_sha256=str(artifacts["manifest_sha256"]),
        ),
    )


def validate_candidate_identity(
    executable: Path,
    expected_git_commit: str,
    expected_app_sha256: str,
    expected_tree_sha256: str,
) -> Path:
    return validated_candidate_snapshot(
        executable,
        expected_git_commit,
        expected_app_sha256,
        expected_tree_sha256,
    ).sidecar.path


def validate_cdp_port(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise LaunchSafetyError("CDP port must be an integer from 1 through 65535")
    if not 1 <= value <= 65535:
        raise LaunchSafetyError("CDP port must be from 1 through 65535")
    return value


def build_child_environment(
    data_dir: Path,
    cdp_port: int | None = None,
) -> dict[str, str]:
    resolved_data_dir = data_dir.resolve(strict=True)
    if not resolved_data_dir.is_absolute():
        raise LaunchSafetyError("Isolated data directory must be absolute")
    legacy_config = resolved_data_dir / "no-legacy-config.json"
    if legacy_config.exists():
        raise LaunchSafetyError("Isolated legacy-config sentinel must not exist")
    knowledge_base = resolved_data_dir / "no-knowledge-vault"
    knowledge_base.mkdir(exist_ok=False)
    webview_data = resolved_data_dir / "webview2-user-data"
    webview_data.mkdir(exist_ok=False)
    return _build_child_environment_for_bound_directories(
        resolved_data_dir,
        legacy_config,
        knowledge_base,
        webview_data,
        cdp_port,
    )


def _build_child_environment_for_bound_directories(
    resolved_data_dir: Path,
    legacy_config: Path,
    knowledge_base: Path,
    webview_data: Path,
    cdp_port: int | None,
) -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        normalized_name = name.casefold()
        if (
            normalized_name.startswith("product_atelier_")
            or normalized_name == WEBVIEW_ARGUMENTS_VARIABLE.casefold()
            or normalized_name == "webview2_user_data_folder"
        ):
            del environment[name]
    validated_cdp_port = validate_cdp_port(cdp_port)
    if validated_cdp_port is not None:
        environment[WEBVIEW_ARGUMENTS_VARIABLE] = (
            f"--remote-debugging-port={validated_cdp_port}"
        )
    environment["PRODUCT_ATELIER_CANDIDATE_ISOLATION"] = "1"
    environment["PRODUCT_ATELIER_DATA_DIR"] = str(resolved_data_dir)
    environment["PRODUCT_ATELIER_LEGACY_CONFIG"] = str(legacy_config)
    environment["PRODUCT_ATELIER_KNOWLEDGE_BASE"] = str(knowledge_base)
    environment["PRODUCT_ATELIER_WEBVIEW_DATA_DIR"] = str(webview_data)
    environment["WEBVIEW2_USER_DATA_FOLDER"] = str(webview_data)
    return environment


def rebuild_child_environment(
    data_dir: Path,
    cdp_port: int | None = None,
) -> dict[str, str]:
    resolved_data_dir = data_dir.resolve(strict=True)
    _require_regular_directory(resolved_data_dir, "Isolated data directory")
    legacy_config = resolved_data_dir / "no-legacy-config.json"
    if legacy_config.exists() or _is_link_like(legacy_config):
        raise LaunchSafetyError("Isolated legacy-config sentinel must not exist")
    knowledge_base = resolved_data_dir / "no-knowledge-vault"
    webview_data = resolved_data_dir / "webview2-user-data"
    _require_regular_directory(knowledge_base, "Isolated knowledge directory")
    _require_regular_directory(webview_data, "WebView2 data directory")
    return _build_child_environment_for_bound_directories(
        resolved_data_dir,
        legacy_config,
        knowledge_base,
        webview_data,
        cdp_port,
    )


def create_isolated_data_directory() -> IsolatedDataDirectory:
    temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    created = Path(
        tempfile.mkdtemp(prefix=ISOLATED_DATA_PREFIX, dir=str(temp_root))
    ).resolve(strict=True)
    location = IsolatedDataDirectory(path=created, temp_root=temp_root)
    _validate_isolated_data_directory(location)
    return location


def _is_link_like(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag)


def _require_regular_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise LaunchSafetyError(f"{label} is unavailable: {path}: {error}") from error
    if _is_link_like(path) or not stat.S_ISDIR(metadata.st_mode):
        raise LaunchSafetyError(f"{label} must be a regular directory: {path}")


def _validate_isolated_data_directory(location: IsolatedDataDirectory) -> None:
    _require_regular_directory(location.temp_root, "Recorded temp root")
    _require_regular_directory(location.path, "Isolated data directory")
    path = location.path.resolve(strict=True)
    temp_root = location.temp_root.resolve(strict=True)
    if (
        path == temp_root
        or path.parent != temp_root
        or not path.name.startswith(ISOLATED_DATA_PREFIX)
    ):
        raise LaunchSafetyError(f"Refusing unsafe isolated-data path: {path}")


def cleanup_isolated_data_directory(location: IsolatedDataDirectory) -> None:
    if not location.path.exists() and not _is_link_like(location.path):
        return
    _validate_isolated_data_directory(location)
    quarantine = location.temp_root / f"{ISOLATED_DATA_PREFIX}cleanup-{uuid.uuid4().hex}"
    if quarantine.exists() or _is_link_like(quarantine):
        raise LaunchSafetyError(f"Cleanup quarantine already exists: {quarantine}")
    source_identity = _path_identity(location.path, "isolated data directory")
    for attempt in range(CLEANUP_RENAME_RETRY_ATTEMPTS):
        try:
            os.replace(location.path, quarantine)
            break
        except OSError as error:
            retryable = (
                error.errno in CLEANUP_RETRYABLE_ERRNOS
                or getattr(error, "winerror", None) in CLEANUP_RETRYABLE_WINERRORS
            )
            if not retryable or attempt + 1 >= CLEANUP_RENAME_RETRY_ATTEMPTS:
                raise LaunchSafetyError(
                    f"Could not quarantine isolated data directory {location.path}: {error}"
                ) from error
            _validate_isolated_data_directory(location)
            if _path_identity(location.path, "isolated data directory") != source_identity:
                raise LaunchSafetyError(
                    "Isolated data directory identity changed during cleanup retry"
                )
            if quarantine.exists() or _is_link_like(quarantine):
                raise LaunchSafetyError(
                    f"Cleanup quarantine appeared during retry: {quarantine}"
                )
            time.sleep(CLEANUP_RENAME_RETRY_INTERVAL_SECONDS)
    quarantined = IsolatedDataDirectory(quarantine, location.temp_root)
    try:
        # Validate again after the atomic rename. If the source was exchanged
        # between validation and rename, never recurse into the moved object.
        _validate_isolated_data_directory(quarantined)
        if _path_identity(quarantine, "cleanup quarantine") != source_identity:
            raise LaunchSafetyError(
                "Isolated data directory identity changed before recursive delete"
            )
        shutil.rmtree(quarantine)
    except Exception as error:
        raise LaunchSafetyError(
            f"Isolated data cleanup was refused; preserved at {quarantine}: {error}"
        ) from error


def _path_identity(path: Path, label: str) -> tuple[int, int]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise LaunchSafetyError(f"Could not inspect {label}: {path}: {error}") from error
    if _is_link_like(path):
        raise LaunchSafetyError(f"{label} may not be a reparse point: {path}")
    return int(metadata.st_dev), int(metadata.st_ino)


class CandidateLaunchSession:
    """Own one verified candidate process and every resource protecting its run."""

    def __init__(
        self,
        *,
        executable: Path,
        expected_git_commit: str,
        expected_app_sha256: str,
        expected_tree_sha256: str,
        cdp_port: int | None = None,
        seed_review_fixture: bool = False,
    ) -> None:
        if not isinstance(seed_review_fixture, bool):
            raise LaunchSafetyError("seed_review_fixture must be a bool")
        self._requested_executable = Path(executable)
        self._expected_git_commit = validate_expected_git_commit(expected_git_commit)
        self._expected_app_sha256 = validate_expected_sha256(
            expected_app_sha256, "candidate app"
        )
        self._expected_tree_sha256 = validate_expected_sha256(
            expected_tree_sha256, "candidate tree"
        )
        self.cdp_port = validate_cdp_port(cdp_port)
        self.seed_review_fixture = seed_review_fixture

        self.process = None
        self.process_identity: ProcessIdentity | None = None
        self.candidate_exe: Path | None = None
        self.candidate_sha256: str | None = None
        self.candidate_dir: Path | None = None
        self.candidate_tree_sha256: str | None = None
        self.data_dir: Path | None = None
        self.webview_data_dir: Path | None = None
        self.knowledge_base_dir: Path | None = None
        self.legacy_config_path: Path | None = None
        self.sidecar_identity: VerifiedSidecarIdentity | None = None
        self.data_dir_identity: tuple[int, int] | None = None
        self.webview_data_dir_identity: tuple[int, int] | None = None
        self.knowledge_base_dir_identity: tuple[int, int] | None = None
        self.environment: dict[str, str] | None = None
        self.closed_cleanly = False

        self._promotion_context = None
        self._tree_locks: CandidateFileLocks | None = None
        self._isolated_location: IsolatedDataDirectory | None = None
        self._candidate_snapshot: CandidateSnapshot | None = None
        self._entered = False
        self._closed = False
        self._runtime_cleanup_attempted = False
        self._runtime_cleanup_errors: tuple[str, ...] = ()
        self._publication_ready = False
        self._restart_count = 0
        self._graceful_close_binding: GracefulCloseBinding | None = None

    @property
    def pid(self) -> int:
        if self.process_identity is None:
            raise LaunchSafetyError("Candidate process identity is not available")
        return self.process_identity.pid

    @property
    def create_time(self) -> float:
        if self.process_identity is None:
            raise LaunchSafetyError("Candidate process identity is not available")
        return self.process_identity.create_time

    @property
    def expected_git_commit(self) -> str:
        return self._expected_git_commit

    @property
    def publication_ready(self) -> bool:
        return self._publication_ready

    @property
    def publication_protections_held(self) -> bool:
        return (
            self._entered
            and not self._closed
            and self._tree_locks is not None
            and self._promotion_context is not None
        )

    @property
    def restart_count(self) -> int:
        return self._restart_count

    def _launch_runtime(self, snapshot: CandidateSnapshot) -> None:
        if self.process is not None or self.process_identity is not None:
            raise LaunchSafetyError("Candidate runtime is already active")
        if self.environment is None:
            raise LaunchSafetyError("Candidate runtime environment is unavailable")
        self.process = subprocess.Popen(
            [str(snapshot.executable)],
            cwd=str(snapshot.candidate_dir),
            env=self.environment,
        )
        self.process_identity = capture_launched_app_identity(
            self.process,
            snapshot.executable,
        )
        running_snapshot = validated_candidate_snapshot(
            snapshot.executable,
            snapshot.git_commit,
            snapshot.app_sha256,
            snapshot.tree_sha256,
        )
        if running_snapshot != snapshot:
            raise LaunchSafetyError("Candidate identity changed during process launch")

    def _stop_current_runtime(self) -> list[str]:
        if self.process is None:
            if self.process_identity is not None:
                return ["candidate process handle is missing while identity remains"]
            return []
        if self.sidecar_identity is None:
            return ["candidate sidecar identity is unavailable"]
        if (
            self._graceful_close_binding is not None
            and self.process.poll() is not None
        ):
            try:
                self._complete_armed_app_exit(require_success=False)
            except Exception as error:
                return [f"armed graceful cleanup: {error}"]
            return []
        errors = cleanup_launched_processes(
            self.process,
            self.process_identity,
            self.sidecar_identity.path,
        )
        if not errors:
            self.process = None
            self.process_identity = None
            self._graceful_close_binding = None
        return errors

    def _assert_isolated_data_binding(self) -> None:
        required = (
            self._isolated_location,
            self.data_dir,
            self.webview_data_dir,
            self.knowledge_base_dir,
            self.legacy_config_path,
            self.data_dir_identity,
            self.webview_data_dir_identity,
            self.knowledge_base_dir_identity,
        )
        if any(value is None for value in required):
            raise LaunchSafetyError("Persistent candidate isolation binding is incomplete")
        _validate_isolated_data_directory(self._isolated_location)  # type: ignore[arg-type]
        identities = (
            (
                self.data_dir,
                self.data_dir_identity,
                "isolated data directory",
            ),
            (
                self.webview_data_dir,
                self.webview_data_dir_identity,
                "WebView2 data directory",
            ),
            (
                self.knowledge_base_dir,
                self.knowledge_base_dir_identity,
                "isolated knowledge directory",
            ),
        )
        for path, expected, label in identities:
            if _path_identity(path, label) != expected:  # type: ignore[arg-type]
                raise LaunchSafetyError(f"{label} identity changed before candidate restart")
        if self.legacy_config_path.exists() or _is_link_like(self.legacy_config_path):
            raise LaunchSafetyError("Isolated legacy-config sentinel appeared before restart")

    def arm_graceful_close(
        self,
        *,
        timeout: float = 45.0,
        poll_interval: float = 0.1,
    ) -> GracefulCloseBinding:
        """Bind live child identities before a human closes the candidate window."""
        if not self._entered or self._closed:
            raise LaunchSafetyError("Candidate session is not active")
        if self._runtime_cleanup_attempted or self._publication_ready:
            raise LaunchSafetyError("Candidate session has already entered final cleanup")
        if self._graceful_close_binding is not None:
            raise LaunchSafetyError("Candidate graceful close is already armed")
        if timeout <= 0 or poll_interval <= 0:
            raise LaunchSafetyError("Graceful close wait values must be positive")
        if (
            self.process is None
            or self.process_identity is None
            or self.sidecar_identity is None
            or self.webview_data_dir is None
        ):
            raise LaunchSafetyError("Candidate graceful close binding is unavailable")

        assert_launched_app_identity(self.process, self.process_identity)
        deadline = time.monotonic() + timeout
        while True:
            observed_at = time.time()
            sidecars = matching_sidecars(
                expected_executable=self.sidecar_identity.path,
                expected_parent_pid=self.process_identity.pid,
                earliest_create_time=self.process_identity.create_time,
                latest_create_time=observed_at,
            )
            if len(sidecars) > 1:
                raise LaunchSafetyError(
                    "Candidate exposed more than one exact child sidecar before graceful close"
                )
            webviews = matching_webview_processes(self.webview_data_dir)
            if len(sidecars) == 1 and webviews:
                assert_launched_app_identity(self.process, self.process_identity)
                binding = GracefulCloseBinding(
                    app_identity=self.process_identity,
                    sidecars=tuple(sidecars),
                    webviews=tuple(webviews),
                    armed_at=observed_at,
                )
                self._graceful_close_binding = binding
                return binding
            if time.monotonic() >= deadline:
                raise LaunchSafetyError(
                    "Candidate App, exact sidecar, and isolated WebView profile were not "
                    "all observable before graceful close"
                )
            time.sleep(poll_interval)

    def _wait_for_natural_app_exit(
        self,
        *,
        timeout: float,
        poll_interval: float,
    ) -> int:
        if timeout <= 0 or poll_interval <= 0:
            raise LaunchSafetyError("Natural App exit wait values must be positive")
        if self.process is None:
            raise LaunchSafetyError("Candidate process handle is unavailable")
        deadline = time.monotonic() + timeout
        while True:
            returncode = self.process.poll()
            if returncode is not None:
                return int(returncode)
            if time.monotonic() >= deadline:
                raise LaunchSafetyError(
                    "Candidate App is still running; close it through the real "
                    "application UI first"
                )
            time.sleep(poll_interval)

    def _complete_armed_app_exit(self, *, require_success: bool) -> int:
        binding = self._graceful_close_binding
        if binding is None:
            raise LaunchSafetyError("Candidate graceful close was not armed")
        if self.process is None or self.process_identity is None:
            raise LaunchSafetyError("Candidate process binding disappeared before graceful close")
        if (
            self.process_identity != binding.app_identity
            or self.process.pid != binding.app_identity.pid
        ):
            raise LaunchSafetyError("Candidate App identity changed after graceful close was armed")
        returncode = self.process.poll()
        if returncode is None:
            raise LaunchSafetyError("Candidate App has not exited after graceful close was armed")
        if require_success and int(returncode) != 0:
            raise LaunchSafetyError(
                "Candidate App did not exit cleanly after the real UI close; "
                f"returncode={returncode}"
            )
        if self.sidecar_identity is None or self.webview_data_dir is None:
            raise LaunchSafetyError("Candidate child-process binding is unavailable")

        cleanup_errors: list[str] = []
        for tracked in binding.sidecars:
            try:
                if tracked_sidecar_is_current(
                    tracked,
                    expected_executable=self.sidecar_identity.path,
                ):
                    _stop_matching_sidecar(
                        tracked,
                        expected_executable=self.sidecar_identity.path,
                        expected_parent_pid=None,
                    )
                if tracked_sidecar_is_current(
                    tracked,
                    expected_executable=self.sidecar_identity.path,
                ):
                    cleanup_errors.append(
                        "armed candidate sidecar remained after graceful App exit: "
                        f"PID {tracked.identity.pid}"
                    )
            except Exception as error:
                cleanup_errors.append(
                    f"armed sidecar PID {tracked.identity.pid}: {error}"
                )

        remaining = matching_sidecars(
            expected_executable=self.sidecar_identity.path,
            expected_parent_pid=binding.app_identity.pid,
            earliest_create_time=binding.app_identity.create_time,
            latest_create_time=time.time(),
        )
        armed_identities = {tracked.identity for tracked in binding.sidecars}
        unbound = [
            tracked.identity
            for tracked in remaining
            if tracked.identity not in armed_identities
        ]
        if unbound:
            cleanup_errors.append(
                "unbound candidate sidecar appeared after graceful close: "
                + ", ".join(
                    f"PID {identity.pid}@{identity.create_time}"
                    for identity in unbound
                )
            )
        if cleanup_errors:
            raise LaunchSafetyError("; ".join(cleanup_errors))

        wait_for_webview_processes_to_exit(self.webview_data_dir)
        self._assert_isolated_data_binding()
        self.process = None
        self.process_identity = None
        self._graceful_close_binding = None
        return int(returncode)

    def complete_graceful_close(
        self,
        *,
        timeout: float = NATURAL_APP_EXIT_TIMEOUT_SECONDS,
        poll_interval: float = NATURAL_APP_EXIT_POLL_INTERVAL_SECONDS,
    ) -> int:
        """Accept only a previously armed natural App exit and clean its bound children."""
        if self._graceful_close_binding is None:
            raise LaunchSafetyError("Candidate graceful close was not armed")
        self._wait_for_natural_app_exit(
            timeout=timeout,
            poll_interval=poll_interval,
        )
        return self._complete_armed_app_exit(require_success=True)

    def restart_with_same_data(
        self,
        *,
        timeout: float = NATURAL_APP_EXIT_TIMEOUT_SECONDS,
        poll_interval: float = NATURAL_APP_EXIT_POLL_INTERVAL_SECONDS,
    ) -> "CandidateLaunchSession":
        """Restart the verified candidate while retaining its bound isolated ledger."""
        if not self._entered or self._closed:
            raise LaunchSafetyError("Candidate session is not active")
        if self._runtime_cleanup_attempted or self._publication_ready:
            raise LaunchSafetyError("Candidate session has already entered final cleanup")
        self.complete_graceful_close(
            timeout=timeout,
            poll_interval=poll_interval,
        )
        if self._candidate_snapshot is None or self.webview_data_dir is None:
            raise LaunchSafetyError("Candidate restart binding is unavailable")
        protection_errors = self._candidate_protection_errors()
        if protection_errors:
            raise LaunchSafetyError(
                "Candidate protections changed before restart; "
                + "; ".join(protection_errors)
            )
        if self.data_dir is None:
            raise LaunchSafetyError("Candidate isolated data directory is unavailable")
        self.environment = rebuild_child_environment(self.data_dir, self.cdp_port)
        self._assert_isolated_data_binding()
        self._launch_runtime(self._candidate_snapshot)
        self._restart_count += 1
        return self

    def __enter__(self) -> "CandidateLaunchSession":
        if self._entered or self._closed:
            raise LaunchSafetyError("Candidate launch session may only be entered once")
        try:
            project = portable_release._project_root(PROJECT_ROOT)
            promotion_context = portable_release._promotion_lock(project)
            promotion_context.__enter__()
            self._promotion_context = promotion_context

            snapshot = validated_candidate_snapshot(
                self._requested_executable,
                self._expected_git_commit,
                self._expected_app_sha256,
                self._expected_tree_sha256,
            )
            self._tree_locks = acquire_candidate_tree_locks(
                snapshot.candidate_dir,
                snapshot.tree_sha256,
            )
            self._candidate_snapshot = snapshot
            locked_snapshot = validated_candidate_snapshot(
                snapshot.executable,
                snapshot.git_commit,
                snapshot.app_sha256,
                snapshot.tree_sha256,
            )
            if locked_snapshot != snapshot:
                raise LaunchSafetyError(
                    "Candidate identity changed while launch locks were acquired"
                )
            self._candidate_snapshot = locked_snapshot
            self.candidate_exe = locked_snapshot.executable
            self.candidate_sha256 = locked_snapshot.app_sha256
            self.candidate_dir = locked_snapshot.candidate_dir
            self.candidate_tree_sha256 = locked_snapshot.tree_sha256
            self.sidecar_identity = locked_snapshot.sidecar

            location = create_isolated_data_directory()
            self._isolated_location = location
            self.data_dir = location.path
            try:
                if any(location.path.iterdir()):
                    raise LaunchSafetyError(
                        f"New isolated data directory is not empty: {location.path}"
                    )
            except OSError as error:
                raise LaunchSafetyError(
                    f"Could not verify empty isolated data directory: {location.path}"
                ) from error
            if self.seed_review_fixture:
                from seed_feedback_checkpoint import seed_feedback_checkpoint

                seed_feedback_checkpoint(location.path)

            self.environment = build_child_environment(location.path, self.cdp_port)
            self.webview_data_dir = location.path / "webview2-user-data"
            self.knowledge_base_dir = location.path / "no-knowledge-vault"
            self.legacy_config_path = location.path / "no-legacy-config.json"
            self.data_dir_identity = _path_identity(location.path, "isolated data directory")
            self.webview_data_dir_identity = _path_identity(
                self.webview_data_dir,
                "WebView2 data directory",
            )
            self.knowledge_base_dir_identity = _path_identity(
                self.knowledge_base_dir,
                "isolated knowledge directory",
            )
            self._launch_runtime(locked_snapshot)
            self._entered = True
            return self
        except BaseException as error:
            cleanup_errors = self._teardown()
            if cleanup_errors:
                detail = str(error) or repr(error)
                raise LaunchSafetyError(
                    "Candidate session setup failed and cleanup was incomplete; "
                    f"primary={type(error).__name__}: {detail}; "
                    f"cleanup={'; '.join(cleanup_errors)}"
                ) from error
            raise

    def _cleanup_runtime(self) -> list[str]:
        if self._runtime_cleanup_attempted:
            return list(self._runtime_cleanup_errors)
        self._runtime_cleanup_attempted = True
        cleanup_errors: list[str] = []
        try:
            cleanup_errors.extend(self._stop_current_runtime())
        except Exception as error:
            cleanup_errors.append(f"process cleanup raised unexpectedly: {error}")

        if self._isolated_location is not None:
            if cleanup_errors:
                cleanup_errors.append(
                    f"isolated data preserved at {self._isolated_location.path}"
                )
            else:
                try:
                    cleanup_isolated_data_directory(self._isolated_location)
                except Exception as error:
                    cleanup_errors.append(f"isolated data cleanup: {error}")

        self._runtime_cleanup_errors = tuple(cleanup_errors)
        return list(cleanup_errors)

    def _candidate_protection_errors(self) -> list[str]:
        if self._tree_locks is None:
            return []
        errors: list[str] = []

        def poll_tree_changes() -> None:
            try:
                poll_errors = self._tree_locks.poll_change_errors()  # type: ignore[union-attr]
            except Exception as error:
                errors.append(
                    f"candidate tree lock query raised unexpectedly: {error}"
                )
                return
            errors.extend(
                message
                for error in poll_errors
                if (message := f"candidate tree lock: {error}") not in errors
            )

        poll_tree_changes()
        if self._candidate_snapshot is not None:
            try:
                final_snapshot = validated_candidate_snapshot(
                    self._candidate_snapshot.executable,
                    self._candidate_snapshot.git_commit,
                    self._candidate_snapshot.app_sha256,
                    self._candidate_snapshot.tree_sha256,
                )
                if final_snapshot != self._candidate_snapshot:
                    raise LaunchSafetyError("Candidate identity changed after the run")
            except Exception as error:
                errors.append(f"candidate identity after run: {error}")
        poll_tree_changes()
        return errors

    def prepare_for_publication(self) -> None:
        """Clean runtime state and reverify the candidate without releasing its locks."""
        if not self._entered or self._closed:
            raise LaunchSafetyError("Candidate session is not open for publication")
        if self._publication_ready:
            raise LaunchSafetyError("Candidate session was already prepared for publication")
        preparation_errors = self._cleanup_runtime()
        if not preparation_errors:
            preparation_errors.extend(self._candidate_protection_errors())
        if preparation_errors:
            raise LaunchSafetyError(
                "Candidate session publication preparation failed; "
                f"cleanup={'; '.join(preparation_errors)}"
            )
        self._publication_ready = True

    def _teardown(self) -> list[str]:
        if self._closed:
            return []
        cleanup_errors = self._cleanup_runtime()

        if self._tree_locks is not None:
            cleanup_errors.extend(self._candidate_protection_errors())
            try:
                cleanup_errors.extend(
                    f"candidate tree lock cleanup: {error}"
                    for error in self._tree_locks.close()
                )
            except Exception as error:
                cleanup_errors.append(
                    f"candidate tree lock cleanup raised unexpectedly: {error}"
                )
            self._tree_locks = None

        if self._promotion_context is not None:
            try:
                self._promotion_context.__exit__(None, None, None)
            except Exception as error:
                cleanup_errors.append(f"promotion lock cleanup: {error}")
            self._promotion_context = None

        self._closed = True
        self.closed_cleanly = not cleanup_errors
        return cleanup_errors

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        cleanup_errors = self._teardown()
        if cleanup_errors:
            if exc_value is not None:
                detail = str(exc_value) or repr(exc_value)
                raise LaunchSafetyError(
                    "Candidate session failed and cleanup was incomplete; "
                    f"primary={type(exc_value).__name__}: {detail}; "
                    f"cleanup={'; '.join(cleanup_errors)}"
                ) from exc_value
            raise LaunchSafetyError(
                "Candidate session cleanup failed; "
                f"cleanup={'; '.join(cleanup_errors)}"
            )
        return False


def capture_launched_app_identity(
    process,
    expected_executable: Path | None = None,
) -> ProcessIdentity:
    pid = int(process.pid)
    if process.poll() is not None:
        raise LaunchSafetyError("Candidate app exited before its process identity was captured")
    try:
        live_process = psutil.Process(pid)
        create_time = float(live_process.create_time())
        is_running = live_process.is_running()
        executable = live_process.exe() if expected_executable is not None else None
    except (OSError, psutil.Error) as error:
        raise LaunchSafetyError(f"Could not capture candidate app process identity: {error}") from error
    if not is_running:
        raise LaunchSafetyError("Candidate app is not running after launch")
    if expected_executable is not None and (
        not executable
        or _normalized_path(executable) != _normalized_path(expected_executable)
    ):
        raise LaunchSafetyError(
            "Launched process executable does not match the canonical candidate app"
        )
    return ProcessIdentity(pid=pid, create_time=create_time)


def launched_app_identity_is_current(process, identity: ProcessIdentity) -> bool:
    if int(process.pid) != identity.pid or process.poll() is not None:
        return False
    try:
        live_process = psutil.Process(identity.pid)
        return live_process.is_running() and float(live_process.create_time()) == identity.create_time
    except (OSError, psutil.Error):
        return False


def assert_launched_app_identity(process, identity: ProcessIdentity) -> None:
    if not launched_app_identity_is_current(process, identity):
        raise LaunchSafetyError("Candidate app PID identity changed or exited during capture")


def capture_matching_sidecar(
    process: psutil.Process,
    *,
    expected_executable: Path,
    expected_parent_pid: int | None,
    earliest_create_time: float | None = None,
    latest_create_time: float | None = None,
) -> TrackedSidecar | None:
    """Capture a sidecar only when its path, parent, and time window match."""
    try:
        pid = int(process.pid)
        create_time = float(process.create_time())
        executable = process.exe()
        parent_pid = process.ppid() if expected_parent_pid is not None else None
    except (OSError, psutil.Error):
        return None
    if expected_parent_pid is not None and parent_pid != expected_parent_pid:
        return None
    if earliest_create_time is not None and create_time < earliest_create_time:
        return None
    if latest_create_time is not None and create_time > latest_create_time:
        return None
    if not executable:
        return None
    try:
        matches = _normalized_path(executable) == _normalized_path(expected_executable)
    except (OSError, LaunchSafetyError):
        return None
    if not matches:
        return None
    return TrackedSidecar(
        process=process,
        identity=ProcessIdentity(pid=pid, create_time=create_time),
    )


def process_matches_sidecar(
    tracked: TrackedSidecar,
    *,
    expected_executable: Path,
    expected_parent_pid: int | None,
) -> bool:
    current = capture_matching_sidecar(
        tracked.process,
        expected_executable=expected_executable,
        expected_parent_pid=expected_parent_pid,
    )
    return current is not None and current.identity == tracked.identity


def tracked_sidecar_is_current(
    tracked: TrackedSidecar,
    *,
    expected_executable: Path,
) -> bool:
    """Revalidate an armed sidecar without ever following a reused PID."""
    process = tracked.process
    try:
        if int(process.pid) != tracked.identity.pid:
            return False
        create_time = float(process.create_time())
        if create_time != tracked.identity.create_time:
            return False
        executable = process.exe()
        if not executable:
            raise LaunchSafetyError(
                f"Armed sidecar PID {tracked.identity.pid} exposed no executable path"
            )
        if not process.is_running():
            return False
    except psutil.NoSuchProcess:
        return False
    except (OSError, ValueError, psutil.Error, LaunchSafetyError) as error:
        if isinstance(error, LaunchSafetyError):
            raise
        raise LaunchSafetyError(
            f"Could not revalidate armed sidecar PID {tracked.identity.pid}: {error}"
        ) from error
    try:
        return _normalized_path(executable) == _normalized_path(expected_executable)
    except (OSError, LaunchSafetyError) as error:
        raise LaunchSafetyError(
            f"Could not compare armed sidecar PID {tracked.identity.pid} executable: {error}"
        ) from error


def matching_sidecars(
    *,
    expected_executable: Path,
    expected_parent_pid: int,
    earliest_create_time: float | None = None,
    latest_create_time: float | None = None,
    processes: Iterable[psutil.Process] | None = None,
) -> list[TrackedSidecar]:
    candidates = processes if processes is not None else psutil.process_iter()
    matches: list[TrackedSidecar] = []
    for process in candidates:
        tracked = capture_matching_sidecar(
            process,
            expected_executable=expected_executable,
            expected_parent_pid=expected_parent_pid,
            earliest_create_time=earliest_create_time,
            latest_create_time=latest_create_time,
        )
        if tracked is not None:
            matches.append(tracked)
    return matches


def _command_line_option_values(command_line: Sequence[str], name: str) -> list[str]:
    normalized_name = name.casefold()
    prefix = f"{normalized_name}="
    values: list[str] = []
    for index, argument in enumerate(command_line):
        normalized_argument = str(argument).casefold()
        if normalized_argument.startswith(prefix):
            values.append(str(argument)[len(prefix) :])
        elif normalized_argument == normalized_name and index + 1 < len(command_line):
            values.append(str(command_line[index + 1]))
    return values


def matching_webview_processes(
    webview_data_dir: Path,
    *,
    processes: Iterable[psutil.Process] | None = None,
) -> list[ProcessIdentity]:
    expected_profiles = {
        _normalized_path(webview_data_dir),
        _normalized_path(webview_data_dir / WEBVIEW_RUNTIME_DATA_DIRECTORY_NAME),
    }
    candidates = processes if processes is not None else psutil.process_iter()
    matches: list[ProcessIdentity] = []
    for process in candidates:
        try:
            process_name = process.name()
        except psutil.NoSuchProcess:
            continue
        except (OSError, psutil.Error) as error:
            raise LaunchSafetyError(
                f"Could not inspect process name while proving WebView shutdown: {error}"
            ) from error
        if process_name.casefold() != WEBVIEW_PROCESS_NAME:
            continue
        try:
            command_line = process.cmdline()
            profile_paths = _command_line_option_values(
                command_line,
                WEBVIEW_USER_DATA_ARGUMENT,
            )
            normalized_profiles = [
                _normalized_path(profile_path) for profile_path in profile_paths
            ]
            matching_profiles = [
                profile
                for profile in normalized_profiles
                if profile in expected_profiles
            ]
            if not matching_profiles:
                continue
            if len(normalized_profiles) != 1:
                raise LaunchSafetyError(
                    "Isolated WebView process exposed an ambiguous user-data profile"
                )
            if not process.is_running():
                continue
            matches.append(
                ProcessIdentity(
                    pid=int(process.pid),
                    create_time=float(process.create_time()),
                )
            )
        except psutil.NoSuchProcess:
            continue
        except (OSError, ValueError, psutil.Error, LaunchSafetyError) as error:
            raise LaunchSafetyError(
                "Could not prove isolated WebView process identity for "
                f"PID {getattr(process, 'pid', 'unknown')}: {error}"
            ) from error
    return matches


def wait_for_webview_processes_to_exit(
    webview_data_dir: Path,
    *,
    timeout: float = WEBVIEW_SHUTDOWN_TIMEOUT_SECONDS,
    poll_interval: float = WEBVIEW_SHUTDOWN_POLL_INTERVAL_SECONDS,
) -> None:
    if timeout < 0 or poll_interval <= 0:
        raise LaunchSafetyError("WebView shutdown wait values must be positive")
    deadline = time.monotonic() + timeout
    while True:
        matches = matching_webview_processes(webview_data_dir)
        if not matches:
            return
        if time.monotonic() >= deadline:
            identities = ", ".join(
                f"PID {identity.pid}@{identity.create_time}"
                for identity in matches
            )
            raise LaunchSafetyError(
                "Isolated WebView2 processes remained after candidate stop: "
                + identities
            )
        time.sleep(poll_interval)


def _stop_launched_app(process, timeout: float = 5.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


def _stop_matching_sidecar(
    tracked: TrackedSidecar,
    *,
    expected_executable: Path,
    expected_parent_pid: int | None,
    timeout: float = 5.0,
) -> None:
    def still_matches() -> bool:
        if expected_parent_pid is None:
            return tracked_sidecar_is_current(
                tracked,
                expected_executable=expected_executable,
            )
        return process_matches_sidecar(
            tracked,
            expected_executable=expected_executable,
            expected_parent_pid=expected_parent_pid,
        )

    if not still_matches():
        return
    process = tracked.process
    try:
        process.terminate()
        process.wait(timeout=timeout)
    except psutil.NoSuchProcess:
        return
    except psutil.TimeoutExpired:
        if not still_matches():
            return
        process.kill()
        try:
            process.wait(timeout=timeout)
        except psutil.NoSuchProcess:
            return


def cleanup_launched_processes(
    app_process,
    app_identity: ProcessIdentity | None,
    expected_sidecar: Path,
) -> list[str]:
    """Stop only the launched app and sidecars proven to be its exact children."""
    app_pid = int(app_process.pid)
    cleanup_errors: list[str] = []
    tracked_by_identity: dict[ProcessIdentity, TrackedSidecar] = {}
    can_track_sidecars = False

    def discover_sidecars(label: str, latest_create_time: float) -> None:
        if not can_track_sidecars or app_identity is None:
            return
        try:
            discovered = matching_sidecars(
                expected_executable=expected_sidecar,
                expected_parent_pid=app_pid,
                earliest_create_time=app_identity.create_time,
                latest_create_time=latest_create_time,
            )
        except Exception as error:
            cleanup_errors.append(f"sidecar discovery {label}: {error}")
            return
        for sidecar in discovered:
            tracked_by_identity[sidecar.identity] = sidecar

    if app_identity is None:
        cleanup_errors.append("candidate app identity was not captured; sidecar cleanup skipped")
    elif not launched_app_identity_is_current(app_process, app_identity):
        cleanup_errors.append("candidate app identity changed before sidecar cleanup")
    else:
        can_track_sidecars = True
        discover_sidecars("before app stop", time.time())

    try:
        _stop_launched_app(app_process)
    except Exception as error:
        cleanup_errors.append(f"app PID {app_pid}: {error}")

    app_stop_cutoff = time.time()
    discover_sidecars("after app stop", app_stop_cutoff)

    for sidecar in tracked_by_identity.values():
        try:
            _stop_matching_sidecar(
                sidecar,
                expected_executable=expected_sidecar,
                # The parent may no longer exist or may have been reaped. The
                # exact PID, create time, and executable path remain required.
                expected_parent_pid=None,
            )
        except Exception as error:
            cleanup_errors.append(f"sidecar PID {sidecar.identity.pid}: {error}")

    if can_track_sidecars and app_identity is not None:
        try:
            remaining = matching_sidecars(
                expected_executable=expected_sidecar,
                expected_parent_pid=app_pid,
                earliest_create_time=app_identity.create_time,
                latest_create_time=app_stop_cutoff,
            )
        except Exception as error:
            cleanup_errors.append(f"sidecar discovery after cleanup: {error}")
        else:
            for sidecar in remaining:
                cleanup_errors.append(
                    "candidate sidecar remained after cleanup: "
                    f"PID {sidecar.identity.pid}, create_time={sidecar.identity.create_time}"
                )
    return cleanup_errors


def assert_window_owned_by_pid(hwnd: int, expected_pid: int) -> None:
    if os.name != "nt":
        raise LaunchSafetyError("Window ownership validation requires Windows")

    import ctypes
    from ctypes import wintypes

    get_window_process = ctypes.WinDLL(
        "user32", use_last_error=True
    ).GetWindowThreadProcessId
    get_window_process.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    get_window_process.restype = wintypes.DWORD
    owner_pid = wintypes.DWORD()
    thread_id = get_window_process(hwnd, ctypes.byref(owner_pid))
    if not thread_id:
        raise LaunchSafetyError(
            f"Could not validate candidate window ownership: "
            f"{ctypes.WinError(ctypes.get_last_error())}"
        )
    if int(owner_pid.value) != int(expected_pid):
        raise LaunchSafetyError(
            f"Candidate window owner changed from PID {expected_pid} "
            f"to PID {owner_pid.value}"
        )


def _discard_pending_screenshot(path: Path | None) -> None:
    if path is None:
        return
    if not path.name.startswith(".incomplete-") or path.suffix.casefold() != ".png":
        raise LaunchSafetyError(f"Refusing to remove an invalid pending screenshot path: {path}")
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        raise LaunchSafetyError(f"Could not remove pending screenshot {path}: {error}") from error


def publish_pending_screenshot(pending_path: Path, output_path: Path) -> Path:
    output_path = resolve_output_path(output_path)
    if pending_path.parent != output_path.parent:
        raise LaunchSafetyError("Pending screenshot must share the final output directory")
    if not pending_path.name.startswith(".incomplete-") or pending_path.suffix.casefold() != ".png":
        raise LaunchSafetyError(f"Invalid pending screenshot path: {pending_path}")
    try:
        metadata = pending_path.lstat()
    except OSError as error:
        raise LaunchSafetyError(f"Pending screenshot is unavailable: {pending_path}") from error
    if _is_link_like(pending_path) or not stat.S_ISREG(metadata.st_mode):
        raise LaunchSafetyError(f"Pending screenshot must be a regular file: {pending_path}")
    if os.name == "nt" and metadata.st_nlink != 1:
        raise LaunchSafetyError(f"Pending screenshot may not be a hard link: {pending_path}")

    linked = False
    try:
        os.link(pending_path, output_path)
        linked = True
    except FileExistsError as error:
        raise LaunchSafetyError(
            f"Refusing to overwrite existing screenshot evidence: {output_path}"
        ) from error
    except OSError as error:
        unsupported_hardlink = os.name == "nt" and getattr(error, "winerror", None) in {
            1,
            50,
        }
        if not unsupported_hardlink:
            raise LaunchSafetyError(
                f"Could not atomically publish screenshot evidence: {error}"
            ) from error
        if output_path.exists() or output_path.is_symlink():
            raise LaunchSafetyError(
                f"Refusing to overwrite existing screenshot evidence: {output_path}"
            )
        try:
            os.rename(pending_path, output_path)
        except OSError as rename_error:
            raise LaunchSafetyError(
                f"Could not atomically publish screenshot evidence: {rename_error}"
            ) from rename_error
        return output_path

    try:
        pending_path.unlink()
    except OSError as error:
        if linked:
            try:
                output_path.unlink()
            except OSError as rollback_error:
                raise LaunchSafetyError(
                    "Screenshot was linked but incomplete evidence cleanup and "
                    f"publication rollback both failed: {error}; {rollback_error}"
                ) from error
        raise LaunchSafetyError(
            f"Could not remove pending screenshot after publication: {error}"
        ) from error
    return output_path


def capture_launched_window(
    *,
    app_process,
    app_identity: ProcessIdentity,
    output_path: Path,
    wait_seconds: float,
    padding: int,
) -> Path:
    # Importing screenshot initializes Win32 APIs, so keep it out of unit-test imports.
    from screenshot import capture_region, find_window_by_pid, save_png

    assert_launched_app_identity(app_process, app_identity)
    time.sleep(wait_seconds)
    assert_launched_app_identity(app_process, app_identity)
    result = find_window_by_pid(app_identity.pid)
    if not result:
        raise RuntimeError(f"Candidate window for PID {app_identity.pid} was not found")

    hwnd, (left, top, right, bottom), _ = result
    assert_window_owned_by_pid(hwnd, app_identity.pid)
    x = left - padding
    y = top - padding
    width = (right - left) + padding * 2
    height = (bottom - top) + padding * 2
    if width <= 0 or height <= 0:
        raise RuntimeError("Candidate window has an invalid capture rectangle")

    assert_launched_app_identity(app_process, app_identity)
    raw_data, captured_width, captured_height = capture_region(x, y, width, height)
    assert_window_owned_by_pid(hwnd, app_identity.pid)
    assert_launched_app_identity(app_process, app_identity)
    output_path = resolve_output_path(output_path)
    temporary_output = output_path.with_name(
        f".incomplete-{uuid.uuid4().hex}.png"
    )
    try:
        save_png(raw_data, captured_width, captured_height, temporary_output)
        if not temporary_output.is_file():
            raise RuntimeError("Screenshot encoder did not create the expected PNG evidence")
        if output_path.exists() or output_path.is_symlink():
            raise LaunchSafetyError(
                f"Screenshot evidence appeared during capture; refusing overwrite: {output_path}"
            )
    except BaseException:
        _discard_pending_screenshot(temporary_output)
        raise
    return temporary_output


def launch_and_capture(
    *,
    executable: Path,
    output_path: Path,
    expected_git_commit: str,
    expected_app_sha256: str,
    expected_tree_sha256: str,
    wait_seconds: float,
    padding: int,
    cdp_port: int | None = None,
    seed_review_fixture: bool = False,
) -> Path:
    executable = resolve_candidate_executable(executable)
    output_path = resolve_output_path(output_path)
    assert_output_outside_candidate(output_path, executable)
    pending_output: Path | None = None
    session = CandidateLaunchSession(
        executable=executable,
        expected_git_commit=expected_git_commit,
        expected_app_sha256=expected_app_sha256,
        expected_tree_sha256=expected_tree_sha256,
        cdp_port=cdp_port,
        seed_review_fixture=seed_review_fixture,
    )
    try:
        with session:
            if session.process is None or session.process_identity is None:
                raise LaunchSafetyError("Candidate session did not expose its process identity")
            pending_output = capture_launched_window(
                app_process=session.process,
                app_identity=session.process_identity,
                output_path=output_path,
                wait_seconds=wait_seconds,
                padding=padding,
            )
    except BaseException as error:
        try:
            _discard_pending_screenshot(pending_output)
        except Exception as cleanup_error:
            raise LaunchSafetyError(
                f"Candidate run failed: {error}; "
                f"pending screenshot cleanup failed: {cleanup_error}"
            ) from error
        if isinstance(error, ReleaseError):
            raise LaunchSafetyError(f"Portable promotion lock failed: {error}") from error
        raise

    if not session.closed_cleanly or pending_output is None:
        _discard_pending_screenshot(pending_output)
        raise LaunchSafetyError("Candidate session did not complete cleanly")
    try:
        return publish_pending_screenshot(pending_output, output_path)
    except BaseException as error:
        try:
            _discard_pending_screenshot(pending_output)
        except Exception as cleanup_error:
            raise LaunchSafetyError(
                f"Screenshot publication failed: {error}; "
                f"pending cleanup failed: {cleanup_error}"
            ) from error
        raise


def resolve_evidence_output_directory(
    value: str | os.PathLike[str],
    candidate_executable: Path,
) -> Path:
    requested = Path(value)
    if not requested.is_absolute():
        raise LaunchSafetyError("Formal evidence output directory must be absolute")
    if requested.name in {"", ".", ".."}:
        raise LaunchSafetyError("Formal evidence output must have a concrete directory name")
    _require_regular_directory(requested.parent, "Formal evidence output parent")
    parent = requested.parent.resolve(strict=True)
    output = parent / requested.name
    if output.exists() or _is_link_like(output):
        raise LaunchSafetyError(f"Refusing to overwrite formal evidence: {output}")
    if _contains_formal_release_marker(output):
        raise LaunchSafetyError("Formal evidence may not be written into the portable release")
    candidate_root = candidate_executable.parent.resolve(strict=True)
    if output == candidate_root or output.is_relative_to(candidate_root):
        raise LaunchSafetyError("Formal evidence must stay outside the candidate directory")
    return output


def _identity_payload(identity: tuple[int, int] | None) -> dict[str, int]:
    if identity is None:
        raise LaunchSafetyError("Required filesystem identity was not captured")
    return {"st_dev": identity[0], "st_ino": identity[1]}


def _receipt_process_create_time(value: Any) -> float:
    try:
        create_time = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise LaunchSafetyError(
            "Verification receipt process create time is invalid"
        ) from error
    if not math.isfinite(create_time) or create_time < 0:
        raise LaunchSafetyError("Verification receipt process create time is invalid")
    return create_time


def _read_stable_json(path: Path, label: str) -> tuple[dict[str, Any], str]:
    size_before, sha_before = portable_release._stable_file_record(path, label)
    try:
        payload_bytes = path.read_bytes()
    except OSError as error:
        raise LaunchSafetyError(f"Could not read {label}: {path}: {error}") from error
    size_after, sha_after = portable_release._stable_file_record(path, label)
    payload_sha = hashlib.sha256(payload_bytes).hexdigest().upper()
    if (
        size_before != len(payload_bytes)
        or size_after != len(payload_bytes)
        or sha_before != payload_sha
        or sha_after != payload_sha
    ):
        raise LaunchSafetyError(f"{label} changed while launcher read it: {path}")
    try:
        payload = json.loads(payload_bytes.decode("utf-8-sig"))
    except (UnicodeError, ValueError, TypeError) as error:
        raise LaunchSafetyError(f"{label} is not valid JSON: {path}: {error}") from error
    if not isinstance(payload, dict):
        raise LaunchSafetyError(f"{label} must contain a JSON object: {path}")
    return payload, payload_sha


def validate_staged_verification_receipt(
    result: dict[str, Any],
    session: CandidateLaunchSession,
    expected_final_output: Path,
    *,
    expected_finalization_sha256: str | None = None,
) -> StagedVerificationReceipt:
    if not isinstance(result, dict):
        raise LaunchSafetyError("Formal verifier did not return a staged receipt object")
    if result.get("status") != "staged" or result.get("passed") is not True:
        raise LaunchSafetyError("Formal verifier did not return a passing staged receipt")
    if not session.publication_ready or not session.publication_protections_held:
        raise LaunchSafetyError(
            "Candidate session must be cleaned and remain protected during evidence validation"
        )
    required_session_values = (
        session.candidate_exe,
        session.candidate_dir,
        session.candidate_sha256,
        session.candidate_tree_sha256,
        session.data_dir,
        session.webview_data_dir,
        session.sidecar_identity,
        session.process_identity,
    )
    if any(value is None for value in required_session_values):
        raise LaunchSafetyError("Candidate session identity is incomplete")

    expected_final = resolve_evidence_output_directory(
        expected_final_output,
        session.candidate_exe,  # type: ignore[arg-type]
    )
    try:
        staging = Path(str(result["staging_dir"]))
        result_final = Path(str(result["final_output_dir"]))
        receipt_path = Path(str(result["receipt_path"]))
    except (KeyError, TypeError, ValueError) as error:
        raise LaunchSafetyError("Staged receipt paths are missing or invalid") from error
    if not staging.is_absolute() or not result_final.is_absolute() or not receipt_path.is_absolute():
        raise LaunchSafetyError("Staged receipt paths must be absolute")
    if _normalized_path(result_final) != _normalized_path(expected_final):
        raise LaunchSafetyError("Verifier staged evidence for an unexpected final directory")
    if staging.parent.resolve(strict=True) != expected_final.parent:
        raise LaunchSafetyError("Evidence staging directory has an unexpected parent")
    if not staging.name.startswith(f".{expected_final.name}.incomplete-"):
        raise LaunchSafetyError("Evidence staging directory does not use the incomplete prefix")
    _require_regular_directory(staging, "Evidence staging directory")
    if _normalized_lexical_path(staging) != _normalized_path(staging):
        raise LaunchSafetyError("Evidence staging directory resolves through an alternate path")
    expected_receipt_path = staging / VERIFICATION_RECEIPT_NAME
    if _normalized_lexical_path(receipt_path) != _normalized_lexical_path(
        expected_receipt_path
    ):
        raise LaunchSafetyError("Verification receipt is outside its staging directory")

    expected_receipt_hash = validate_expected_sha256(
        str(result.get("receipt_sha256") or ""),
        "verification receipt",
    )
    payload, receipt_sha256 = _read_stable_json(
        receipt_path,
        "Verification receipt",
    )
    if receipt_sha256 != expected_receipt_hash:
        raise LaunchSafetyError("Verification receipt SHA-256 does not match staged metadata")
    if payload.get("format_version") != 3 or payload.get("passed") is not True:
        raise LaunchSafetyError("Verification receipt is not a passing format-v3 receipt")

    publication = payload.get("publication")
    if not isinstance(publication, dict) or any(
        (
            publication.get("state") != "staged",
            publication.get("requires_launcher_finalize") is not True,
            publication.get("staging_directory_name") != staging.name,
            publication.get("receipt_name") != VERIFICATION_RECEIPT_NAME,
            publication.get("launcher_finalization_name")
            != LAUNCHER_FINALIZATION_NAME,
        )
    ):
        raise LaunchSafetyError("Verification receipt publication contract is invalid")
    publication_final = Path(str(publication.get("final_output_dir") or ""))
    if not publication_final.is_absolute() or _normalized_path(
        publication_final
    ) != _normalized_path(expected_final):
        raise LaunchSafetyError("Verification receipt binds another final output directory")

    candidate = payload.get("candidate")
    if not isinstance(candidate, dict):
        raise LaunchSafetyError("Verification receipt has no candidate identity")
    candidate_exe = session.candidate_exe
    candidate_dir = session.candidate_dir
    sidecar_identity = session.sidecar_identity
    process_identity = session.process_identity
    if (
        _normalized_path(str(candidate.get("executable") or ""))
        != _normalized_path(candidate_exe)  # type: ignore[arg-type]
        or _normalized_path(str(candidate.get("candidate_root") or ""))
        != _normalized_path(candidate_dir)  # type: ignore[arg-type]
        or candidate.get("git_commit") != session.expected_git_commit
        or candidate.get("app_sha256") != session.candidate_sha256
        or candidate.get("tree_sha256") != session.candidate_tree_sha256
    ):
        raise LaunchSafetyError("Verification receipt candidate identity does not match session")
    artifacts = candidate.get("artifacts")
    if not isinstance(artifacts, dict) or (
        artifacts.get("sidecar_sha256")
        != sidecar_identity.sha256  # type: ignore[union-attr]
        or artifacts.get("manifest_sha256")
        != sidecar_identity.manifest_sha256  # type: ignore[union-attr]
    ):
        raise LaunchSafetyError("Verification receipt sidecar identity does not match session")

    app_process = payload.get("app_process")
    if not isinstance(app_process, dict):
        raise LaunchSafetyError("Verification receipt process identity does not match session")
    receipt_create_time = _receipt_process_create_time(app_process.get("create_time"))
    if (
        app_process.get("pid") != process_identity.pid  # type: ignore[union-attr]
        or receipt_create_time != process_identity.create_time  # type: ignore[union-attr]
        or _normalized_path(str(app_process.get("executable") or ""))
        != _normalized_path(candidate_exe)  # type: ignore[arg-type]
    ):
        raise LaunchSafetyError("Verification receipt process identity does not match session")

    isolation = payload.get("isolation")
    if not isinstance(isolation, dict):
        raise LaunchSafetyError("Verification receipt has no isolation identity")
    if (
        _normalized_lexical_path(str(isolation.get("data_root") or ""))
        != _normalized_lexical_path(session.data_dir)  # type: ignore[arg-type]
        or _normalized_lexical_path(str(isolation.get("webview_data_root") or ""))
        != _normalized_lexical_path(session.webview_data_dir)  # type: ignore[arg-type]
    ):
        raise LaunchSafetyError("Verification receipt isolation paths do not match session")
    isolation_identities = isolation.get("identities")
    if not isinstance(isolation_identities, dict) or (
        isolation_identities.get("data_root")
        != _identity_payload(session.data_dir_identity)
        or isolation_identities.get("webview_data_root")
        != _identity_payload(session.webview_data_dir_identity)
    ):
        raise LaunchSafetyError("Verification receipt isolation file IDs do not match session")

    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        raise LaunchSafetyError("Verification receipt has no evidence inventory")
    parent_identity = _identity_payload(
        _path_identity(expected_final.parent, "evidence output parent")
    )
    staging_identity = _identity_payload(
        _path_identity(staging, "evidence staging directory")
    )
    if (
        evidence.get("output_parent") != str(expected_final.parent)
        or evidence.get("output_parent_identity") != parent_identity
        or evidence.get("staging_identity") != staging_identity
    ):
        raise LaunchSafetyError("Verification receipt evidence directory identity is invalid")

    screenshots = evidence.get("screenshots")
    if not isinstance(screenshots, list) or not screenshots:
        raise LaunchSafetyError("Verification receipt has no screenshot inventory")
    screenshot_names: set[str] = set()
    for record in screenshots:
        if not isinstance(record, dict):
            raise LaunchSafetyError("Verification screenshot record is invalid")
        name = str(record.get("relative_path") or "")
        relative = Path(name)
        if (
            not name
            or relative.is_absolute()
            or len(relative.parts) != 1
            or relative.name != name
            or relative.suffix.casefold() != ".png"
            or name in screenshot_names
        ):
            raise LaunchSafetyError("Verification screenshot path is invalid")
        screenshot_names.add(name)
        expected_hash = validate_expected_sha256(
            str(record.get("sha256") or ""),
            f"screenshot {name}",
        )
        try:
            expected_size = int(record.get("size_bytes", record.get("bytes", -1)))
        except (TypeError, ValueError) as error:
            raise LaunchSafetyError(f"Verification screenshot size is invalid: {name}") from error
        actual_size, actual_hash = portable_release._stable_file_record(
            staging / name,
            f"Verification screenshot {name}",
        )
        if actual_size != expected_size or actual_hash != expected_hash:
            raise LaunchSafetyError(f"Verification screenshot identity changed: {name}")

    expected_names = screenshot_names | {VERIFICATION_RECEIPT_NAME}
    if expected_finalization_sha256 is not None:
        expected_names.add(LAUNCHER_FINALIZATION_NAME)
        actual_size, actual_hash = portable_release._stable_file_record(
            staging / LAUNCHER_FINALIZATION_NAME,
            "Launcher finalization",
        )
        if actual_size <= 0 or actual_hash != expected_finalization_sha256:
            raise LaunchSafetyError("Launcher finalization identity changed before publication")
    try:
        actual_names = {entry.name for entry in staging.iterdir()}
    except OSError as error:
        raise LaunchSafetyError(f"Could not enumerate evidence staging: {error}") from error
    if actual_names != expected_names:
        raise LaunchSafetyError("Evidence staging contains an unexpected or missing entry")

    return StagedVerificationReceipt(
        staging_dir=staging,
        final_output_dir=expected_final,
        receipt_path=receipt_path,
        receipt_sha256=receipt_sha256,
        payload=payload,
        screenshot_names=tuple(sorted(screenshot_names)),
    )


def _write_exclusive_json(path: Path, payload: dict[str, Any]) -> str:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    try:
        with path.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise LaunchSafetyError(f"Could not write launcher finalization: {path}: {error}") from error
    size, sha256 = portable_release._stable_file_record(path, "Launcher finalization")
    expected_hash = hashlib.sha256(encoded).hexdigest().upper()
    if size != len(encoded) or sha256 != expected_hash:
        raise LaunchSafetyError("Launcher finalization changed after its exclusive write")
    return sha256


def _rename_directory_no_replace(source: Path, destination: Path) -> None:
    _require_regular_directory(source, "Evidence staging directory")
    if source.parent != destination.parent:
        raise LaunchSafetyError("Evidence staging and final directory must share a parent")
    if destination.exists() or _is_link_like(destination):
        raise LaunchSafetyError(f"Refusing to overwrite formal evidence: {destination}")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        move_file = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move_file.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        move_file.restype = wintypes.BOOL
        movefile_write_through = 0x00000008
        if not move_file(str(source), str(destination), movefile_write_through):
            raise LaunchSafetyError(
                f"Could not atomically publish formal evidence: "
                f"{ctypes.WinError(ctypes.get_last_error())}"
            )
        return
    try:
        os.rename(source, destination)
    except OSError as error:
        raise LaunchSafetyError(
            f"Could not atomically publish formal evidence: {error}"
        ) from error


def _quarantine_failed_publication(final_output: Path) -> Path:
    _require_regular_directory(final_output, "Published evidence directory")
    quarantine = final_output.with_name(
        f".{final_output.name}.failed-{uuid.uuid4().hex}"
    )
    _rename_directory_no_replace(final_output, quarantine)
    return quarantine


def finalize_staged_verification(
    result: dict[str, Any],
    session: CandidateLaunchSession,
    expected_final_output: Path,
) -> dict[str, Any]:
    staged = validate_staged_verification_receipt(
        result,
        session,
        expected_final_output,
    )
    inventory_before_finalization = directory_inventory(staged.staging_dir)
    finalization_payload = {
        "format_version": 1,
        "status": "finalized",
        "verification_receipt_sha256": staged.receipt_sha256,
        "candidate": {
            "executable": str(session.candidate_exe),
            "app_sha256": session.candidate_sha256,
            "tree_sha256": session.candidate_tree_sha256,
            "git_commit": session.expected_git_commit,
        },
        "sidecar": {
            "path": str(session.sidecar_identity.path),  # type: ignore[union-attr]
            "sha256": session.sidecar_identity.sha256,  # type: ignore[union-attr]
            "manifest_sha256": session.sidecar_identity.manifest_sha256,  # type: ignore[union-attr]
        },
        "process": {
            "pid": session.pid,
            "create_time": session.create_time,
            "cleaned": True,
        },
        "isolation": {
            "data_dir": str(session.data_dir),
            "webview_data_dir": str(session.webview_data_dir),
            "cleaned": True,
        },
        "candidate_reverified": True,
        "tree_lock_clean": True,
        "tree_lock_held_during_publication": True,
        "promotion_lock_held_during_publication": True,
        "staged_inventory": inventory_before_finalization,
        "publication": {
            "from": str(staged.staging_dir),
            "to": str(staged.final_output_dir),
            "no_overwrite": True,
        },
    }
    finalization_path = staged.staging_dir / LAUNCHER_FINALIZATION_NAME
    finalization_sha256 = _write_exclusive_json(
        finalization_path,
        finalization_payload,
    )
    staged = validate_staged_verification_receipt(
        result,
        session,
        expected_final_output,
        expected_finalization_sha256=finalization_sha256,
    )
    final_inventory = directory_inventory(staged.staging_dir)
    finalized_result = {
        "status": "finalized",
        "passed": True,
        "final_output_dir": str(staged.final_output_dir),
        "receipt_path": str(staged.final_output_dir / VERIFICATION_RECEIPT_NAME),
        "receipt_sha256": staged.receipt_sha256,
        "launcher_finalization_path": str(
            staged.final_output_dir / LAUNCHER_FINALIZATION_NAME
        ),
        "launcher_finalization_sha256": finalization_sha256,
        "final_inventory": final_inventory,
    }
    _rename_directory_no_replace(staged.staging_dir, staged.final_output_dir)
    return finalized_result


def launch_verify_and_finalize(
    *,
    executable: Path,
    expected_git_commit: str,
    expected_app_sha256: str,
    expected_tree_sha256: str,
    cdp_port: int,
    monitor_index: int,
    expected_dpi: int,
    output_dir: Path,
    sizes: Sequence[tuple[int, int]] | None = None,
    profiles: Sequence[str] | None = None,
    surfaces: Sequence[str] | None = None,
) -> dict[str, Any]:
    validated_port = validate_cdp_port(cdp_port)
    if validated_port is None:
        raise LaunchSafetyError("Formal WebView verification requires a CDP port")
    if isinstance(monitor_index, bool) or not isinstance(monitor_index, int):
        raise LaunchSafetyError("Monitor index must be an integer")
    if isinstance(expected_dpi, bool) or not isinstance(expected_dpi, int) or expected_dpi <= 0:
        raise LaunchSafetyError("Expected DPI must be a positive integer")

    candidate_executable = resolve_candidate_executable(executable)
    final_output = resolve_evidence_output_directory(output_dir, candidate_executable)
    session = CandidateLaunchSession(
        executable=candidate_executable,
        expected_git_commit=expected_git_commit,
        expected_app_sha256=expected_app_sha256,
        expected_tree_sha256=expected_tree_sha256,
        cdp_port=validated_port,
        seed_review_fixture=True,
    )
    staged_result: dict[str, Any] | None = None
    finalized_result: dict[str, Any] | None = None
    try:
        with session:
            from verify_formal_webview import (
                DEFAULT_PROFILES,
                DEFAULT_SIZES,
                DEFAULT_SURFACES,
                run_formal_webview_verification,
            )

            args = argparse.Namespace(
                exe=session.candidate_exe,
                expected_git_commit=session.expected_git_commit,
                expected_app_sha256=session.candidate_sha256,
                expected_tree_sha256=session.candidate_tree_sha256,
                pid=session.pid,
                expected_create_time=session.create_time,
                cdp_port=validated_port,
                isolated_data_dir=session.data_dir,
                monitor_index=monitor_index,
                expected_dpi=expected_dpi,
                output_dir=final_output,
                sizes=tuple(sizes) if sizes is not None else tuple(DEFAULT_SIZES),
                profiles=tuple(profiles) if profiles is not None else tuple(DEFAULT_PROFILES),
                surfaces=tuple(surfaces) if surfaces is not None else tuple(DEFAULT_SURFACES),
            )
            staged_result = run_formal_webview_verification(args)
            session.prepare_for_publication()
            finalized_result = finalize_staged_verification(
                staged_result,
                session,
                final_output,
            )
    except BaseException as error:
        if finalized_result is not None:
            try:
                quarantine = _quarantine_failed_publication(final_output)
            except Exception as quarantine_error:
                raise LaunchSafetyError(
                    "Formal session failed after publication and the final evidence "
                    f"could not be quarantined: primary={error}; "
                    f"quarantine={quarantine_error}"
                ) from error
            raise LaunchSafetyError(
                "Formal session failed after publication; evidence was quarantined at "
                f"{quarantine}: {error}"
            ) from error
        if isinstance(error, ReleaseError):
            raise LaunchSafetyError(f"Portable promotion lock failed: {error}") from error
        raise

    if staged_result is None or finalized_result is None or not session.closed_cleanly:
        raise LaunchSafetyError("Formal candidate session did not produce staged evidence")
    return finalized_result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a verified Product Atelier candidate capture or formal WebView gate"
    )
    parser.add_argument("--exe", required=True, help="Absolute candidate EXE path")
    parser.add_argument("--output", help="Absolute screenshot PNG path")
    parser.add_argument(
        "--expected-git-commit",
        required=True,
        help="Full 40-character Git commit expected in the candidate sidecar manifest",
    )
    parser.add_argument(
        "--expected-app-sha256",
        required=True,
        help="Full 64-character SHA-256 expected for the candidate app executable",
    )
    parser.add_argument(
        "--expected-tree-sha256",
        required=True,
        help="Full 64-character SHA-256 expected for the complete candidate directory inventory",
    )
    parser.add_argument("--wait-seconds", type=float, default=8.0)
    parser.add_argument("--pad", type=int, default=30)
    parser.add_argument(
        "--cdp-port",
        type=int,
        help="Optional isolated WebView2 remote-debugging port (1-65535)",
    )
    parser.add_argument(
        "--seed-review-fixture",
        action="store_true",
        help="Seed the isolated runtime with deterministic Result Review data",
    )
    parser.add_argument(
        "--formal-webview",
        action="store_true",
        help="Run the complete formal WebView gate and publish finalized evidence",
    )
    parser.add_argument("--formal-output-dir", help="Absolute final evidence directory")
    parser.add_argument("--monitor-index", type=int)
    parser.add_argument("--expected-dpi", type=int)
    parser.add_argument("--formal-sizes", nargs="+")
    parser.add_argument("--formal-profiles", nargs="+")
    parser.add_argument("--formal-surfaces", nargs="+")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.wait_seconds < 0:
        parser.error("--wait-seconds must be non-negative")
    if args.pad < 0:
        parser.error("--pad must be non-negative")
    try:
        validate_cdp_port(args.cdp_port)
    except LaunchSafetyError as error:
        parser.error(str(error))

    if args.formal_webview:
        if args.output:
            parser.error("--output is only valid for the screenshot mode")
        if not args.formal_output_dir:
            parser.error("--formal-output-dir is required with --formal-webview")
        if args.cdp_port is None:
            parser.error("--cdp-port is required with --formal-webview")
        if args.monitor_index is None:
            parser.error("--monitor-index is required with --formal-webview")
        if args.expected_dpi is None:
            parser.error("--expected-dpi is required with --formal-webview")
    elif not args.output:
        parser.error("--output is required unless --formal-webview is used")

    try:
        if args.formal_webview:
            parsed_sizes = None
            if args.formal_sizes is not None:
                from verify_formal_webview import parse_size

                parsed_sizes = tuple(parse_size(value) for value in args.formal_sizes)
            result = launch_verify_and_finalize(
                executable=Path(args.exe),
                expected_git_commit=args.expected_git_commit,
                expected_app_sha256=args.expected_app_sha256,
                expected_tree_sha256=args.expected_tree_sha256,
                cdp_port=args.cdp_port,
                monitor_index=args.monitor_index,
                expected_dpi=args.expected_dpi,
                output_dir=Path(args.formal_output_dir),
                sizes=parsed_sizes,
                profiles=args.formal_profiles,
                surfaces=args.formal_surfaces,
            )
        else:
            result = launch_and_capture(
                executable=Path(args.exe),
                output_path=Path(args.output),
                expected_git_commit=args.expected_git_commit,
                expected_app_sha256=args.expected_app_sha256,
                expected_tree_sha256=args.expected_tree_sha256,
                wait_seconds=args.wait_seconds,
                padding=args.pad,
                cdp_port=args.cdp_port,
                seed_review_fixture=args.seed_review_fixture,
            )
    except KeyboardInterrupt:
        print("Interrupted after cleaning up the launched candidate.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if args.formal_webview:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Saved: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
