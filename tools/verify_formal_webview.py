#!/usr/bin/env python3
"""Exercise a packaged Product Atelier WebView through its read-only CDP port."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import stat
import statistics
import tempfile
import time
import urllib.request
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import psutil
import websocket
from portable_release import APP_NAME, ReleaseError, validate_candidate

SW_RESTORE = 9
SWP_SHOWWINDOW = 0x0040

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_RELATIVE_ROOT = Path("build") / "portable-candidate-current"
FORMAL_RELEASE_RELATIVE_ROOT = Path("release") / "ProductAtelier-Portable"
FORMAL_RELEASE_MARKER = ("release", "productatelier-portable")
WEBVIEW_DATA_DIRECTORY_NAME = "webview2-user-data"
KNOWLEDGE_DIRECTORY_NAME = "no-knowledge-vault"
LEGACY_CONFIG_SENTINEL_NAME = "no-legacy-config.json"
ISOLATED_DATA_PREFIX = "ProductAtelier-launch-and-shoot-"
RECEIPT_NAME = "verification-receipt.json"
FAILURE_MARKER_NAME = "verification-failure.json"
LAUNCHER_FINALIZATION_NAME = "launcher-finalization.json"
PROCESS_CREATE_TIME_TOLERANCE_SECONDS = 0.01
SENSITIVE_CONFIG_FIELDS = frozenset({"api_key", "apikey", "token", "secret", "password"})

USER32: Any | None = None
WINTYPES: Any | None = None
FIND_PROCESS_WINDOW: Any | None = None
GET_MONITORS_INFO: Any | None = None

DEFAULT_SIZES = ((960, 600), (1280, 720), (1440, 900))
DEFAULT_PROFILES = ("light", "dark", "high")
DEFAULT_SURFACES = (
    "single",
    "multi-file",
    "group-split",
    "cutout-batch",
    "infinite-canvas-library",
    "result-review",
    "task-center",
    "growth",
)

DANGEROUS_SELECTORS = (
    "#btn-generate",
    "#btn-retry",
    "#btn-result-next",
    "#btn-save-all",
    "#btn-adopt",
    "#btn-feedback",
    "#btn-review-adjust",
    "#btn-review-record",
    "#btn-review-suggest",
    "#btn-clear",
    "#btn-new-session",
    "#btn-folder-load",
    "#btn-save-key",
    "#btn-save-settings",
    "#btn-reload-knowledge",
    "#btn-select-output-root",
    "#btn-select-grounding-runtime",
    "#btn-select-grounding-model",
    "#btn-verify-grounding-pack",
    "#btn-disable-grounding-pack",
    "#btn-send-result-canvas",
    "#btn-spatial-new",
    "#btn-spatial-empty-new",
    "#btn-spatial-rename",
    "#spatial-rename-form",
    "#asset-purge-action",
    "#asset-bulk-action",
    "[data-remove-asset-id]",
    "[data-memory-action]",
    "[data-send-asset-canvas]",
    "[data-asset-canvas-id]",
    "[data-job-action='retry-item']",
    "[data-job-action='retry-failed']",
    "[data-job-action='pause']",
    "[data-job-action='resume']",
    "[data-job-action='cancel']",
    "[data-job-action='send-canvas']",
    "[data-job-action='open-video-canvas']",
    "[data-purge-asset]",
    "[data-spatial-open]",
    "[data-spatial-rename]",
    "[data-spatial-retry]",
    "[data-spatial-conflict-discard]",
    "[data-spatial-action]",
    "[data-spatial-video-form]",
)

READ_ONLY_GUARD_EVENT_TYPES = ("click", "submit")

INFINITE_CANVAS_READY_EXPRESSION = r"""
  (() => {
    const visible = (element) => element
      && !element.closest('[hidden]')
      && !element.closest('[inert]')
      && element.getClientRects().length > 0;
    const entry = document.querySelector("[data-page='canvas']");
    const page = document.querySelector('#page-canvas');
    const library = document.querySelector('#spatial-library');
    const editor = document.querySelector('#spatial-editor');
    const empty = document.querySelector('#spatial-library-empty');
    const list = document.querySelector('#spatial-canvas-list');
    const countText = document.querySelector('#spatial-canvas-count')?.textContent || '';
    const statusText = document.querySelector('#spatial-save-state')?.textContent || '';
    const count = Number.parseInt(countText, 10);
    const cardCount = document.querySelectorAll('[data-spatial-record]').length;
    const emptyReady = count === 0 && visible(empty) && Boolean(list?.hidden);
    const listReady = count > 0 && Boolean(empty?.hidden) && visible(list) && cardCount === count;
    const ready = visible(entry)
      && entry.classList.contains('active')
      && entry.getAttribute('aria-current') === 'page'
      && entry.getAttribute('aria-label') === '\u65e0\u9650\u753b\u5e03'
      && visible(page)
      && page.classList.contains('active')
      && visible(library)
      && Boolean(editor?.hidden)
      && Number.isInteger(count)
      && (emptyReady || listReady)
      && statusText.endsWith('\u00b7 \u5df2\u540c\u6b65')
      && document.documentElement.dataset.spatialRuntime !== 'loaded';
    return ready ? {
      entry: entry.getAttribute('aria-label'),
      pageVisible: true,
      libraryState: emptyReady ? 'empty' : 'list',
      canvasCount: count,
      cardCount,
      status: statusText,
      editorHidden: true,
      runtimeState: document.documentElement.dataset.spatialRuntime || 'not-loaded',
    } : false;
  })()
