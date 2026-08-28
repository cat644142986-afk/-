#!/usr/bin/env python3
"""Transactional staging and promotion for Product Atelier portable releases.

The Windows build creates the EXE and PyInstaller sidecar.  This helper owns
only directory assembly, inventory, backup, promotion, finalization and
rollback so those safety properties can be tested on every development host.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any


APP_NAME = "Product Atelier.exe"
SIDECAR_EXE = Path("python-server") / "python-server.exe"
SIDECAR_MANIFEST = Path("python-server") / "sidecar-manifest.json"
TRANSACTION_FORMAT_VERSION = 1
TRANSACTION_FILE_NAME = "portable-promotion-transaction.json"
LOCK_FILE_NAME = "portable-promotion.lock"
TRANSACTION_PHASES = {
    "prepared",
    "backed_up",
    "candidate_copied",
    "previous_moved",
    "promoted",
    "finalizing",
    "finalized",
    "rolled_back",
}


class ReleaseError(RuntimeError):
    """Raised when a release safety invariant is not satisfied."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _is_broad_project_root(project: PurePath, home: PurePath) -> bool:
    """Reject filesystem roots and the user home, but allow a drive child.

    ``D:\\ProductAtelier-Desktop`` has only two ``Path.parts`` entries on
    Windows (drive anchor plus directory). A generic ``len(parts) < 3`` check
    therefore rejects the intended production checkout even though all release
    mutations are further confined to its ``build`` and ``release`` children.
    """
    anchor = type(project)(project.anchor)
    return project == anchor or project == home


def _project_root(path: str | Path) -> Path:
    project = _resolved(path)
    if _is_broad_project_root(project, _resolved(Path.home())):
        raise ReleaseError(f"Project root is too broad: {project}")
    sentinels = (project / "package.json", project / "src-tauri" / "tauri.conf.json")
    if not all(sentinel.is_file() for sentinel in sentinels):
        raise ReleaseError(f"Project root is missing Product Atelier sentinels: {project}")
    return project


@contextlib.contextmanager
def _promotion_lock(project: Path):
    """Hold one stable cross-process lock for every mutating release command."""

    build_root = project / "build"
    if build_root.exists() and _is_link_like(build_root):
        raise ReleaseError(f"Build root may not be a symlink or junction: {build_root}")
    build_root.mkdir(parents=True, exist_ok=True)
    if _resolved(build_root).parent != project:
        raise ReleaseError(f"Build root resolves outside the project: {build_root}")
    lock_path = build_root / LOCK_FILE_NAME
    if _is_link_like(lock_path):
        raise ReleaseError(f"Promotion lock may not be a symlink or junction: {lock_path}")

    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise ReleaseError(f"Could not safely open the promotion lock: {lock_path}") from error
    with os.fdopen(descriptor, "r+b") as handle:
        if _is_link_like(lock_path) or _resolved(lock_path) != lock_path:
            raise ReleaseError(f"Promotion lock resolved outside its canonical path: {lock_path}")
        lock_stat = os.fstat(handle.fileno())
        if not stat.S_ISREG(lock_stat.st_mode):
            raise ReleaseError(f"Promotion lock is not a regular file: {lock_path}")
        if lock_stat.st_nlink != 1:
            raise ReleaseError(f"Promotion lock may not be a hard link: {lock_path}")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        locked = False
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as error:
            raise ReleaseError(
                f"Another portable release command owns the promotion lock: {lock_path}"
            ) from error
        try:
            yield
        finally:
            if locked:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _require_child(path: Path, root: Path, label: str) -> Path:
    resolved_path = _resolved(path)
    resolved_root = _resolved(root)
    if resolved_path == resolved_root or resolved_root not in resolved_path.parents:
        raise ReleaseError(f"{label} must stay inside {resolved_root}: {resolved_path}")
    return resolved_path


def _require_direct_child(path: Path, root: Path, label: str) -> Path:
    resolved_path = _require_child(path, root, label)
    if resolved_path.parent != _resolved(root):
        raise ReleaseError(f"{label} must be a direct child of {_resolved(root)}: {resolved_path}")
    return resolved_path


def _canonical_candidate_path(project: Path, path: str | Path) -> Path:
    candidate = _require_direct_child(Path(path), project / "build", "candidate directory")
    expected = project / "build" / "portable-candidate-current"
    if candidate != expected:
        raise ReleaseError(f"Candidate directory must use the canonical path: {expected}")
    return candidate