"""

WINDOWS_VIRTUAL_KEYS = {
    "Tab": 9,
    "Enter": 13,
    "Escape": 27,
    "Home": 36,
    "ArrowLeft": 37,
    "ArrowRight": 39,
    "End": 35,
}

KEY_CHARACTER_TEXT = {
    "Enter": "\r",
}


class VerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class FilesystemIdentity:
    st_dev: int
    st_ino: int


@dataclass(frozen=True)
class CandidateBinding:
    project_root: Path
    candidate_root: Path
    executable: Path
    git_commit: str
    app_sha256: str
    tree_sha256: str
    candidate: dict[str, Any]
    candidate_root_identity: FilesystemIdentity
    executable_identity: FilesystemIdentity


@dataclass(frozen=True)
class IsolationBinding:
    temp_root: Path
    data_root: Path
    webview_data_root: Path
    knowledge_root: Path
    legacy_config_path: Path
    real_product_atelier_data_root: Path
    temp_root_identity: FilesystemIdentity
    data_root_identity: FilesystemIdentity
    webview_data_root_identity: FilesystemIdentity
    knowledge_root_identity: FilesystemIdentity


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    create_time: float
    executable: Path


@dataclass(frozen=True)
class BrowserProof:
    cdp_port: int
    identity: ProcessIdentity
    app_identity: ProcessIdentity
    listener_addresses: tuple[str, ...]
    ancestry: tuple[ProcessIdentity, ...]
    command_line_proof: dict[str, str]


@dataclass(frozen=True)
class EvidenceStagingBinding:
    output_parent: Path
    final_output_dir: Path
    staging_dir: Path
    output_parent_identity: FilesystemIdentity
    staging_identity: FilesystemIdentity


def _normalized_path(path: str | os.PathLike[str]) -> str:
    value = os.fspath(path)
    if os.name == "nt":
        folded = value.casefold()
        if folded.startswith("\\\\?\\unc\\"):
            value = "\\\\" + value[8:]
        elif folded.startswith("\\\\?\\"):
            value = value[4:]
    resolved = Path(value).resolve(strict=False)
    return os.path.normcase(str(resolved)).casefold()


def _same_path(left: str | os.PathLike[str], right: str | os.PathLike[str]) -> bool:
    return _normalized_path(left) == _normalized_path(right)


def _is_within(path: Path, root: Path) -> bool:
    resolved_path = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    return resolved_path == resolved_root or resolved_path.is_relative_to(resolved_root)


def _paths_overlap(left: Path, right: Path) -> bool:
    return _is_within(left, right) or _is_within(right, left)


def _contains_formal_release_marker(path: Path) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    marker_length = len(FORMAL_RELEASE_MARKER)
    return any(
        parts[index : index + marker_length] == FORMAL_RELEASE_MARKER
        for index in range(len(parts) - marker_length + 1)
    )


def _is_link_like(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag)


def _require_no_reparse_components(path: Path, label: str) -> None:
    absolute = Path(os.path.abspath(path))
    for component in reversed((absolute, *absolute.parents)):
        try:
            component.lstat()
        except OSError as error:
            raise VerificationError(f"{label} is unavailable: {component}: {error}") from error
        if _is_link_like(component):
            raise VerificationError(f"{label} must not traverse a reparse point: {component}")


def _filesystem_identity(path: Path, label: str) -> FilesystemIdentity:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise VerificationError(f"Could not bind {label} identity: {path}: {error}") from error
    identity = FilesystemIdentity(st_dev=int(metadata.st_dev), st_ino=int(metadata.st_ino))
    if identity.st_ino <= 0:
        raise VerificationError(f"{label} does not expose a stable filesystem identity: {path}")
    return identity


def _require_regular_directory(path: Path, label: str) -> Path:
    _require_no_reparse_components(path, label)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise VerificationError(f"{label} is unavailable: {path}: {error}") from error
    if _is_link_like(path) or _is_link_like(resolved) or not resolved.is_dir():
        raise VerificationError(f"{label} must be a regular directory: {path}")
    return resolved


def _require_regular_file(path: Path, label: str) -> Path:
    _require_no_reparse_components(path, label)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise VerificationError(f"{label} is unavailable: {path}: {error}") from error
    if _is_link_like(path) or _is_link_like(resolved) or not resolved.is_file():
        raise VerificationError(f"{label} must be a regular file: {path}")
    return resolved


def _validate_git_commit(value: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", normalized):
        raise VerificationError("Expected Git commit must be a full 40-character hash")
    return normalized


def _validate_sha256(value: str, label: str) -> str:
    normalized = value.strip().upper()
    if not re.fullmatch(r"[0-9A-F]{64}", normalized):
        raise VerificationError(f"Expected {label} SHA-256 must be a full 64-character hash")
    return normalized


def validate_candidate_binding(
    executable_value: str | os.PathLike[str],
    *,
    expected_git_commit: str,
    expected_app_sha256: str,
    expected_tree_sha256: str,
    project_root: Path = PROJECT_ROOT,
) -> CandidateBinding:
    requested = Path(executable_value)
    if not requested.is_absolute():
        raise VerificationError("Candidate App path must be absolute")
    project = _require_regular_directory(project_root, "Project root")
    candidate_root = _require_regular_directory(
        project / CANDIDATE_RELATIVE_ROOT,
        "Canonical candidate root",
    )
    expected_executable = _require_regular_file(
        candidate_root / APP_NAME,
        "Canonical candidate App",
    )
    try:
        executable = requested.resolve(strict=True)
    except OSError as error:
        raise VerificationError(f"Candidate App is unavailable: {requested}: {error}") from error
    if _contains_formal_release_marker(requested) or _contains_formal_release_marker(executable):
        raise VerificationError("Refusing to verify the formal portable release")
    if not _same_path(executable, expected_executable):
        raise VerificationError(
            f"Candidate App must use the canonical path: {expected_executable}"
        )

    git_commit = _validate_git_commit(expected_git_commit)
    app_sha256 = _validate_sha256(expected_app_sha256, "candidate App")
    tree_sha256 = _validate_sha256(expected_tree_sha256, "candidate tree")
    try:
        candidate = validate_candidate(candidate_root, git_commit)
    except (OSError, ReleaseError, TypeError, ValueError) as error:
        raise VerificationError(f"Candidate release validation failed: {error}") from error
    actual_app_sha256 = str(candidate.get("artifacts", {}).get("app_sha256") or "").upper()
    actual_tree_sha256 = str(candidate.get("inventory", {}).get("tree_sha256") or "").upper()
    if actual_app_sha256 != app_sha256:
        raise VerificationError("Candidate App SHA-256 does not match the expected identity")
    if actual_tree_sha256 != tree_sha256:
        raise VerificationError("Candidate tree SHA-256 does not match the expected identity")
    return CandidateBinding(
        project_root=project,
        candidate_root=candidate_root,
        executable=expected_executable,
        git_commit=git_commit,
        app_sha256=app_sha256,
        tree_sha256=tree_sha256,
        candidate=candidate,
        candidate_root_identity=_filesystem_identity(candidate_root, "candidate root"),
        executable_identity=_filesystem_identity(expected_executable, "candidate App"),
    )


def assert_candidate_binding(binding: CandidateBinding) -> CandidateBinding:
    current = validate_candidate_binding(
        binding.executable,
        expected_git_commit=binding.git_commit,
        expected_app_sha256=binding.app_sha256,
        expected_tree_sha256=binding.tree_sha256,
        project_root=binding.project_root,
    )
    if current != binding:
        raise VerificationError("Candidate identity changed during formal WebView verification")
    return current


def _credential_fields(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            current = f"{prefix}.{key}" if prefix else str(key)
            if str(key).casefold() in SENSITIVE_CONFIG_FIELDS and str(item or "").strip():
                found.append(current)
            found.extend(_credential_fields(item, current))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_credential_fields(item, f"{prefix}[{index}]"))
    return found


def credential_isolation_snapshot(binding: IsolationBinding) -> dict[str, Any]:
    config_path = binding.data_root / "config.json"
    if config_path.exists() or _is_link_like(config_path):
        config_path = _require_regular_file(config_path, "Isolated config")
        try:
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, TypeError, ValueError) as error:
            raise VerificationError(f"Isolated config is invalid: {config_path}: {error}") from error
        sensitive_fields = _credential_fields(config)
        if sensitive_fields:
            raise VerificationError(
                "Isolated config contains configured credential fields: "
                + ", ".join(sorted(sensitive_fields))
            )
    else:
        sensitive_fields = []
    if binding.legacy_config_path.exists() or _is_link_like(binding.legacy_config_path):
        raise VerificationError(
            f"Legacy credential sentinel must remain absent: {binding.legacy_config_path}"
        )
    return {
        "config_exists": config_path.is_file(),
        "configured_credential_fields": sensitive_fields,
        "legacy_config_absent": True,
    }


def validate_isolation_binding(
    data_root_value: str | os.PathLike[str],
    candidate: CandidateBinding,
) -> IsolationBinding:
    requested = Path(data_root_value)
    if not requested.is_absolute():
        raise VerificationError("Isolated data root must be absolute")
    temp_root = _require_regular_directory(
        Path(tempfile.gettempdir()),
        "Launcher temp root",
    )
    data_root = _require_regular_directory(requested, "Isolated data root")
    appdata_value = str(os.environ.get("APPDATA") or "").strip()
    appdata_root = (
        Path(appdata_value)
        if appdata_value
        else Path.home() / "AppData" / "Roaming"
    )
    real_data_root = (appdata_root / "ProductAtelier").resolve(strict=False)
    protected_roots = {
        "candidate": candidate.candidate_root,
        "formal release": candidate.project_root / FORMAL_RELEASE_RELATIVE_ROOT,
        "project": candidate.project_root,
        "real Product Atelier APPDATA": real_data_root,
    }
    overlaps = [
        label
        for label, protected_root in protected_roots.items()
        if _paths_overlap(data_root, protected_root)
    ]
    if overlaps:
        raise VerificationError(
            "Isolated data root overlaps a protected boundary in either direction: "
            + ", ".join(overlaps)
        )
    suffix = data_root.name[len(ISOLATED_DATA_PREFIX) :]
    if (
        not _same_path(data_root.parent, temp_root)
        or not data_root.name.startswith(ISOLATED_DATA_PREFIX)
        or not suffix
        or suffix.casefold().startswith("cleanup-")
    ):
        raise VerificationError(
            "Isolated data root must be a launcher-created direct child of the system "
            f"temp root with prefix {ISOLATED_DATA_PREFIX!r}"
        )

    webview_data_root = _require_regular_directory(
        data_root / WEBVIEW_DATA_DIRECTORY_NAME,
        "Isolated WebView2 data root",
    )
    knowledge_root = _require_regular_directory(
        data_root / KNOWLEDGE_DIRECTORY_NAME,
        "Isolated knowledge root",
    )
    binding = IsolationBinding(
        temp_root=temp_root,
        data_root=data_root,
        webview_data_root=webview_data_root,
        knowledge_root=knowledge_root,
        legacy_config_path=data_root / LEGACY_CONFIG_SENTINEL_NAME,
        real_product_atelier_data_root=real_data_root,
        temp_root_identity=_filesystem_identity(temp_root, "launcher temp root"),
        data_root_identity=_filesystem_identity(data_root, "isolated data root"),
        webview_data_root_identity=_filesystem_identity(
            webview_data_root,
            "isolated WebView2 data root",
        ),
        knowledge_root_identity=_filesystem_identity(
            knowledge_root,
            "isolated knowledge root",
        ),
    )
    credential_isolation_snapshot(binding)
    return binding


def assert_isolation_binding(
    binding: IsolationBinding,
    candidate: CandidateBinding,
) -> IsolationBinding:
    current = validate_isolation_binding(binding.data_root, candidate)
    if current != binding:
        raise VerificationError("Isolated data directory identity changed during verification")
    return current


def _read_process_identity(process: Any) -> ProcessIdentity:
    try:
        pid = int(process.pid)
        create_time = float(process.create_time())
        executable = Path(process.exe()).resolve(strict=True)
        running = bool(process.is_running())
    except (OSError, ValueError, psutil.Error) as error:
        raise VerificationError(f"Could not read process identity: {error}") from error
    if not running:
        raise VerificationError(f"Process {pid} is not running")
    return ProcessIdentity(pid=pid, create_time=create_time, executable=executable)


def _validate_process_environment(process: Any, isolation: IsolationBinding) -> None:
    try:
        environment = process.environ()
    except (OSError, psutil.Error) as error:
        raise VerificationError(f"Could not verify candidate process environment: {error}") from error
    expected_paths = {
        "PRODUCT_ATELIER_DATA_DIR": isolation.data_root,
        "PRODUCT_ATELIER_LEGACY_CONFIG": isolation.legacy_config_path,
        "PRODUCT_ATELIER_KNOWLEDGE_BASE": isolation.knowledge_root,
        "PRODUCT_ATELIER_WEBVIEW_DATA_DIR": isolation.webview_data_root,
        "WEBVIEW2_USER_DATA_FOLDER": isolation.webview_data_root,
    }
    if str(environment.get("PRODUCT_ATELIER_CANDIDATE_ISOLATION") or "") != "1":
        raise VerificationError("Candidate process does not prove candidate isolation mode")
    for name, expected in expected_paths.items():
        actual = str(environment.get(name) or "").strip()
        if not actual or not Path(actual).is_absolute() or not _same_path(actual, expected):
            raise VerificationError(
                f"Candidate process does not prove isolated environment {name}={expected}"
            )


def validate_app_process_identity(
    pid: int,
    expected_create_time: float,
    candidate: CandidateBinding,
    isolation: IsolationBinding,
    *,
    process_factory: Callable[[int], Any] = psutil.Process,
) -> ProcessIdentity:
    if pid <= 0 or not math.isfinite(expected_create_time) or expected_create_time <= 0:
        raise VerificationError("Candidate PID and create time must be positive")
    try:
        process = process_factory(pid)
    except (LookupError, OSError, TypeError, ValueError, psutil.Error) as error:
        raise VerificationError(f"Candidate process {pid} is unavailable: {error}") from error
    identity = _read_process_identity(process)
    if identity.pid != pid:
        raise VerificationError("Candidate process factory returned another PID")
    if abs(identity.create_time - expected_create_time) > PROCESS_CREATE_TIME_TOLERANCE_SECONDS:
        raise VerificationError("Candidate App PID create time changed or was reused")
    if not _same_path(identity.executable, candidate.executable):
        raise VerificationError("Candidate App PID executable is not the bound candidate App")
    assert_isolation_binding(isolation, candidate)
    _validate_process_environment(process, isolation)
    return identity


def _command_line_option(command_line: Sequence[str], name: str) -> str | None:
    values = _command_line_options(command_line, name)
    return values[0] if values else None


def _command_line_options(command_line: Sequence[str], name: str) -> list[str]:
    prefix = f"--{name}="
    flag = f"--{name}"
    values: list[str] = []
    for index, argument in enumerate(command_line):
        if argument.startswith(prefix):
            values.append(argument[len(prefix) :])
        if argument == flag and index + 1 < len(command_line):
            values.append(command_line[index + 1])
    return values


def _is_literal_loopback(host: str | None) -> bool:
    return host in {"127.0.0.1", "::1"}


def _validate_webview_user_data_directory(
    value: str,
    isolation: IsolationBinding,
) -> Path:
    requested = Path(unquote(value))
    if not requested.is_absolute():
        raise VerificationError("WebView2 command line does not bind the isolated user-data root")
    if _same_path(requested, isolation.webview_data_root):
        return isolation.webview_data_root

    # Current Edge WebView2 runtimes append this fixed profile directory to
    # the data_directory supplied by Tauri. Keep the allowance exact: no
    # arbitrary descendant, alternate leaf name, or reparse point is valid.
    expected_profile = isolation.webview_data_root / "EBWebView"
    if not _same_path(requested, expected_profile):
        raise VerificationError("WebView2 command line does not bind the isolated user-data root")
    profile = _require_regular_directory(
        requested,
        "Isolated Edge WebView2 profile directory",
    )
    if (
        profile.name.casefold() != "ebwebview"
        or not _same_path(profile.parent, isolation.webview_data_root)
    ):
        raise VerificationError("WebView2 command line does not bind the isolated user-data root")
    return profile


def _listener_address(connection: Any) -> tuple[str, int] | None:
    local = getattr(connection, "laddr", None)
    if not local:
        return None
    try:
        if hasattr(local, "ip") and hasattr(local, "port"):
            return str(local.ip), int(local.port)
        if isinstance(local, tuple) and len(local) >= 2:
            return str(local[0]), int(local[1])
    except (TypeError, ValueError) as error:
        raise VerificationError(f"CDP listener has an invalid local address: {local}") from error
    return None


def prove_cdp_browser_process(
    cdp_port: int,
    app_identity: ProcessIdentity,
    candidate: CandidateBinding,
    isolation: IsolationBinding,
    *,
    connections: Iterable[Any] | None = None,
    process_factory: Callable[[int], Any] = psutil.Process,
) -> BrowserProof:
    if not 1 <= cdp_port <= 65535:
        raise VerificationError("CDP port must be between 1 and 65535")
    try:
        connection_rows = list(connections) if connections is not None else psutil.net_connections(kind="tcp")
    except (OSError, psutil.Error) as error:
        raise VerificationError(f"Could not inspect the CDP listener: {error}") from error
    listeners: list[tuple[int, str]] = []
    for connection in connection_rows:
        address = _listener_address(connection)
        status = str(getattr(connection, "status", "")).upper()
        pid = getattr(connection, "pid", None)
        if address is None or address[1] != cdp_port or status != "LISTEN":
            continue
        if not _is_literal_loopback(address[0]):
            raise VerificationError("CDP listener is exposed beyond loopback")
        if pid is None:
            raise VerificationError("CDP loopback listener owner PID is unavailable")
        try:
            listener_pid = int(pid)
        except (TypeError, ValueError) as error:
            raise VerificationError("CDP loopback listener owner PID is invalid") from error
        if listener_pid <= 0:
            raise VerificationError("CDP loopback listener owner PID is invalid")
        listeners.append((listener_pid, f"{address[0]}:{address[1]}"))
    listener_pids = {pid for pid, _address in listeners}
    if len(listener_pids) != 1:
        raise VerificationError(
            f"Expected one loopback CDP listener owner, found {sorted(listener_pids)}"
        )
    browser_pid = next(iter(listener_pids))
    try:
        browser_process = process_factory(browser_pid)
        browser_identity = _read_process_identity(browser_process)
        browser_name = str(browser_process.name() or "").casefold()
        command_line = list(browser_process.cmdline())
    except (LookupError, OSError, TypeError, ValueError, psutil.Error) as error:
        raise VerificationError(f"Could not inspect WebView2 browser process: {error}") from error
    if browser_name != "msedgewebview2.exe" or browser_identity.executable.name.casefold() != browser_name:
        raise VerificationError("CDP listener owner is not Microsoft Edge WebView2")
    if browser_identity.create_time + PROCESS_CREATE_TIME_TOLERANCE_SECONDS < app_identity.create_time:
        raise VerificationError("CDP browser predates the bound candidate App process")

    remote_ports = _command_line_options(command_line, "remote-debugging-port")
    user_data_dirs = _command_line_options(command_line, "user-data-dir")
    webview_exe_names = _command_line_options(command_line, "webview-exe-name")
    remote_addresses = _command_line_options(command_line, "remote-debugging-address")
    if len(remote_ports) != 1 or len(user_data_dirs) != 1:
        raise VerificationError(
            "WebView2 command line must unambiguously bind one CDP port and user-data root"
        )
    if len(webview_exe_names) > 1 or len(remote_addresses) > 1:
        raise VerificationError("WebView2 command line contains ambiguous CDP identity options")
    remote_port = remote_ports[0]
    user_data_dir = user_data_dirs[0]
    webview_exe_name = webview_exe_names[0] if webview_exe_names else None
    remote_address = remote_addresses[0] if remote_addresses else ""
    if remote_port != str(cdp_port):
        raise VerificationError("WebView2 command line does not bind the expected CDP port")
    if remote_address and not _is_literal_loopback(remote_address):
        raise VerificationError("WebView2 command line does not bind a literal loopback CDP host")
    if not user_data_dir:
        raise VerificationError("WebView2 command line does not bind the isolated user-data root")
    proven_user_data_dir = _validate_webview_user_data_directory(
        user_data_dir,
        isolation,
    )
    if (
        webview_exe_name
        and Path(unquote(webview_exe_name)).name.casefold()
        != candidate.executable.name.casefold()
    ):
        raise VerificationError("WebView2 command line does not identify the candidate App executable")

    ancestry: list[ProcessIdentity] = []
    current_process = browser_process
    current_identity = browser_identity
    reached_app = False
    visited = {browser_identity.pid}
    for _ in range(32):
        try:
            parent_pid = int(current_process.ppid())
        except (OSError, TypeError, ValueError, psutil.Error) as error:
            raise VerificationError(f"Could not follow WebView2 ancestry: {error}") from error
        if parent_pid <= 0 or parent_pid in visited:
            break
        visited.add(parent_pid)
        try:
            parent = process_factory(parent_pid)
            parent_identity = _read_process_identity(parent)
        except (LookupError, OSError, TypeError, ValueError, psutil.Error) as error:
            raise VerificationError(f"Could not inspect WebView2 parent PID {parent_pid}: {error}") from error
        ancestry.append(parent_identity)
        if (
            parent_identity.create_time
            > current_identity.create_time + PROCESS_CREATE_TIME_TOLERANCE_SECONDS
        ):
            raise VerificationError("WebView2 ancestry contains a reused parent PID")
        if (
            parent_identity.create_time + PROCESS_CREATE_TIME_TOLERANCE_SECONDS
            < app_identity.create_time
        ):
            raise VerificationError("WebView2 ancestry contains a process that predates the App")
        if parent_identity.pid == app_identity.pid:
            if (
                abs(parent_identity.create_time - app_identity.create_time)
                > PROCESS_CREATE_TIME_TOLERANCE_SECONDS
                or not _same_path(parent_identity.executable, app_identity.executable)
            ):
                raise VerificationError("WebView2 ancestry reached a reused candidate App PID")
            reached_app = True
            break
        current_process = parent
        current_identity = parent_identity
    if not reached_app:
        raise VerificationError("CDP browser ancestry does not reach the bound candidate App PID")

    return BrowserProof(
        cdp_port=cdp_port,
        identity=browser_identity,
        app_identity=app_identity,
        listener_addresses=tuple(sorted(address for _pid, address in listeners)),
        ancestry=tuple(ancestry),
        command_line_proof={
            "remote_debugging_port": remote_port,
            "remote_debugging_address": remote_address,
            "user_data_dir": str(proven_user_data_dir),
            "webview_exe_name": webview_exe_name or "",
            "ancestry_app_pid": str(app_identity.pid),
        },
    )


def validate_cdp_target(
    target: dict[str, Any],
    cdp_port: int,
    browser_proof: BrowserProof,
) -> dict[str, Any]:
    page_url = urlparse(str(target.get("url") or ""))
    websocket_url = urlparse(str(target.get("webSocketDebuggerUrl") or ""))
    try:
        page_port = page_url.port
        websocket_port = websocket_url.port
    except ValueError as error:
        raise VerificationError(f"CDP target contains an invalid port: {error}") from error
    if (
        target.get("type") != "page"
        or not str(target.get("id") or "").strip()
        or page_url.scheme != "http"
        or page_url.hostname != "tauri.localhost"
        or page_url.username is not None
        or page_url.password is not None
        or page_port is not None
    ):
        raise VerificationError("CDP page target is not the packaged Tauri origin")
    if (
        websocket_url.scheme != "ws"
        or not _is_literal_loopback(websocket_url.hostname)
        or websocket_url.username is not None
        or websocket_url.password is not None
        or websocket_url.query
        or websocket_url.fragment
    ):
        raise VerificationError("CDP page WebSocket is not loopback-only")
    target_id = str(target["id"])
    if (
        browser_proof.cdp_port != cdp_port
        or browser_proof.app_identity not in browser_proof.ancestry
        or not browser_proof.listener_addresses
    ):
        raise VerificationError("CDP target is missing a listener and ancestry proof")
    target_listener = f"{websocket_url.hostname}:{cdp_port}"
    if target_listener not in browser_proof.listener_addresses:
        raise VerificationError("CDP target host is not owned by the proven listener PID")
    if (
        websocket_port != cdp_port
        or websocket_url.path != f"/devtools/page/{target_id}"
    ):
        raise VerificationError("CDP page WebSocket does not belong to the expected listener")
    return target


def _filesystem_identity_payload(identity: FilesystemIdentity) -> dict[str, int]:
    return {"st_dev": identity.st_dev, "st_ino": identity.st_ino}


def _evidence_protected_roots(
    candidate: CandidateBinding,
    isolation: IsolationBinding,
) -> tuple[Path, ...]:
    return (
        candidate.candidate_root,
        candidate.project_root / FORMAL_RELEASE_RELATIVE_ROOT,
        isolation.data_root,
        isolation.real_product_atelier_data_root,
    )


def _assert_evidence_path_boundaries(
    path: Path,
    candidate: CandidateBinding,
    isolation: IsolationBinding,
) -> None:
    if _contains_formal_release_marker(path) or any(
        _paths_overlap(path, protected_root)
        for protected_root in _evidence_protected_roots(candidate, isolation)
    ):
        raise VerificationError(
            "Evidence output must stay outside candidate, formal release, isolated-data, "
            "and real Product Atelier APPDATA trees"
        )


def prepare_evidence_staging(
    value: str | os.PathLike[str],
    candidate: CandidateBinding,
    isolation: IsolationBinding,
) -> EvidenceStagingBinding:
    requested = Path(value)
    if not requested.is_absolute():
        raise VerificationError("Evidence output directory must be absolute")
    if requested.name in {"", ".", ".."}:
        raise VerificationError("Evidence output directory must have a concrete final name")
    if requested.exists() or _is_link_like(requested):
        raise VerificationError(f"Refusing to overwrite existing output directory: {requested}")
    parent = _require_regular_directory(requested.parent, "Evidence output parent")
    parent_identity = _filesystem_identity(parent, "evidence output parent")
    final_output = parent / requested.name
    _assert_evidence_path_boundaries(final_output, candidate, isolation)

    staging = parent / f".{requested.name}.incomplete-{uuid.uuid4().hex}"
    if staging.exists() or _is_link_like(staging):
        raise VerificationError(f"Evidence staging directory already exists: {staging}")
    try:
        staging.mkdir()
    except OSError as error:
        raise VerificationError(
            f"Could not create evidence staging directory: {staging}: {error}"
        ) from error
    binding = EvidenceStagingBinding(
        output_parent=parent,
        final_output_dir=final_output,
        staging_dir=staging,
        output_parent_identity=parent_identity,
        staging_identity=_filesystem_identity(staging, "evidence staging directory"),
    )
    return assert_evidence_staging(binding, candidate, isolation)


def _assert_staging_filesystem_binding(
    binding: EvidenceStagingBinding,
) -> EvidenceStagingBinding:
    parent = _require_regular_directory(binding.output_parent, "Evidence output parent")
    if (
        not _same_path(parent, binding.output_parent)
        or _filesystem_identity(parent, "evidence output parent")
        != binding.output_parent_identity
    ):
        raise VerificationError("Evidence output parent identity changed during verification")
    if binding.final_output_dir.exists() or _is_link_like(binding.final_output_dir):
        raise VerificationError(
            f"Final evidence output appeared before launcher finalization: {binding.final_output_dir}"
        )
    staging = _require_regular_directory(
        binding.staging_dir,
        "Evidence staging directory",
    )
    if (
        not _same_path(staging.parent, parent)
        or _filesystem_identity(staging, "evidence staging directory")
        != binding.staging_identity
    ):
        raise VerificationError("Evidence staging directory identity changed during verification")
    return binding


def assert_evidence_staging(
    binding: EvidenceStagingBinding,
    candidate: CandidateBinding,
    isolation: IsolationBinding,
) -> EvidenceStagingBinding:
    _assert_staging_filesystem_binding(binding)
    _assert_evidence_path_boundaries(binding.final_output_dir, candidate, isolation)
    _assert_evidence_path_boundaries(binding.staging_dir, candidate, isolation)
    return binding


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _sha256_file(path: Path, label: str) -> tuple[int, str]:
    regular = _require_regular_file(path, label)
    digest = hashlib.sha256()
    size = 0
    try:
        with regular.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
    except OSError as error:
        raise VerificationError(f"Could not hash {label}: {regular}: {error}") from error
    return size, digest.hexdigest().upper()


def _png_evidence(
    report: dict[str, Any],
    binding: EvidenceStagingBinding,
) -> list[dict[str, Any]]:
    cases = report.get("cases")
    if not isinstance(cases, list) or not cases:
        raise VerificationError("Verification receipt requires at least one completed case")
    records: list[dict[str, Any]] = []
    expected_names: set[str] = set()
    for case in cases:
        screenshot = case.get("screenshot") if isinstance(case, dict) else None
        if not isinstance(screenshot, dict):
            raise VerificationError("Every verification case must contain PNG metadata")
        relative_path = str(screenshot.get("relative_path") or "")
        relative = Path(relative_path)
        if (
            not relative_path
            or relative.is_absolute()
            or len(relative.parts) != 1
            or relative.name != relative_path
            or relative.suffix.casefold() != ".png"
            or relative_path in expected_names
        ):
            raise VerificationError("Every verification case must bind one unique relative PNG path")
        expected_names.add(relative_path)
        size, sha256 = _sha256_file(
            binding.staging_dir / relative,
            f"case {case.get('index')} PNG",
        )
        try:
            expected_size = int(
                screenshot.get("size_bytes", screenshot.get("bytes", -1))
            )
        except (TypeError, ValueError) as error:
            raise VerificationError(f"Case PNG size is invalid: {relative_path}") from error
        if expected_size != size or screenshot.get("sha256") != sha256:
            raise VerificationError(f"Case PNG identity changed before receipt: {relative_path}")
        records.append({
            "relative_path": relative_path,
            "bytes": size,
            "size_bytes": size,
            "sha256": sha256,
        })
    try:
        entries = list(binding.staging_dir.iterdir())
    except OSError as error:
        raise VerificationError(f"Could not inventory evidence staging: {error}") from error
    if any(entry.name not in expected_names for entry in entries):
        raise VerificationError("Evidence staging contains an unexpected file before receipt")
    if {entry.name for entry in entries} != expected_names:
        raise VerificationError("Evidence staging PNG inventory is incomplete")
    return records


def stage_verification_receipt(
    binding: EvidenceStagingBinding,
    report: dict[str, Any],
    candidate: CandidateBinding,
    isolation: IsolationBinding,
) -> dict[str, Any]:
    assert_evidence_staging(binding, candidate, isolation)
    if report.get("format_version") != 3 or report.get("passed") is not True:
        raise VerificationError("Only a passing format-v3 verification can be staged")
    required_fields = {
        "candidate",
        "app_process",
        "browser_proof",
        "isolation",
        "cases",
        "memory",
        "console_failures",
        "final_identity",
    }
    missing_fields = sorted(required_fields.difference(report))
    if missing_fields:
        raise VerificationError(
            "Verification receipt is missing required v3 fields: "
            + ", ".join(missing_fields)
        )
    receipt = dict(report)
    receipt["evidence"] = {
        "screenshots": _png_evidence(receipt, binding),
        "output_parent": str(binding.output_parent),
        "output_parent_identity": _filesystem_identity_payload(
            binding.output_parent_identity
        ),
        "staging_identity": _filesystem_identity_payload(binding.staging_identity),
    }
    receipt["publication"] = {
        "state": "staged",
        "requires_launcher_finalize": True,
        "staging_directory_name": binding.staging_dir.name,
        "final_output_dir": str(binding.final_output_dir),
        "receipt_name": RECEIPT_NAME,
        "launcher_finalization_name": LAUNCHER_FINALIZATION_NAME,
    }
    payload = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    receipt_path = binding.staging_dir / RECEIPT_NAME
    write_new_file(receipt_path, payload)
    size, receipt_sha256 = _sha256_file(receipt_path, "verification receipt")
    if size != len(payload) or receipt_sha256 != _sha256_bytes(payload):
        raise VerificationError("Verification receipt identity changed after its exclusive write")
    assert_evidence_staging(binding, candidate, isolation)
    return {
        "status": "staged",
        "passed": True,
        "staging_dir": str(binding.staging_dir),
        "final_output_dir": str(binding.final_output_dir),
        "receipt_path": str(receipt_path),
        "receipt_sha256": receipt_sha256,
    }


def mark_evidence_failed(
    binding: EvidenceStagingBinding,
    error: BaseException,
) -> Path:
    _assert_staging_filesystem_binding(binding)
    marker = {
        "format_version": 1,
        "status": "failed",
        "error_type": type(error).__name__,
        "error": "Formal WebView verification failed before receipt publication",
        "final_output_dir": str(binding.final_output_dir),
    }
    write_new_file(
        binding.staging_dir / FAILURE_MARKER_NAME,
        (json.dumps(marker, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    failed = binding.output_parent / (
        f".{binding.final_output_dir.name}.failed-{uuid.uuid4().hex}"
    )
    if failed.exists() or _is_link_like(failed):
        raise VerificationError(f"Evidence failure directory already exists: {failed}")
    try:
        os.rename(binding.staging_dir, failed)
    except OSError as rename_error:
        raise VerificationError(
            f"Could not quarantine failed evidence staging {binding.staging_dir}: {rename_error}"
        ) from rename_error
    if (
        _filesystem_identity(failed, "failed evidence directory")
        != binding.staging_identity
        or binding.final_output_dir.exists()
        or _is_link_like(binding.final_output_dir)
    ):
        raise VerificationError("Failed evidence quarantine identity could not be proven")
    return failed


def write_new_file(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise VerificationError(f"Refusing to overwrite existing evidence: {path}") from error
    except OSError as error:
        raise VerificationError(f"Could not write evidence file {path}: {error}") from error


def load_windows_runtime() -> None:
    """Load Win32-only helpers lazily so this module remains importable on macOS."""
    if os.name != "nt":
        raise VerificationError("Formal WebView verification is available on Windows only")

    global USER32, WINTYPES, FIND_PROCESS_WINDOW, GET_MONITORS_INFO
    if USER32 is not None:
        return

    from ctypes import windll, wintypes

    from capture_startup_video import find_process_window
    from screenshot import get_monitors_info

    USER32 = windll.user32
    WINTYPES = wintypes
    FIND_PROCESS_WINDOW = find_process_window
    GET_MONITORS_INFO = get_monitors_info
    USER32.GetDpiForWindow.argtypes = [WINTYPES.HWND]
    USER32.GetDpiForWindow.restype = WINTYPES.UINT


class CdpClient:
    def __init__(self, websocket_url: str, timeout: float = 12.0) -> None:
        self._socket = websocket.create_connection(
            websocket_url,
            timeout=timeout,
            suppress_origin=True,
        )
        self._next_id = 1
        self.events: list[dict[str, Any]] = []

    def close(self) -> None:
        self._socket.close()

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._socket.send(json.dumps({
            "id": request_id,
            "method": method,
            "params": params or {},
        }))
        while True:
            message = json.loads(self._socket.recv())
            if message.get("id") == request_id:
                if "error" in message:
                    raise VerificationError(f"CDP {method} failed: {message['error']}")
                return message.get("result") or {}
            if "method" in message:
                self.events.append(message)

    def evaluate(self, expression: str, *, await_promise: bool = True) -> Any:
        response = self.call("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": await_promise,
            "returnByValue": True,
            "userGesture": True,
        })
        if response.get("exceptionDetails"):
            details = response["exceptionDetails"]
            raise VerificationError(details.get("text") or "WebView JavaScript evaluation failed")
        remote = response.get("result") or {}
        if remote.get("subtype") == "error":
            raise VerificationError(remote.get("description") or "WebView returned an error")
        return remote.get("value")

    def press_key(self, key: str, code: str | None = None) -> None:
        params = key_event_params(key, code)
        self.call("Input.dispatchKeyEvent", {"type": "rawKeyDown", **params})
        if key in KEY_CHARACTER_TEXT:
            text = KEY_CHARACTER_TEXT[key]
            self.call("Input.dispatchKeyEvent", {
                "type": "char",
                "text": text,
                "unmodifiedText": text,
                **params,
            })
        self.call("Input.dispatchKeyEvent", {"type": "keyUp", **params})

    def screenshot(self, destination: Path) -> dict[str, Any]:
        response = self.call("Page.captureScreenshot", {
            "format": "png",
            "fromSurface": True,
            "captureBeyondViewport": False,
        })
        image_bytes = base64.b64decode(response["data"])
        if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise VerificationError("CDP screenshot was not a PNG")
        write_new_file(destination, image_bytes)
        file_size, file_sha256 = _sha256_file(destination, "CDP screenshot")
        if file_size != len(image_bytes) or file_sha256 != _sha256_bytes(image_bytes):
            raise VerificationError("CDP screenshot identity changed after its exclusive write")
        dimensions = self.evaluate(
            "({width: innerWidth, height: innerHeight, dpr: devicePixelRatio})"
        )
        return {
            "relative_path": destination.name,
            "bytes": file_size,
            "size_bytes": file_size,
            "sha256": file_sha256,
            **dimensions,
        }


def parse_size(raw: str) -> tuple[int, int]:
    parts = raw.lower().split("x", 1)
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("size must use WIDTHxHEIGHT")
    try:
        width, height = (int(value) for value in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("size must use integer dimensions") from exc
    if width < 800 or height < 500:
        raise argparse.ArgumentTypeError("size is below the supported desktop test floor")
    return width, height


def key_event_params(key: str, code: str | None = None) -> dict[str, Any]:
    virtual_key = WINDOWS_VIRTUAL_KEYS.get(key)
    if virtual_key is None:
        raise VerificationError(f"Unsupported keyboard key: {key}")
    return {
        "key": key,
        "code": code or key,
        "windowsVirtualKeyCode": virtual_key,
        "nativeVirtualKeyCode": virtual_key,
    }


def matrix_cases(
    sizes: tuple[tuple[int, int], ...],
    profiles: tuple[str, ...],
    surfaces: tuple[str, ...],
) -> list[tuple[tuple[int, int], str, str]]:
    return [
        (size, profile, surface)
        for size in sizes
        for profile in profiles
        for surface in surfaces
    ]


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise VerificationError(f"CDP discovery refused an HTTP redirect to {newurl!r}")


def _is_packaged_tauri_target_url(value: Any) -> bool:
    try:
        parsed = urlparse(str(value or ""))
        return (
            parsed.scheme == "http"
            and parsed.hostname == "tauri.localhost"
            and parsed.username is None
            and parsed.password is None
            and parsed.port is None
        )
    except ValueError:
        return False


def locate_page(cdp_port: int) -> dict[str, Any]:
    if not 1 <= cdp_port <= 65535:
        raise VerificationError("CDP port must be between 1 and 65535")
    discovery_url = f"http://127.0.0.1:{cdp_port}/json"
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirectHandler(),
    )
    try:
        with opener.open(discovery_url, timeout=5) as response:
            response_url = urlparse(str(response.geturl()))
            if (
                response_url.scheme != "http"
                or response_url.hostname != "127.0.0.1"
                or response_url.port != cdp_port
                or response_url.path != "/json"
                or response_url.username is not None
                or response_url.password is not None
            ):
                raise VerificationError("CDP discovery escaped the literal loopback endpoint")
            targets = json.load(response)
    except VerificationError:
        raise
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise VerificationError(f"Could not read the loopback CDP target list: {error}") from error
    if not isinstance(targets, list):
        raise VerificationError("CDP target list must be a JSON array")
    pages = [
        target for target in targets
        if isinstance(target, dict)
        if target.get("type") == "page"
        and _is_packaged_tauri_target_url(target.get("url"))
    ]
    if len(pages) != 1:
        raise VerificationError(f"Expected one Tauri WebView target, found {len(pages)}")
    return pages[0]


def wait_for(client: CdpClient, expression: str, timeout: float = 12.0) -> Any:
    deadline = time.perf_counter() + timeout
    last_value: Any = None
    while time.perf_counter() < deadline:
        last_value = client.evaluate(expression)
        if last_value:
            return last_value
        time.sleep(0.1)
    raise VerificationError(f"Timed out waiting for WebView condition: {expression}; last={last_value!r}")


def wait_for_stable(
    client: CdpClient,
    expression: str,
    *,
    stable_for: float = 0.6,
    timeout: float = 15.0,
    poll_interval: float = 0.1,
) -> Any:
    deadline = time.perf_counter() + timeout
    stable_since: float | None = None
    last_value: Any = None
    while time.perf_counter() < deadline:
        last_value = client.evaluate(expression)
        now = time.perf_counter()
        if last_value:
            if stable_since is None:
                stable_since = now
            elif now - stable_since >= stable_for:
                return last_value
        else:
            stable_since = None
        time.sleep(poll_interval)
    raise VerificationError(
        f"Timed out waiting for stable WebView condition: {expression}; last={last_value!r}"
    )


def click(client: CdpClient, selector: str) -> dict[str, Any]:
    encoded = json.dumps(selector)
    result = client.evaluate(f"""
      (() => {{
        const selector = {encoded};
        const element = document.querySelector(selector);
        if (!element) return {{ok: false, reason: 'missing', selector}};
        if (element.closest('[hidden]')) return {{ok: false, reason: 'hidden', selector}};
        element.click();
        return {{ok: true, id: element.id || '', text: (element.textContent || '').trim().slice(0, 80)}};
      }})()
    """)
    if not result.get("ok"):
        raise VerificationError(f"Could not click {selector}: {result}")
    return result


def current_window(process_id: int) -> tuple[int, tuple[int, int, int, int]]:
    load_windows_runtime()
    found = FIND_PROCESS_WINDOW(process_id)
    if not found:
        raise VerificationError(f"No visible window found for process {process_id}")
    return found


def move_window(
    process_id: int,
    monitor: dict[str, Any],
    logical_size: tuple[int, int],
    expected_dpi: int,
) -> dict[str, Any]:
    load_windows_runtime()
    hwnd, _ = current_window(process_id)
    work_x, work_y, work_width, work_height = monitor["work"]
    physical_width = round(logical_size[0] * expected_dpi / 96)
    physical_height = round(logical_size[1] * expected_dpi / 96)
    if physical_width > work_width or physical_height > work_height:
        raise VerificationError(
            f"{logical_size[0]}x{logical_size[1]} at {expected_dpi} DPI does not fit monitor work area"
        )
    x = work_x + (work_width - physical_width) // 2
    y = work_y + (work_height - physical_height) // 2
    USER32.ShowWindow(hwnd, SW_RESTORE)
    if not USER32.SetWindowPos(
        hwnd,
        WINTYPES.HWND(0),
        x,
        y,
        physical_width,
        physical_height,
        SWP_SHOWWINDOW,
    ):
        raise OSError("SetWindowPos failed")
    USER32.SetForegroundWindow(hwnd)
    deadline = time.perf_counter() + 5
    actual = current_window(process_id)[1]
    while time.perf_counter() < deadline:
        actual = current_window(process_id)[1]
        if abs(actual[2] - physical_width) <= 32 and abs(actual[3] - physical_height) <= 32:
            break
        time.sleep(0.1)
    actual_dpi = int(USER32.GetDpiForWindow(hwnd))
    if actual_dpi != expected_dpi:
        raise VerificationError(f"Expected {expected_dpi} DPI, window reported {actual_dpi}")
    time.sleep(0.35)
    return {
        "logical_size": list(logical_size),
        "requested_physical_size": [physical_width, physical_height],
        "actual_rect": list(actual),
        "dpi": actual_dpi,
    }


def install_read_only_guard(client: CdpClient) -> dict[str, Any]:
    selectors = json.dumps(DANGEROUS_SELECTORS)
    event_types = json.dumps(READ_ONLY_GUARD_EVENT_TYPES)
    return client.evaluate(f"""
      (() => {{
        if (window.__productAtelierR9Guard) return window.__productAtelierR9Guard;
        const selectors = {selectors};
        const eventTypes = {event_types};
        const guard = (event) => {{
          if (selectors.some((selector) => event.target.closest?.(selector))) {{
            event.preventDefault();
            event.stopImmediatePropagation();
          }}
        }};
        const guardDrop = (event) => {{
          event.preventDefault();
          event.stopImmediatePropagation();
        }};
        eventTypes.forEach((eventType) => document.addEventListener(eventType, guard, true));
        document.addEventListener('drop', guardDrop, true);
        window.__productAtelierR9Guard = {{
          installed: true,
          selectors,
          eventTypes: [...eventTypes, 'drop'],
        }};
        return window.__productAtelierR9Guard;
      }})()
    """)


def install_persistence_guard(client: CdpClient) -> dict[str, Any]:
    """Block browser-storage mutations while retaining exact pre-test state."""
    return client.evaluate(r"""
      (() => {
        if (window.__productAtelierPersistenceGuard) {
          return window.__productAtelierPersistenceGuard.report();
        }
        const local = window.localStorage;
        const session = window.sessionStorage;
        const snapshot = (storage) => Array.from(
          {length: storage.length},
          (_, index) => storage.key(index),
        ).filter((key) => key !== null).map((key) => [key, storage.getItem(key)]);
        const original = {
          setItem: Storage.prototype.setItem,
          removeItem: Storage.prototype.removeItem,
          clear: Storage.prototype.clear,
        };
        const initial = {local: snapshot(local), session: snapshot(session)};
        const blocked = [];
        const storageName = (storage) => storage === local ? 'localStorage' : (
          storage === session ? 'sessionStorage' : 'other'
        );
        Storage.prototype.setItem = function(key, _value) {
          const storage = storageName(this);
          if (storage !== 'other') {
            blocked.push({storage, operation: 'setItem', key: String(key)});
            return undefined;
          }
          return original.setItem.apply(this, arguments);
        };
        Storage.prototype.removeItem = function(key) {
          const storage = storageName(this);
          if (storage !== 'other') {
            blocked.push({storage, operation: 'removeItem', key: String(key)});
            return undefined;
          }
          return original.removeItem.apply(this, arguments);
        };
        Storage.prototype.clear = function() {
          const storage = storageName(this);
          if (storage !== 'other') {
            blocked.push({storage, operation: 'clear', key: ''});
            return undefined;
          }
          return original.clear.apply(this, arguments);
        };
        const report = () => ({
          installed: true,
          blockedOperationCount: blocked.length,
          blockedOperations: blocked.map(({storage, operation, key}) => ({storage, operation, key})),
          initialEntryCounts: {localStorage: initial.local.length, sessionStorage: initial.session.length},
        });
        const restoreStorage = (storage, entries) => {
          original.clear.call(storage);
          entries.forEach(([key, value]) => original.setItem.call(storage, key, value));
        };
        window.__productAtelierPersistenceGuard = {
          report,
          restore() {
            Storage.prototype.setItem = original.setItem;
            Storage.prototype.removeItem = original.removeItem;
            Storage.prototype.clear = original.clear;
            restoreStorage(local, initial.local);
            restoreStorage(session, initial.session);
            const localMatches = JSON.stringify(snapshot(local)) === JSON.stringify(initial.local);
            const sessionMatches = JSON.stringify(snapshot(session)) === JSON.stringify(initial.session);
            const result = {
              ...report(),
              restored: true,
              storageMatchesSnapshot: localMatches && sessionMatches,
            };
            delete window.__productAtelierPersistenceGuard;
            return result;
          },
        };
        return report();
      })()
    """)


def restore_persistence_guard(client: CdpClient) -> dict[str, Any]:
    result = client.evaluate(r"""
      (() => {
        const guard = window.__productAtelierPersistenceGuard;
        return guard ? guard.restore() : {installed: false, restored: false};
      })()
    """)
    if (
        not result.get("installed")
        or not result.get("restored")
        or not result.get("storageMatchesSnapshot")
    ):
        raise VerificationError("Browser persistence guard was not restored")
    return result


def dismiss_layers(client: CdpClient) -> None:
    client.press_key("Escape")
    client.press_key("Escape")
    time.sleep(0.1)


def open_process(client: CdpClient) -> None:
    dismiss_layers(client)
    click(client, "[data-page='process']")
    wait_for(client, "!document.querySelector('#page-process').hidden")


def open_result_view(client: CdpClient) -> dict[str, Any]:
    open_process(client)
    click(client, "#btn-rail-jobs")
    wait_for(client, "!document.querySelector('#job-drawer').hidden")
    wait_for(client, "document.querySelectorAll('[data-job-action=\"open-results\"]').length > 0")
    result = client.evaluate("""
      (() => {
        const button = document.querySelector('[data-job-action="open-results"]');
        const jobId = button?.dataset.jobId || '';
        button?.click();
        return {ok: Boolean(button), jobId};
      })()
    """)
    if not result.get("ok"):
        raise VerificationError("No historical job with results was available")
    wait_for_stable(client, """
      (() => {
        const result = document.querySelector('#canvas-results');
        const image = document.querySelector('#viewer-main-img');
        return Boolean(result && !result.hidden && image?.complete && image.naturalWidth > 0);
      })()
    """)
    return result


def open_surface(client: CdpClient, surface: str) -> dict[str, Any]:
    if surface in {"single", "multi-file", "group-split", "cutout-batch"}:
        open_process(client)
        click(client, f"[data-mode='{surface}']")
        wait_for(client, f"document.querySelector('[data-mode=\"{surface}\"]').classList.contains('active')")
        return {"surface": surface}
    if surface == "infinite-canvas-library":
        dismiss_layers(client)
        entry = click(client, "[data-page='canvas']")
        state = wait_for_stable(client, INFINITE_CANVAS_READY_EXPRESSION)
        return {"surface": surface, "entry_state": entry, **state}
    if surface == "growth":
        dismiss_layers(client)
        click(client, "[data-page='memory']")
        wait_for(client, "!document.querySelector('#page-memory').hidden")
        return {"surface": surface}
    if surface == "task-center":
        open_process(client)
        click(client, "#btn-rail-jobs")
        wait_for(client, "!document.querySelector('#job-drawer').hidden")
        return {"surface": surface}
    if surface == "result-review":
        job = open_result_view(client)
        click(client, "#btn-open-compare")
        wait_for(client, "!document.querySelector('#page-compare').hidden")
        wait_for(client, "!document.querySelector('#compare-view').hidden", timeout=15)
        reset_result_review_view(client)
        return {"surface": surface, **job}
    raise VerificationError(f"Unknown surface: {surface}")


def reset_result_review_view(client: CdpClient) -> dict[str, Any]:
    result = client.evaluate("""
      (() => {
        const reset = document.querySelector('#btn-compare-reset');
        reset?.click();
        const guide = document.querySelector('#review-guide');
        if (guide && !guide.hidden) document.querySelector('#btn-review-guide-done')?.click();
        const reason = document.querySelector('#review-reason');
        if (reason) reason.hidden = true;
        document.querySelectorAll('[data-review-decision]').forEach((button) => {
          button.classList.remove('is-selected');
        });
        document.querySelector('.review-page')?.scrollTo({top: 0, left: 0, behavior: 'instant'});
        document.querySelector('.review-decision-panel')?.scrollTo({top: 0, left: 0, behavior: 'instant'});
        return {
          ok: Boolean(reset),
          divider: document.querySelector('#compare-slider')?.getAttribute('aria-valuenow') || '',
          zoom: document.querySelector('#compare-zoom-value')?.textContent || '',
          reasonHidden: Boolean(reason?.hidden),
          guideHidden: Boolean(guide?.hidden),
        };
      })()
    """)
    if not result.get("ok"):
        raise VerificationError("Could not restore the result review baseline")
    wait_for_stable(client, """
      (() => {
        const images = [...document.querySelectorAll('#page-compare img')]
          .filter((image) => !image.closest('[hidden]') && image.getClientRects().length > 0);
        return images.length > 0 && images.every((image) => image.complete);
      })()
    """)
    return result


def apply_profile(client: CdpClient, profile: str) -> dict[str, Any]:
    settings = {
        "light": ("light", "standard"),
        "dark": ("dark", "standard"),
        "high": ("light", "high"),
    }
    theme, contrast = settings[profile]
    dismiss_layers(client)
    click(client, "[data-page='settings']")
    wait_for(client, "!document.querySelector('#page-settings').hidden")
    result = client.evaluate(f"""
      (() => {{
        const themeInput = document.querySelector('input[name="appearance-theme"][value="{theme}"]');
        const contrastInput = document.querySelector('input[name="appearance-contrast"][value="{contrast}"]');
        if (!themeInput || !contrastInput) return {{ok: false}};
        const root = document.documentElement;
        root.dataset.themePreference = '{theme}';
        root.dataset.theme = '{theme}';
        root.dataset.contrast = '{contrast}';
        document.querySelectorAll('input[name="appearance-theme"]').forEach((input) => {{
          input.checked = input === themeInput;
        }});
        document.querySelectorAll('input[name="appearance-contrast"]').forEach((input) => {{
          input.checked = input === contrastInput;
        }});
        const dark = '{theme}' === 'dark';
        const moon = document.querySelector('#theme-icon-moon');
        const sun = document.querySelector('#theme-icon-sun');
        if (moon) moon.hidden = dark;
        if (sun) sun.hidden = !dark;
        const toggle = document.querySelector('#theme-toggle');
        if (toggle) {{
          toggle.setAttribute('aria-label', `当前为${{dark ? '深色' : '浅色'}}主题`);
          toggle.title = toggle.getAttribute('aria-label');
        }}
        return {{
          ok: true,
          transient: true,
          theme: root.dataset.theme,
          contrast: root.dataset.contrast,
        }};
      }})()
    """)
    if not result.get("ok"):
        raise VerificationError(f"Could not apply appearance profile {profile}")
    return result


def dom_snapshot(client: CdpClient) -> dict[str, Any]:
    return client.evaluate("""
      (() => {
        const visible = (element) => element
          && !element.closest('[hidden]')
          && !element.closest('[inert]')
          && element.getClientRects().length > 0;
        const viewport = {width: innerWidth, height: innerHeight, dpr: devicePixelRatio};
        const referencedLabel = (element) => (element.getAttribute('aria-labelledby') || '')
          .split(/\\s+/)
          .filter(Boolean)
          .map((id) => document.getElementById(id)?.textContent || '')
          .join(' ');
        const named = (element) => ([
          element.getAttribute('aria-label'),
          referencedLabel(element),
          [...(element.labels || [])].map((label) => label.textContent || '').join(' '),
          element.closest('label')?.textContent,
          element.querySelector('img[alt]')?.getAttribute('alt'),
          element.getAttribute('title'),
          element.textContent,
        ].find((value) => String(value || '').trim()) || '').trim();
        const visibleControls = [...document.querySelectorAll('button,input,select,textarea,[role="button"],[role="tab"],[role="slider"]')]
          .filter(visible);
        const unnamed = visibleControls.filter((element) => !named(element));
        const positiveTabIndex = visibleControls.filter((element) => element.tabIndex > 0);
        const brokenImages = [...document.querySelectorAll('img')]
          .filter(visible)
          .filter((image) => !image.complete || image.naturalWidth <= 0)
          .map((image) => ({
            id: image.id || '',
            alt: image.getAttribute('alt') || '',
            src: image.currentSrc || image.getAttribute('src') || '',
            complete: image.complete,
            naturalWidth: image.naturalWidth,
            naturalHeight: image.naturalHeight,
          }));
        const boundsIssues = [...document.querySelectorAll('.app-page:not([hidden]), .drawer-layer:not([hidden]) .drawer')]
          .filter(visible)
          .map((element) => ({id: element.id || element.className, rect: element.getBoundingClientRect()}))
          .filter(({rect}) => rect.left < -1 || rect.top < -1 || rect.right > innerWidth + 1 || rect.bottom > innerHeight + 1)
          .map(({id, rect}) => ({id, left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom}));
        return {
          viewport,
          profile: {
            theme: document.documentElement.dataset.theme || '',
            contrast: document.documentElement.dataset.contrast || '',
            textScale: document.documentElement.dataset.textScale || '',
            motion: document.documentElement.dataset.motion || '',
          },
          page: document.querySelector('.app-page:not([hidden])')?.dataset.pageName || '',
          drawer: document.querySelector('.drawer-layer:not([hidden])')?.id || '',
          documentOverflowX: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - innerWidth,
          visibleControlCount: visibleControls.length,
          unnamedControls: unnamed.map((element) => element.id || element.outerHTML.slice(0, 100)),
          positiveTabIndex: positiveTabIndex.map((element) => element.id || element.outerHTML.slice(0, 100)),
          brokenImages,
          boundsIssues,
          resultImage: {
            visible: visible(document.querySelector('#viewer-main-img')),
            complete: Boolean(document.querySelector('#viewer-main-img')?.complete),
            naturalWidth: document.querySelector('#viewer-main-img')?.naturalWidth || 0,
            naturalHeight: document.querySelector('#viewer-main-img')?.naturalHeight || 0,
          },
        };
      })()
    """)


def snapshot_passes(snapshot: dict[str, Any]) -> bool:
    return all((
        snapshot["documentOverflowX"] <= 1,
        not snapshot["unnamedControls"],
        not snapshot["positiveTabIndex"],
        not snapshot["brokenImages"],
        not snapshot["boundsIssues"],
    ))


def focus_snapshot(client: CdpClient) -> dict[str, Any]:
    return client.evaluate("""
      (() => {
        const element = document.activeElement;
        const rect = element?.getBoundingClientRect?.();
        const style = element ? getComputedStyle(element) : null;
        const center = rect ? document.elementFromPoint(
          Math.max(0, Math.min(innerWidth - 1, rect.left + rect.width / 2)),
          Math.max(0, Math.min(innerHeight - 1, rect.top + rect.height / 2)),
        ) : null;
        return {
          tag: element?.tagName || '',
          id: element?.id || '',
          className: typeof element?.className === 'string' ? element.className : '',
          text: (element?.textContent || '').trim().slice(0, 80),
          role: element?.getAttribute?.('role') || '',
          resultTab: element?.dataset?.rtab || '',
          reviewDecision: element?.dataset?.reviewDecision || '',
          visible: Boolean(rect && rect.width && rect.height && rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth),
          fullyVisible: Boolean(rect && rect.top >= 0 && rect.left >= 0 && rect.bottom <= innerHeight && rect.right <= innerWidth),
          centerUnobscured: Boolean(element && center && (element === center || element.contains(center) || center.contains(element))),
          outlineWidth: style?.outlineWidth || '',
          outlineStyle: style?.outlineStyle || '',
          ariaSelected: element?.getAttribute?.('aria-selected') || '',
          ariaValueNow: element?.getAttribute?.('aria-valuenow') || '',
        };
      })()
    """)


def prepare_review_form_for_keyboard(client: CdpClient) -> dict[str, Any]:
    state = client.evaluate("""
      (() => {
        const summary = document.querySelector('#review-summary');
        const options = document.querySelector('#review-options');
        return {
          summaryVisible: Boolean(summary && !summary.hidden),
          optionsVisible: Boolean(options && !options.hidden),
        };
      })()
    """)
    if state.get("summaryVisible"):
        client.evaluate("document.querySelector('#btn-review-edit').focus()")
        client.press_key("Enter")
        wait_for(client, "!document.querySelector('#review-options').hidden")
        return {
            "entered_edit_mode": True,
            "focus_after_edit": focus_snapshot(client),
        }
    if not state.get("optionsVisible"):
        raise VerificationError(
            "Result review exposed neither its durable summary nor decision options"
        )
    return {"entered_edit_mode": False}


def run_keyboard_path(client: CdpClient) -> dict[str, Any]:
    report: dict[str, Any] = {}
    job = open_result_view(client)
    report["job_id"] = job["jobId"]
    client.evaluate("document.querySelector('.result-tab[data-rtab=\"main\"]').focus()")
    client.press_key("ArrowRight")
    time.sleep(0.15)
    report["result_tab_arrow_right"] = focus_snapshot(client)
    client.press_key("Home")
    time.sleep(0.15)
    report["result_tab_home"] = focus_snapshot(client)
    client.evaluate("document.querySelector('#btn-open-compare').focus()")
    client.press_key("Enter")
    wait_for(client, "!document.querySelector('#page-compare').hidden")
    client.evaluate("document.querySelector('#btn-compare-help').focus()")
    client.press_key("Enter")
    wait_for(client, "!document.querySelector('#review-guide').hidden")
    report["guide_initial_focus"] = focus_snapshot(client)
    client.press_key("Escape")
    wait_for(client, "document.querySelector('#review-guide').hidden")
    report["guide_focus_restore"] = focus_snapshot(client)
    client.evaluate("document.querySelector('#compare-slider').focus()")
    before = focus_snapshot(client)
    client.press_key("ArrowRight")
    time.sleep(0.15)
    after = focus_snapshot(client)
    report["compare_slider"] = {"before": before, "after": after}
    report["review_form"] = prepare_review_form_for_keyboard(client)
    client.evaluate("document.querySelector('[data-review-decision=\"adjusted\"]').focus()")
    client.press_key("Enter")
    wait_for(client, "!document.querySelector('#review-reason').hidden")
    report["review_decision"] = focus_snapshot(client)
    report["passed"] = all((
        report["result_tab_arrow_right"]["resultTab"] == "cutout",
        report["result_tab_arrow_right"]["ariaSelected"] == "true",
        report["result_tab_home"]["resultTab"] == "main",
        report["guide_initial_focus"]["id"] == "btn-review-guide-done",
        report["guide_focus_restore"]["id"] == "btn-compare-help",
        int(after["ariaValueNow"] or 0) > int(before["ariaValueNow"] or 0),
        all(value["visible"] and value["centerUnobscured"] for value in (
            report["result_tab_arrow_right"],
            report["result_tab_home"],
            report["guide_initial_focus"],
            report["guide_focus_restore"],
            after,
            report["review_decision"],
        )),
    ))
    return report


def classify_process(process: psutil.Process, root_pid: int) -> str:
    name = process.name().lower()
    if process.pid == root_pid:
        return "app_shell"
    if name == "msedgewebview2.exe":
        return "webview2"
    if name == "python-server.exe":
        return "sidecar"
    return "other"


def memory_sample(process_id: int) -> dict[str, int]:
    root = psutil.Process(process_id)
    processes = [root, *root.children(recursive=True)]
    totals = {"app_shell": 0, "webview2": 0, "sidecar": 0, "other": 0, "total": 0}
    for process in processes:
        try:
            working_set = int(process.memory_info().rss)
            totals[classify_process(process, process_id)] += working_set
            totals["total"] += working_set
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return totals


def summarize_memory_samples(samples: list[dict[str, int]]) -> dict[str, Any]:
    if not samples:
        raise VerificationError("No memory samples were collected")
    report: dict[str, Any] = {"sample_count": len(samples), "samples": samples}
    for key in ("app_shell", "webview2", "sidecar", "other", "total"):
        values = [sample[key] for sample in samples]
        report[f"{key}_p50_bytes"] = int(statistics.median(values))
        report[f"{key}_peak_bytes"] = max(values)
    return report


def console_failures(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for event in events:
        method = event.get("method")
        params = event.get("params") or {}
        if method == "Runtime.exceptionThrown":
            failures.append({"method": method, "text": params.get("exceptionDetails", {}).get("text", "")})
        elif method == "Log.entryAdded" and params.get("entry", {}).get("level") in {"error", "warning"}:
            entry = params["entry"]
            failures.append({"method": method, "level": entry.get("level"), "text": entry.get("text", "")})
        elif method == "Runtime.consoleAPICalled" and params.get("type") in {"error", "warning"}:
            failures.append({"method": method, "level": params.get("type")})
    return failures


def _process_identity_payload(identity: ProcessIdentity) -> dict[str, Any]:
    return {
        "pid": identity.pid,
        "create_time": identity.create_time,
        "executable": str(identity.executable),
    }


def _candidate_payload(binding: CandidateBinding) -> dict[str, Any]:
    return {
        "project_root": str(binding.project_root),
        "candidate_root": str(binding.candidate_root),
        "executable": str(binding.executable),
        "git_commit": binding.git_commit,
        "app_sha256": binding.app_sha256,
        "tree_sha256": binding.tree_sha256,
        "candidate_root_identity": _filesystem_identity_payload(
            binding.candidate_root_identity
        ),
        "executable_identity": _filesystem_identity_payload(binding.executable_identity),
        "inventory": binding.candidate["inventory"],
        "artifacts": binding.candidate["artifacts"],
    }


def _isolation_payload(binding: IsolationBinding) -> dict[str, Any]:
    return {
        "temp_root": str(binding.temp_root),
        "data_root": str(binding.data_root),
        "webview_data_root": str(binding.webview_data_root),
        "knowledge_root": str(binding.knowledge_root),
        "legacy_config_path": str(binding.legacy_config_path),
        "real_product_atelier_data_root": str(
            binding.real_product_atelier_data_root
        ),
        "identities": {
            "temp_root": _filesystem_identity_payload(binding.temp_root_identity),
            "data_root": _filesystem_identity_payload(binding.data_root_identity),
            "webview_data_root": _filesystem_identity_payload(
                binding.webview_data_root_identity
            ),
            "knowledge_root": _filesystem_identity_payload(
                binding.knowledge_root_identity
            ),
        },
        "credentials": credential_isolation_snapshot(binding),
    }


def _browser_proof_payload(proof: BrowserProof) -> dict[str, Any]:
    return {
        "cdp_port": proof.cdp_port,
        "identity": _process_identity_payload(proof.identity),
        "app_identity": _process_identity_payload(proof.app_identity),
        "listener_addresses": list(proof.listener_addresses),
        "ancestry": [_process_identity_payload(identity) for identity in proof.ancestry],
        "command_line_proof": proof.command_line_proof,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--expected-git-commit", required=True)
    parser.add_argument("--expected-app-sha256", required=True)
    parser.add_argument("--expected-tree-sha256", required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--expected-create-time", type=float, required=True)
    parser.add_argument("--cdp-port", type=int, required=True)
    parser.add_argument("--isolated-data-dir", type=Path, required=True)
    parser.add_argument("--monitor-index", type=int, required=True)
    parser.add_argument("--expected-dpi", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sizes", nargs="+", type=parse_size, default=list(DEFAULT_SIZES))
    parser.add_argument("--profiles", nargs="+", choices=DEFAULT_PROFILES, default=list(DEFAULT_PROFILES))
    parser.add_argument("--surfaces", nargs="+", choices=DEFAULT_SURFACES, default=list(DEFAULT_SURFACES))
    return parser


def run_formal_webview_verification(args: argparse.Namespace) -> dict[str, Any]:
    load_windows_runtime()
    candidate = validate_candidate_binding(
        args.exe,
        expected_git_commit=args.expected_git_commit,
        expected_app_sha256=args.expected_app_sha256,
        expected_tree_sha256=args.expected_tree_sha256,
    )
    isolation = validate_isolation_binding(args.isolated_data_dir, candidate)
    app_identity = validate_app_process_identity(
        args.pid,
        args.expected_create_time,
        candidate,
        isolation,
    )
    if GET_MONITORS_INFO is None:
        raise VerificationError("Windows monitor inspection is unavailable")
    monitors = GET_MONITORS_INFO()
    if args.monitor_index < 0 or args.monitor_index >= len(monitors):
        raise VerificationError(f"Monitor index {args.monitor_index} is unavailable")
    monitor = monitors[args.monitor_index]
    if int(monitor["dpi"]) != args.expected_dpi:
        raise VerificationError(
            f"Monitor {args.monitor_index} reports {monitor['dpi']} DPI, "
            f"expected {args.expected_dpi}"
        )

    browser_proof = prove_cdp_browser_process(
        args.cdp_port,
        app_identity,
        candidate,
        isolation,
    )
    target = validate_cdp_target(
        locate_page(args.cdp_port),
        args.cdp_port,
        browser_proof,
    )
    staging = prepare_evidence_staging(args.output_dir, candidate, isolation)
    try:
        client = CdpClient(target["webSocketDebuggerUrl"])
        report: dict[str, Any] = {
            "format_version": 3,
            "passed": False,
            "candidate": _candidate_payload(candidate),
            "app_process": _process_identity_payload(app_identity),
            "browser_proof": _browser_proof_payload(browser_proof),
            "isolation": _isolation_payload(isolation),
            "target": {
                key: target.get(key)
                for key in ("id", "title", "type", "url", "webSocketDebuggerUrl")
            },
            "monitor": monitor,
            "expected_dpi": args.expected_dpi,
            "cases": [],
        }
        primary_error: BaseException | None = None
        persistence_error: BaseException | None = None
        client_close_error: BaseException | None = None
        persistence_restore_required = False
        try:
            client.call("Runtime.enable")
            client.call("Page.enable")
            client.call("Log.enable")
            wait_for(client, "document.readyState === 'complete'")
            wait_for(
                client,
                "document.body && !document.querySelector('#boot-screen:not([hidden])')",
                timeout=20,
            )
            persistence_restore_required = True
            persistence_initial = install_persistence_guard(client)
            if not isinstance(persistence_initial, dict) or not persistence_initial.get("installed"):
                raise VerificationError("Browser persistence guard was not installed")
            report["persistence_guard_initial"] = persistence_initial
            read_only_guard = install_read_only_guard(client)
            if not isinstance(read_only_guard, dict) or not read_only_guard.get("installed"):
                raise VerificationError("Read-only interaction guard was not installed")
            report["read_only_guard"] = read_only_guard

            baseline_size = (1280, 720)
            validate_app_process_identity(
                args.pid,
                args.expected_create_time,
                candidate,
                isolation,
            )
            report["keyboard_window"] = move_window(
                args.pid,
                monitor,
                baseline_size,
                args.expected_dpi,
            )
            report["keyboard"] = run_keyboard_path(client)

            cases = matrix_cases(
                tuple(args.sizes),
                tuple(args.profiles),
                tuple(args.surfaces),
            )
            for index, (size, profile, surface) in enumerate(cases, start=1):
                validate_app_process_identity(
                    args.pid,
                    args.expected_create_time,
                    candidate,
                    isolation,
                )
                assert_evidence_staging(staging, candidate, isolation)
                window = move_window(args.pid, monitor, size, args.expected_dpi)
                applied_profile = apply_profile(client, profile)
                surface_state = open_surface(client, surface)
                time.sleep(0.3)
                snapshot = dom_snapshot(client)
                filename = (
                    f"dpi-{args.expected_dpi}-"
                    f"{size[0]}x{size[1]}-{profile}-{surface}.png"
                )
                screenshot = client.screenshot(staging.staging_dir / filename)
                assert_evidence_staging(staging, candidate, isolation)
                report["cases"].append({
                    "index": index,
                    "size": list(size),
                    "profile": profile,
                    "surface": surface,
                    "window": window,
                    "applied_profile": applied_profile,
                    "surface_state": surface_state,
                    "snapshot": snapshot,
                    "screenshot": screenshot,
                    "passed": snapshot_passes(snapshot),
                })

            memory_samples = []
            for _ in range(7):
                memory_samples.append(memory_sample(args.pid))
                time.sleep(0.25)
            report["memory"] = summarize_memory_samples(memory_samples)
            report["console_failures"] = console_failures(client.events)
        except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001
            primary_error = error
        finally:
            if persistence_restore_required:
                try:
                    report["persistence_guard_final"] = restore_persistence_guard(client)
                except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001
                    persistence_error = error
            try:
                client.close()
            except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001
                client_close_error = error

        if primary_error is not None:
            cleanup_details = []
            if persistence_error is not None:
                cleanup_details.append(f"storage restoration={persistence_error}")
            if client_close_error is not None:
                cleanup_details.append(f"CDP close={client_close_error}")
            if cleanup_details:
                raise VerificationError(
                    "Formal WebView verification failed with cleanup errors; "
                    f"primary={primary_error}; cleanup={'; '.join(cleanup_details)}"
                ) from primary_error
            raise primary_error
        if persistence_error is not None:
            raise VerificationError(
                f"Browser persistence restoration failed: {persistence_error}"
            ) from persistence_error
        if client_close_error is not None:
            raise VerificationError(
                f"CDP client close failed: {client_close_error}"
            ) from client_close_error

        assert_evidence_staging(staging, candidate, isolation)
        final_isolation = assert_isolation_binding(isolation, candidate)
        final_app_identity = validate_app_process_identity(
            args.pid,
            args.expected_create_time,
            candidate,
            final_isolation,
        )
        if final_app_identity != app_identity:
            raise VerificationError("Candidate App process identity changed during verification")
        final_browser_proof = prove_cdp_browser_process(
            args.cdp_port,
            final_app_identity,
            candidate,
            final_isolation,
        )
        if final_browser_proof != browser_proof:
            raise VerificationError("CDP listener or ancestry identity changed during verification")
        final_target = validate_cdp_target(
            locate_page(args.cdp_port),
            args.cdp_port,
            final_browser_proof,
        )
        target_identity_keys = ("id", "type", "url", "webSocketDebuggerUrl")
        if any(final_target.get(key) != target.get(key) for key in target_identity_keys):
            raise VerificationError("CDP page target changed during verification")
        final_candidate = assert_candidate_binding(candidate)
        final_isolation = assert_isolation_binding(isolation, candidate)
        assert_evidence_staging(staging, candidate, isolation)

        report["final_identity"] = {
            "app_process": _process_identity_payload(final_app_identity),
            "browser_proof": _browser_proof_payload(final_browser_proof),
            "candidate": _candidate_payload(final_candidate),
            "isolation": _isolation_payload(final_isolation),
            "candidate_unchanged": True,
            "isolation_unchanged": True,
            "output_parent_unchanged": True,
        }
        expected_case_count = len(matrix_cases(
            tuple(args.sizes),
            tuple(args.profiles),
            tuple(args.surfaces),
        ))
        report["passed"] = all((
            len(report["cases"]) == expected_case_count,
            report["keyboard"]["passed"],
            all(case["passed"] for case in report["cases"]),
            not report["console_failures"],
            report["persistence_guard_final"]["restored"],
            report["persistence_guard_final"]["storageMatchesSnapshot"],
        ))
        if not report["passed"]:
            failed_cases = [
                str(case["index"])
                for case in report["cases"]
                if not case["passed"]
            ]
            raise VerificationError(
                "Formal WebView behavioral gate failed; failed cases="
                + (",".join(failed_cases) or "none")
            )
        return stage_verification_receipt(staging, report, candidate, isolation)
    except (Exception, KeyboardInterrupt) as error:
        try:
            failed_dir = mark_evidence_failed(staging, error)
        except (VerificationError, KeyboardInterrupt) as quarantine_error:
            raise VerificationError(
                "Formal WebView verification failed and evidence quarantine could not be "
                f"completed; staging remains incomplete at {staging.staging_dir}; "
                f"verification={error}; quarantine={quarantine_error}"
            ) from error
        raise VerificationError(
            f"Formal WebView verification failed; evidence marked failed at {failed_dir}: {error}"
        ) from error


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_formal_webview_verification(args)
    except VerificationError as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