def _canonical_portable_path(project: Path, path: str | Path) -> Path:
    portable = _require_direct_child(Path(path), project / "release", "formal portable directory")
    expected = project / "release" / "ProductAtelier-Portable"
    if portable != expected:
        raise ReleaseError(f"Formal portable directory must use the canonical path: {expected}")
    return portable


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _require_regular_file(path: Path, label: str) -> None:
    if _is_link_like(path):
        raise ReleaseError(f"{label} may not be a symlink or junction: {path}")
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise ReleaseError(f"{label} is missing: {path}") from error
    if not stat.S_ISREG(mode):
        raise ReleaseError(f"{label} must be a regular file: {path}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _tree_entries(root: str | Path) -> list[dict[str, Any]]:
    root_path = _resolved(root)
    if not root_path.is_dir():
        raise ReleaseError(f"Release directory is missing: {root_path}")
    if _is_link_like(root_path):
        raise ReleaseError(f"Release directory may not be a symlink or junction: {root_path}")

    entries: list[dict[str, Any]] = []
    for current_root, directory_names, file_names in os.walk(root_path, topdown=True, followlinks=False):
        current = Path(current_root)
        for name in directory_names:
            directory = current / name
            if _is_link_like(directory) or not stat.S_ISDIR(directory.lstat().st_mode):
                raise ReleaseError(f"Release tree contains an unsafe directory entry: {directory}")
            entries.append(
                {"kind": "D", "path": directory.relative_to(root_path).as_posix()}
            )
        for name in file_names:
            path = current / name
            _require_regular_file(path, "Release artifact")
            relative = path.relative_to(root_path).as_posix()
            entries.append(
                {
                    "kind": "F",
                    "path": relative,
                    "size": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
            )

    entries.sort(key=lambda entry: (entry["path"], entry["kind"]))
    return entries


def directory_inventory(root: str | Path) -> dict[str, Any]:
    """Return a deterministic inventory without storing every path in evidence."""

    entries = _tree_entries(root)
    rows = [entry for entry in entries if entry["kind"] == "F"]
    directories = [entry for entry in entries if entry["kind"] == "D"]

    digest = hashlib.sha256()
    for entry in entries:
        if entry["kind"] == "D":
            digest.update(f"D\0{entry['path']}\n".encode("utf-8"))
        else:
            digest.update(
                f"F\0{entry['path']}\0{entry['size']}\0{entry['sha256']}\n".encode("utf-8")
            )

    return {
        "file_count": len(rows),
        "directory_count": len(directories),
        "total_bytes": sum(int(entry["size"]) for entry in rows),
        "tree_sha256": digest.hexdigest().upper(),
    }


def _validate_cleanup_subset(root: Path, expected_entries: list[dict[str, Any]], label: str) -> None:
    expected: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in expected_entries:
        if not isinstance(entry, dict) or entry.get("kind") not in {"D", "F"}:
            raise ReleaseError(f"{label} cleanup manifest is invalid")
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            raise ReleaseError(f"{label} cleanup manifest has an invalid path")
        expected[(entry["kind"], path)] = entry

    for current in _tree_entries(root):
        expected_entry = expected.get((current["kind"], current["path"]))
        if expected_entry is None:
            raise ReleaseError(
                f"{label} contains an unexpected path during resumable cleanup: {current['path']}"
            )
        if current["kind"] == "F" and (
            current.get("size") != expected_entry.get("size")
            or current.get("sha256") != expected_entry.get("sha256")
        ):
            raise ReleaseError(
                f"{label} contains modified data during resumable cleanup: {current['path']}"
            )


def _verify_inventory(root: Path, expected: dict[str, Any], label: str) -> dict[str, Any]:
    actual = directory_inventory(root)
    for key in ("file_count", "directory_count", "total_bytes", "tree_sha256"):
        if actual.get(key) != expected.get(key):
            raise ReleaseError(
                f"{label} inventory mismatch for {key}: "
                f"expected {expected.get(key)!r}, got {actual.get(key)!r}"
            )
    return actual


def _inventory_matches(root: Path, expected: dict[str, Any]) -> bool:
    try:
        actual = directory_inventory(root)
    except ReleaseError:
        return False
    return all(
        actual.get(key) == expected.get(key)
        for key in ("file_count", "directory_count", "total_bytes", "tree_sha256")
    )


def _load_manifest(release_dir: Path) -> tuple[dict[str, Any], Path]:
    manifest_path = release_dir / SIDECAR_MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError) as error:
        raise ReleaseError(f"Invalid sidecar manifest: {manifest_path}: {error}") from error
    if not isinstance(manifest, dict):
        raise ReleaseError(f"Sidecar manifest must be a JSON object: {manifest_path}")
    return manifest, manifest_path


def validate_candidate(release_dir: str | Path, expected_git_commit: str) -> dict[str, Any]:
    release_path = _resolved(release_dir)
    if not re.fullmatch(r"[0-9a-fA-F]{40}", expected_git_commit):
        raise ReleaseError(f"Expected Git commit must be a full 40-character hash: {expected_git_commit!r}")
    app_path = release_path / APP_NAME
    sidecar_path = release_path / SIDECAR_EXE
    if not app_path.is_file():
        raise ReleaseError(f"Candidate app is missing: {app_path}")
    if not sidecar_path.is_file():
        raise ReleaseError(f"Candidate sidecar is missing: {sidecar_path}")
    _require_regular_file(app_path, "Candidate app")
    _require_regular_file(sidecar_path, "Candidate sidecar")

    manifest, manifest_path = _load_manifest(release_path)
    manifest_commit = str(manifest.get("git_commit") or "").strip()
    if manifest_commit != expected_git_commit:
        raise ReleaseError(
            f"Candidate manifest commit {manifest_commit!r} does not match HEAD {expected_git_commit!r}"
        )
    for field in ("contract_version", "source_fingerprint", "executable_sha256"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise ReleaseError(f"Candidate manifest is missing {field}: {manifest_path}")
    if not isinstance(manifest.get("ledger_schema_version"), int):
        raise ReleaseError(f"Candidate manifest has no numeric ledger_schema_version: {manifest_path}")

    source_hashes = manifest.get("source_hashes")
    if not isinstance(source_hashes, dict) or not source_hashes:
        raise ReleaseError(f"Candidate manifest has no source hashes: {manifest_path}")
    fingerprint_rows: list[str] = []
    for source_path, source_hash in source_hashes.items():
        if not isinstance(source_path, str) or not source_path.strip():
            raise ReleaseError(f"Candidate manifest has an invalid source path: {manifest_path}")
        if not isinstance(source_hash, str) or not source_hash.strip():
            raise ReleaseError(f"Candidate manifest has an invalid source hash: {manifest_path}")
        fingerprint_rows.append(f"{source_path}:{source_hash}")
    calculated_fingerprint = hashlib.sha256("\n".join(fingerprint_rows).encode("utf-8")).hexdigest().upper()
    if calculated_fingerprint != str(manifest["source_fingerprint"]).upper():
        raise ReleaseError("Candidate source fingerprint does not match its source hash manifest")

    sidecar_hash = _sha256_file(sidecar_path)
    if sidecar_hash != str(manifest["executable_sha256"]).upper():
        raise ReleaseError("Candidate sidecar hash does not match sidecar-manifest.json")

    return {
        "inventory": directory_inventory(release_path),
        "artifacts": {
            "app_sha256": _sha256_file(app_path),
            "sidecar_sha256": sidecar_hash,
            "manifest_sha256": _sha256_file(manifest_path),
            "contract_version": manifest["contract_version"],
            "ledger_schema_version": manifest["ledger_schema_version"],
            "source_fingerprint": manifest["source_fingerprint"],
            "git_commit": manifest_commit,
        },
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _create_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    """Atomically create a journal without racing another promotion process."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.claim-{uuid.uuid4().hex}")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise ReleaseError(
                f"An unfinished promotion exists: {path}. Finalize or roll it back first."
            ) from error
    finally:
        if temporary.exists():
            temporary.unlink()


def _remove_generated_tree(path: Path, allowed_root: Path, prefix: str) -> None:
    safe_path = _require_direct_child(path, allowed_root, "generated release directory")
    if not safe_path.name.startswith(prefix):
        raise ReleaseError(f"Refusing to remove unexpected generated directory: {safe_path}")
    if safe_path.exists():
        if not safe_path.is_dir() or _is_link_like(safe_path):
            raise ReleaseError(f"Generated release path is not a safe directory: {safe_path}")
        directory_inventory(safe_path)
        shutil.rmtree(safe_path)


def stage_candidate(
    *,
    project_root: str | Path,
    app_exe: str | Path,
    sidecar_dir: str | Path,
    candidate_dir: str | Path,
    expected_git_commit: str,
    _lock_held: bool = False,
) -> dict[str, Any]:
    project = _project_root(project_root)
    if not _lock_held:
        with _promotion_lock(project):
            return stage_candidate(
                project_root=project,
                app_exe=app_exe,
                sidecar_dir=sidecar_dir,
                candidate_dir=candidate_dir,
                expected_git_commit=expected_git_commit,
                _lock_held=True,
            )
    build_root = project / "build"
    candidate = _canonical_candidate_path(project, candidate_dir)
    app_source = _resolved(app_exe)
    sidecar_source = _resolved(sidecar_dir)
    if not app_source.is_file():
        raise ReleaseError(f"Built Tauri executable is missing: {app_source}")
    if not sidecar_source.is_dir():
        raise ReleaseError(f"Built sidecar directory is missing: {sidecar_source}")
    _require_regular_file(app_source, "Built Tauri executable")
    directory_inventory(sidecar_source)
    transaction = build_root / TRANSACTION_FILE_NAME
    if transaction.exists():
        raise ReleaseError(
            f"An unfinished promotion exists: {transaction}. Finalize or roll it back first."
        )

    build_root.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    replacement = candidate.with_name(f".{candidate.name}.replacement-{token}")
    previous = candidate.with_name(f".{candidate.name}.previous-{token}")
    replacement.mkdir()
    moved_previous = False
    try:
        shutil.copy2(app_source, replacement / APP_NAME)
        shutil.copytree(sidecar_source, replacement / "python-server")
        (replacement / "Start.bat").write_text(
            '@echo off\r\nstart "" "%~dp0Product Atelier.exe"\r\n',
            encoding="utf-8",
            newline="",
        )
        candidate_info = validate_candidate(replacement, expected_git_commit)

        if candidate.exists():
            if not candidate.is_dir() or candidate.is_symlink():
                raise ReleaseError(f"Existing candidate is not a safe directory: {candidate}")
            os.replace(candidate, previous)
            moved_previous = True
        os.replace(replacement, candidate)
        if moved_previous:
            _remove_generated_tree(previous, build_root, f".{candidate.name}.previous-")
        return candidate_info
    except Exception:
        if replacement.exists():
            _remove_generated_tree(replacement, build_root, f".{candidate.name}.replacement-")
        if moved_previous and previous.exists() and not candidate.exists():
            os.replace(previous, candidate)
        raise


def _canonical_transaction_path(project: Path, transaction_path: str | Path) -> Path:
    transaction = _require_direct_child(
        Path(transaction_path), project / "build", "promotion transaction"
    )
    expected = project / "build" / TRANSACTION_FILE_NAME
    if transaction != expected:
        raise ReleaseError(f"Promotion transaction must use the canonical path: {expected}")
    return transaction


def _validate_backup_path(
    backup_dir: Path, project: Path, *, allow_existing: bool = False
) -> Path:
    backup = _resolved(backup_dir)
    if not backup.name.startswith("release-before-"):
        raise ReleaseError(f"Backup directory must start with 'release-before-': {backup}")
    if backup.parent == Path(backup.anchor) or len(backup.parts) < 3:
        raise ReleaseError(f"Backup directory is too broad: {backup}")
    if backup == _resolved(Path.home()):
        raise ReleaseError(f"Backup directory may not be the user home directory: {backup}")
    if backup == project or project in backup.parents or backup in project.parents:
        raise ReleaseError(f"Backup directory may not overlap the project tree: {backup}")
    if backup.exists() and not allow_existing:
        raise ReleaseError(f"Backup directory already exists: {backup}")
    if backup.exists() and (not backup.is_dir() or backup.is_symlink()):
        raise ReleaseError(f"Backup path is not a safe directory: {backup}")
    return backup


def _evidence_path(data: dict[str, Any], project: Path) -> Path:
    backup_value = data.get("backup_dir")
    if backup_value:
        backup = _resolved(backup_value)
        return backup.parent / f"{backup.name}-promotion-evidence.json"
    return project / "build" / "portable-promotion-evidence.json"


def _promotion_evidence(
    data: dict[str, Any], *, status: str, reason: str | None = None, failed_path: Path | None = None
) -> dict[str, Any]:
    previous = data.get("previous")
    evidence = {
        "format_version": TRANSACTION_FORMAT_VERSION,
        "transaction_id": data["transaction_id"],
        "status": status,
        "recorded_at_utc": _utc_now(),
        "project_root": data["project_root"],
        "git_commit": data["git_commit"],
        "candidate_dir": data["candidate_dir"],
        "portable_dir": data["portable_dir"],
        "backup_dir": data.get("backup_dir"),
        "candidate": data["candidate"],
        "previous": {"inventory": previous["inventory"]} if previous else None,
    }
    if reason:
        evidence["reason"] = reason
    if failed_path:
        evidence["failed_candidate_dir"] = str(failed_path)
    return evidence


def _record_evidence(project: Path, data: dict[str, Any], evidence: dict[str, Any]) -> Path:
    evidence_path = _evidence_path(data, project)
    _write_json_atomic(evidence_path, evidence)
    _write_json_atomic(project / "build" / "last-portable-promotion.json", evidence)
    return evidence_path


def _read_transaction(project: Path, transaction_path: str | Path) -> tuple[Path, dict[str, Any]]:
    transaction = _canonical_transaction_path(project, transaction_path)
    try:
        data = json.loads(transaction.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise ReleaseError(f"Invalid promotion transaction: {transaction}: {error}") from error
    if not isinstance(data, dict) or data.get("format_version") != TRANSACTION_FORMAT_VERSION:
        raise ReleaseError(f"Unsupported promotion transaction: {transaction}")
    if not isinstance(data.get("project_root"), str) or _resolved(data["project_root"]) != project:
        raise ReleaseError(f"Promotion transaction belongs to another project: {transaction}")
    if data.get("phase") not in TRANSACTION_PHASES:
        raise ReleaseError(f"Promotion transaction has an invalid phase: {transaction}")
    if not isinstance(data.get("transaction_id"), str) or not data["transaction_id"]:
        raise ReleaseError(f"Promotion transaction has no transaction id: {transaction}")

    build_root = project / "build"
    release_root = project / "release"
    candidate = _canonical_candidate_path(project, data.get("candidate_dir", ""))
    portable = _canonical_portable_path(project, data.get("portable_dir", ""))
    previous = _require_direct_child(Path(data.get("previous_dir", "")), release_root, "previous portable directory")
    replacement = _require_direct_child(
        Path(data.get("replacement_dir", "")), release_root, "promotion replacement"
    )
    transaction_id = data["transaction_id"]
    if previous.name != f".{portable.name}.previous-{transaction_id}":
        raise ReleaseError(f"Promotion transaction has an invalid previous directory: {transaction}")
    if replacement.name != f".{portable.name}.replacement-{transaction_id}":
        raise ReleaseError(f"Promotion transaction has an invalid replacement directory: {transaction}")
    if not isinstance(data.get("candidate"), dict) or not isinstance(
        data["candidate"].get("inventory"), dict
    ):
        raise ReleaseError(f"Promotion transaction has no candidate inventory: {transaction}")
    previous_info = data.get("previous")
    if previous_info is not None and (
        not isinstance(previous_info, dict) or not isinstance(previous_info.get("inventory"), dict)
    ):
        raise ReleaseError(f"Promotion transaction has an invalid previous inventory: {transaction}")
    if previous_info is not None and not isinstance(previous_info.get("cleanup_entries"), list):
        raise ReleaseError(f"Promotion transaction has no previous cleanup manifest: {transaction}")
    backup_value = data.get("backup_dir")
    if previous_info is not None:
        if not isinstance(backup_value, str) or not backup_value:
            raise ReleaseError(f"Promotion transaction has no durable backup path: {transaction}")
        backup = _validate_backup_path(Path(backup_value), project, allow_existing=True)
        backup_replacement_value = data.get("backup_replacement_dir")
        if not isinstance(backup_replacement_value, str) or not backup_replacement_value:
            raise ReleaseError(f"Promotion transaction has no backup replacement path: {transaction}")
        backup_replacement = _resolved(backup_replacement_value)
        expected_backup_replacement = backup.with_name(
            f".{backup.name}.replacement-{transaction_id}"
        )
        if backup_replacement != expected_backup_replacement:
            raise ReleaseError(f"Promotion transaction has an invalid backup replacement: {transaction}")
        data["backup_dir"] = str(backup)
        data["backup_replacement_dir"] = str(backup_replacement)
    elif backup_value is not None:
        raise ReleaseError(f"Initial promotion unexpectedly declares a backup: {transaction}")
    elif data.get("backup_replacement_dir") is not None:
        raise ReleaseError(f"Initial promotion unexpectedly declares a backup replacement: {transaction}")
    if not isinstance(data.get("git_commit"), str) or not data["git_commit"]:
        raise ReleaseError(f"Promotion transaction has no Git commit: {transaction}")

    # Resolve every journal path above before any caller is allowed to mutate it.
    data["candidate_dir"] = str(candidate)
    data["portable_dir"] = str(portable)
    data["previous_dir"] = str(previous)
    data["replacement_dir"] = str(replacement)
    return transaction, data


def _inventory_state(
    path: Path,
    *,
    candidate_inventory: dict[str, Any],
    previous_inventory: dict[str, Any] | None,
    label: str,
) -> str:
    if not path.exists():
        return "missing"
    if not path.is_dir() or _is_link_like(path):
        raise ReleaseError(f"{label} is not a safe directory: {path}")
    actual = directory_inventory(path)
    candidate_match = all(
        actual.get(key) == candidate_inventory.get(key)
        for key in ("file_count", "directory_count", "total_bytes", "tree_sha256")
    )
    previous_match = previous_inventory is not None and all(
        actual.get(key) == previous_inventory.get(key)
        for key in ("file_count", "directory_count", "total_bytes", "tree_sha256")
    )
    if candidate_match and previous_match:
        return "identical"
    if candidate_match:
        return "candidate"
    if previous_match:
        return "previous"
    raise ReleaseError(f"{label} has an unknown file-tree hash; preserving the recovery scene: {path}")


def _restore_backup(
    *,
    backup: Path,
    portable: Path,
    release_root: Path,
    expected: dict[str, Any],
    cleanup_entries: list[dict[str, Any]],
    transaction_id: str,
) -> None:
    _verify_inventory(backup, expected, "durable backup before restore")
    recovery = portable.with_name(f".{portable.name}.recovery-{transaction_id}")
    if recovery.exists():
        _validate_cleanup_subset(recovery, cleanup_entries, "partial backup recovery")
        _remove_generated_tree(recovery, release_root, f".{portable.name}.recovery-")
    shutil.copytree(backup, recovery)
    try:
        _verify_inventory(recovery, expected, "copied backup recovery")
        os.replace(recovery, portable)
    finally:
        if recovery.exists() and _inventory_matches(recovery, expected):
            _remove_generated_tree(recovery, release_root, f".{portable.name}.recovery-")


def begin_promotion(
    *,
    project_root: str | Path,
    candidate_dir: str | Path,
    portable_dir: str | Path,
    backup_dir: str | Path,
    transaction_path: str | Path,
    expected_git_commit: str,
    _lock_held: bool = False,
) -> dict[str, Any]:
    project = _project_root(project_root)
    if not _lock_held:
        with _promotion_lock(project):
            return begin_promotion(
                project_root=project,
                candidate_dir=candidate_dir,
                portable_dir=portable_dir,
                backup_dir=backup_dir,
                transaction_path=transaction_path,
                expected_git_commit=expected_git_commit,
                _lock_held=True,
            )
    build_root = project / "build"
    release_root = project / "release"
    candidate = _canonical_candidate_path(project, candidate_dir)
    portable = _canonical_portable_path(project, portable_dir)
    transaction = _canonical_transaction_path(project, transaction_path)
    backup = _validate_backup_path(Path(backup_dir), project)

    candidate_info = validate_candidate(candidate, expected_git_commit)
    previous_info = None
    if portable.exists():
        if not portable.is_dir() or portable.is_symlink():
            raise ReleaseError(f"Formal portable path is not a safe directory: {portable}")
        previous_info = {
            "inventory": directory_inventory(portable),
            "cleanup_entries": _tree_entries(portable),
        }

    build_root.mkdir(parents=True, exist_ok=True)
    release_root.mkdir(parents=True, exist_ok=True)
    backup.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    replacement = portable.with_name(f".{portable.name}.replacement-{token}")
    previous = portable.with_name(f".{portable.name}.previous-{token}")
    backup_replacement = backup.with_name(f".{backup.name}.replacement-{token}")
    data: dict[str, Any] = {
        "format_version": TRANSACTION_FORMAT_VERSION,
        "transaction_id": token,
        "phase": "prepared",
        "started_at_utc": _utc_now(),
        "project_root": str(project),
        "git_commit": expected_git_commit,
        "candidate_dir": str(candidate),
        "portable_dir": str(portable),
        "backup_dir": str(backup) if previous_info else None,
        "backup_replacement_dir": str(backup_replacement) if previous_info else None,
        "replacement_dir": str(replacement),
        "previous_dir": str(previous),
        "candidate": candidate_info,
        "previous": previous_info,
    }
    _create_json_exclusive(transaction, data)

    try:
        if previous_info:
            shutil.copytree(portable, backup_replacement)
            _verify_inventory(
                backup_replacement, previous_info["inventory"], "portable backup"
            )
            os.replace(backup_replacement, backup)
        data["phase"] = "backed_up"
        _write_json_atomic(transaction, data)

        shutil.copytree(candidate, replacement)
        _verify_inventory(replacement, candidate_info["inventory"], "promotion replacement")
        data["phase"] = "candidate_copied"
        _write_json_atomic(transaction, data)

        if previous_info:
            os.replace(portable, previous)
        data["phase"] = "previous_moved"
        _write_json_atomic(transaction, data)

        os.replace(replacement, portable)
        _verify_inventory(portable, candidate_info["inventory"], "promoted portable release")
        data["phase"] = "promoted"
        _write_json_atomic(transaction, data)
        return data
    except Exception as error:
        try:
            rollback_promotion(
                project_root=project,
                transaction_path=transaction,
                reason=f"promotion failed before smoke verification: {error}",
                expected_git_commit=expected_git_commit,
                expected_transaction_id=token,
                _lock_held=True,
            )
        except Exception as rollback_error:
            raise ReleaseError(
                f"Promotion failed and automatic rollback also failed. "
                f"Keep {transaction} for recovery. Promotion error: {error}; "
                f"rollback error: {rollback_error}"
            ) from rollback_error
        raise


def rollback_promotion(
    *,
    project_root: str | Path,
    transaction_path: str | Path,
    reason: str,
    expected_git_commit: str | None = None,
    expected_transaction_id: str | None = None,
    _lock_held: bool = False,
) -> dict[str, Any]:
    project = _project_root(project_root)
    if not _lock_held:
        with _promotion_lock(project):
            return rollback_promotion(
                project_root=project,
                transaction_path=transaction_path,
                reason=reason,
                expected_git_commit=expected_git_commit,
                expected_transaction_id=expected_transaction_id,
                _lock_held=True,
            )
    transaction, data = _read_transaction(project, transaction_path)
    if expected_git_commit and data["git_commit"].lower() != expected_git_commit.lower():
        raise ReleaseError("Promotion transaction does not match the expected Git commit")
    if expected_transaction_id and data["transaction_id"] != expected_transaction_id:
        raise ReleaseError("Promotion transaction does not match the expected transaction id")
    if data.get("phase") in {"finalizing", "finalized"}:
        raise ReleaseError(
            "Promotion has crossed the finalization decision point; rerun finalize instead of rollback"
        )

    release_root = project / "release"
    build_root = project / "build"
    portable = _canonical_portable_path(project, data["portable_dir"])
    previous = _require_direct_child(Path(data["previous_dir"]), release_root, "previous portable directory")
    replacement = _require_direct_child(
        Path(data["replacement_dir"]), release_root, "promotion replacement"
    )
    candidate_inventory = data["candidate"]["inventory"]
    previous_info = data.get("previous")
    previous_inventory = previous_info["inventory"] if previous_info else None

    portable_state = _inventory_state(
        portable,
        candidate_inventory=candidate_inventory,
        previous_inventory=previous_inventory,
        label="formal portable directory",
    )
    previous_state = _inventory_state(
        previous,
        candidate_inventory=candidate_inventory,
        previous_inventory=previous_inventory,
        label="previous portable directory",
    )
    if previous_state not in {"missing", "previous", "identical"}:
        raise ReleaseError(f"Previous directory contains the candidate unexpectedly: {previous}")
    if replacement.exists():
        # This token-derived directory is build output only.  A partial copy is
        # safe to discard after the live release has been restored.
        directory_inventory(replacement)

    backup = None
    backup_replacement = None
    if previous_info:
        backup = _resolved(data["backup_dir"])
        backup_replacement = _resolved(data["backup_replacement_dir"])
        if backup.exists():
            _verify_inventory(backup, previous_inventory, "durable backup")
        if backup_replacement.exists():
            directory_inventory(backup_replacement)
            backup_replacement_complete = _inventory_matches(
                backup_replacement, previous_inventory
            )
            if not backup.exists() and backup_replacement_complete:
                os.replace(backup_replacement, backup)
        recovery = portable.with_name(
            f".{portable.name}.recovery-{data['transaction_id']}"
        )
        if recovery.exists():
            _validate_cleanup_subset(
                recovery,
                previous_info["cleanup_entries"],
                "partial backup recovery",
            )

    failed_path = build_root / f"failed-portable-candidate-{data['transaction_id']}"
    if failed_path.exists():
        if not _inventory_matches(failed_path, candidate_inventory):
            raise ReleaseError(f"Failed-candidate path has an unknown file-tree hash: {failed_path}")

    if previous_info:
        if previous_state in {"previous", "identical"}:
            if portable_state in {"candidate", "identical"}:
                if failed_path.exists():
                    raise ReleaseError(f"Failed-candidate path already exists while candidate is still live: {failed_path}")
                os.replace(portable, failed_path)
            elif portable_state == "previous":
                raise ReleaseError("Both live and previous directories contain the old release; preserving both")
            os.replace(previous, portable)
        elif portable_state in {"candidate", "missing"}:
            if portable_state == "candidate":
                if failed_path.exists():
                    raise ReleaseError(f"Failed-candidate path already exists while candidate is still live: {failed_path}")
                os.replace(portable, failed_path)
            if not backup or not backup.exists():
                raise ReleaseError("Previous release is unavailable and the durable backup cannot restore it")
            _restore_backup(
                backup=backup,
                portable=portable,
                release_root=release_root,
                expected=previous_inventory,
                cleanup_entries=previous_info["cleanup_entries"],
                transaction_id=data["transaction_id"],
            )
        elif portable_state == "identical":
            # With no internal previous directory, identical bytes are already
            # a valid restored old release; no destructive guess is needed.
            pass
        _verify_inventory(portable, previous_inventory, "restored formal release")
    else:
        if previous_state != "missing":
            raise ReleaseError(f"Initial promotion unexpectedly has a previous directory: {previous}")
        if portable_state == "candidate":
            if failed_path.exists():
                raise ReleaseError(f"Failed-candidate path already exists while candidate is still live: {failed_path}")
            os.replace(portable, failed_path)
        elif portable_state != "missing":
            raise ReleaseError("Initial promotion found an unknown formal release")
        if portable.exists():
            raise ReleaseError("Initial promotion rollback did not restore the absent formal path")

    if replacement.exists():
        _remove_generated_tree(replacement, release_root, f".{portable.name}.replacement-")
    if backup_replacement and backup_replacement.exists():
        _remove_generated_tree(
            backup_replacement, backup_replacement.parent, f".{backup.name}.replacement-"
        )

    data["phase"] = "rolled_back"
    data["rolled_back_at_utc"] = _utc_now()
    data["rollback_reason"] = reason
    data["failed_candidate_dir"] = str(failed_path) if failed_path.exists() else None
    _write_json_atomic(transaction, data)
    evidence = _promotion_evidence(
        data,
        status="rolled_back",
        reason=reason,
        failed_path=failed_path if failed_path.exists() else None,
    )
    evidence["outcome"] = {
        "formal_inventory": directory_inventory(portable) if portable.exists() else None,
        "backup_inventory": directory_inventory(backup) if backup and backup.exists() else None,
    }
    evidence_path = _record_evidence(project, data, evidence)
    transaction.unlink()
    return {**evidence, "evidence_path": str(evidence_path)}


def _replay_finalized_receipt(
    project: Path,
    expected_git_commit: str | None,
    expected_transaction_id: str | None,
) -> dict[str, Any]:
    if not expected_git_commit or not re.fullmatch(r"[0-9a-fA-F]{40}", expected_git_commit):
        raise ReleaseError(
            "A full expected Git commit is required to replay finalized evidence"
        )
    if not expected_transaction_id or not re.fullmatch(r"[0-9a-f]{32}", expected_transaction_id):
        raise ReleaseError(
            "The exact promotion transaction id is required to replay finalized evidence"
        )
    receipt_path = project / "build" / "last-portable-promotion.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise ReleaseError(f"Finalized promotion receipt is unavailable: {receipt_path}") from error
    if not isinstance(receipt, dict) or receipt.get("format_version") != TRANSACTION_FORMAT_VERSION:
        raise ReleaseError(f"Finalized promotion receipt is invalid: {receipt_path}")
    if receipt.get("status") != "finalized":
        raise ReleaseError(f"Latest promotion receipt is not finalized: {receipt_path}")
    if not isinstance(receipt.get("project_root"), str) or _resolved(receipt["project_root"]) != project:
        raise ReleaseError(f"Finalized promotion receipt belongs to another project: {receipt_path}")
    transaction_id = receipt.get("transaction_id")
    if not isinstance(transaction_id, str) or not transaction_id:
        raise ReleaseError(f"Finalized promotion receipt has no transaction id: {receipt_path}")
    if transaction_id != expected_transaction_id:
        raise ReleaseError("Finalized promotion receipt does not match the expected transaction id")
    if not isinstance(receipt.get("git_commit"), str) or receipt["git_commit"].lower() != expected_git_commit.lower():
        raise ReleaseError("Finalized promotion receipt does not match the expected Git commit")
    portable = _canonical_portable_path(project, receipt.get("portable_dir", ""))
    candidate = receipt.get("candidate")
    if not isinstance(candidate, dict) or not isinstance(candidate.get("inventory"), dict):
        raise ReleaseError(f"Finalized promotion receipt has no candidate inventory: {receipt_path}")
    _verify_inventory(portable, candidate["inventory"], "formal release from finalized receipt")
    previous = receipt.get("previous")
    if previous:
        if not isinstance(previous, dict) or not isinstance(previous.get("inventory"), dict):
            raise ReleaseError(f"Finalized promotion receipt has an invalid previous inventory: {receipt_path}")
        backup_value = receipt.get("backup_dir")
        if not isinstance(backup_value, str) or not backup_value:
            raise ReleaseError(f"Finalized promotion receipt has no durable backup: {receipt_path}")
        backup = _validate_backup_path(Path(backup_value), project, allow_existing=True)
        _verify_inventory(backup, previous["inventory"], "durable backup from finalized receipt")
    evidence_path = _evidence_path(receipt, project)
    if not evidence_path.is_file():
        raise ReleaseError(f"Finalized promotion evidence is missing: {evidence_path}")
    try:
        durable_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise ReleaseError(f"Finalized promotion evidence is invalid: {evidence_path}") from error
    if (
        not isinstance(durable_evidence, dict)
        or durable_evidence != receipt
    ):
        raise ReleaseError(f"Finalized promotion evidence does not match its receipt: {evidence_path}")
    return {**receipt, "evidence_path": str(evidence_path), "replayed": True}


def finalize_promotion(
    *,
    project_root: str | Path,
    transaction_path: str | Path,
    expected_git_commit: str | None = None,
    expected_transaction_id: str | None = None,
    _lock_held: bool = False,
) -> dict[str, Any]:
    project = _project_root(project_root)
    if not _lock_held:
        with _promotion_lock(project):
            return finalize_promotion(
                project_root=project,
                transaction_path=transaction_path,
                expected_git_commit=expected_git_commit,
                expected_transaction_id=expected_transaction_id,
                _lock_held=True,
            )
    transaction = _canonical_transaction_path(project, transaction_path)
    if not transaction.exists():
        return _replay_finalized_receipt(
            project, expected_git_commit, expected_transaction_id
        )
    transaction, data = _read_transaction(project, transaction)
    if expected_git_commit and data["git_commit"].lower() != expected_git_commit.lower():
        raise ReleaseError("Promotion transaction does not match the expected Git commit")
    if expected_transaction_id and data["transaction_id"] != expected_transaction_id:
        raise ReleaseError("Promotion transaction does not match the expected transaction id")
    if data.get("phase") not in {"promoted", "finalizing", "finalized"}:
        raise ReleaseError(f"Promotion is not ready to finalize; phase={data.get('phase')!r}")

    release_root = project / "release"
    portable = _canonical_portable_path(project, data["portable_dir"])
    previous = _require_direct_child(Path(data["previous_dir"]), release_root, "previous portable directory")
    _verify_inventory(portable, data["candidate"]["inventory"], "formal release before finalize")

    previous_info = data.get("previous")
    backup_value = data.get("backup_dir")
    if previous_info:
        if previous.exists():
            if data.get("phase") == "finalized":
                _validate_cleanup_subset(
                    previous,
                    previous_info["cleanup_entries"],
                    "previous formal release",
                )
            else:
                _verify_inventory(previous, previous_info["inventory"], "previous formal release")
        elif data.get("phase") != "finalized":
            raise ReleaseError(f"Previous formal release is missing before finalize: {previous}")
        if not backup_value:
            raise ReleaseError("Promotion transaction has no durable backup path")
        backup_inventory = _verify_inventory(
            _resolved(backup_value), previous_info["inventory"], "durable backup"
        )
    else:
        backup_inventory = None
        if previous.exists():
            raise ReleaseError(f"Initial promotion unexpectedly has a previous release: {previous}")

    if data.get("phase") == "promoted":
        data["phase"] = "finalizing"
        data["finalizing_at_utc"] = _utc_now()
        _write_json_atomic(transaction, data)

    evidence = _promotion_evidence(data, status="finalized")
    evidence["outcome"] = {
        "formal_inventory": directory_inventory(portable),
        "backup_inventory": backup_inventory,
    }
    evidence_path = _record_evidence(project, data, evidence)

    if data.get("phase") != "finalized":
        data["phase"] = "finalized"
        data["finalized_at_utc"] = _utc_now()
        _write_json_atomic(transaction, data)

    if previous.exists():
        _remove_generated_tree(previous, release_root, f".{portable.name}.previous-")
    transaction.unlink()
    return {**evidence, "evidence_path": str(evidence_path)}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage = subparsers.add_parser("stage", help="assemble an isolated portable candidate")
    stage.add_argument("--project-root", required=True)
    stage.add_argument("--app-exe", required=True)
    stage.add_argument("--sidecar-dir", required=True)
    stage.add_argument("--candidate-dir", required=True)
    stage.add_argument("--git-commit", required=True)

    begin = subparsers.add_parser("begin", help="back up and promote a verified candidate")
    begin.add_argument("--project-root", required=True)
    begin.add_argument("--candidate-dir", required=True)
    begin.add_argument("--portable-dir", required=True)
    begin.add_argument("--backup-dir", required=True)
    begin.add_argument("--transaction", required=True)
    begin.add_argument("--git-commit", required=True)

    finalize = subparsers.add_parser("finalize", help="commit a promotion after formal smoke")
    finalize.add_argument("--project-root", required=True)
    finalize.add_argument("--transaction", required=True)
    finalize.add_argument("--git-commit", required=True)
    finalize.add_argument("--transaction-id", required=True)

    rollback = subparsers.add_parser("rollback", help="restore the previous formal release")
    rollback.add_argument("--project-root", required=True)
    rollback.add_argument("--transaction", required=True)
    rollback.add_argument("--reason", required=True)
    rollback.add_argument("--git-commit", required=True)
    rollback.add_argument("--transaction-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "stage":
            result = stage_candidate(
                project_root=args.project_root,
                app_exe=args.app_exe,
                sidecar_dir=args.sidecar_dir,
                candidate_dir=args.candidate_dir,
                expected_git_commit=args.git_commit,
            )
        elif args.command == "begin":
            result = begin_promotion(
                project_root=args.project_root,
                candidate_dir=args.candidate_dir,
                portable_dir=args.portable_dir,
                backup_dir=args.backup_dir,
                transaction_path=args.transaction,
                expected_git_commit=args.git_commit,
            )
        elif args.command == "finalize":
            result = finalize_promotion(
                project_root=args.project_root,
                transaction_path=args.transaction,
                expected_git_commit=args.git_commit,
                expected_transaction_id=args.transaction_id,
            )
        else:
            result = rollback_promotion(
                project_root=args.project_root,
                transaction_path=args.transaction,
                reason=args.reason,
                expected_git_commit=args.git_commit,
                expected_transaction_id=args.transaction_id,
            )
    except (OSError, ReleaseError, KeyError, TypeError, ValueError) as error:
        print(f"portable release error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
