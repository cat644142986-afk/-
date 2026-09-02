#!/usr/bin/env python3
"""Seed a deterministic result-review fixture in a fresh isolated runtime root."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import io
import json
import os
import sqlite3
import stat
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ISOLATED_DATA_PREFIX = "ProductAtelier-launch-and-shoot-"
FIXTURE_MANIFEST_NAME = "formal-webview-fixture.json"
LEDGER_NAME = "atelier.sqlite3"
SEED_CLAIM_NAME = ".seed-feedback-in-progress"
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_DIRECTORY = 0x10

if os.name == "nt":
    from ctypes import wintypes

    FILE_LIST_DIRECTORY = 0x0001
    FILE_READ_ATTRIBUTES = 0x0080
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    CREATE_NEW = 1
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    _kernel32.WriteFile.restype = wintypes.BOOL
    _kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _kernel32.FlushFileBuffers.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

if __package__:
    from python.atelier_ledger import AtelierLedger, idempotent_id
    from python.memory_engine import MemoryEngine
else:
    python_root = str(PROJECT_ROOT / "python")
    if python_root not in sys.path:
        sys.path.insert(0, python_root)
    from atelier_ledger import AtelierLedger, idempotent_id
    from memory_engine import MemoryEngine


class SeedSafetyError(RuntimeError):
    """Raised when a fixture target is not a fresh launcher-owned directory."""


@dataclass(frozen=True)
class _FileIdentity:
    volume: int
    index_high: int
    index_low: int


def _windows_error(message: str) -> SeedSafetyError:
    return SeedSafetyError(f"{message}: {ctypes.WinError(ctypes.get_last_error())}")


def _open_windows_handle(
    path: Path,
    *,
    access: int,
    share: int,
    creation: int,
    flags: int,
) -> int:
    handle = _kernel32.CreateFileW(
        str(path), access, share, None, creation, flags, None
    )
    if handle == INVALID_HANDLE_VALUE:
        raise _windows_error(f"Could not open fixture path {path}")
    return int(handle)


def _windows_file_information(handle: int) -> _ByHandleFileInformation:
    information = _ByHandleFileInformation()
    if not _kernel32.GetFileInformationByHandle(
        handle, ctypes.byref(information)
    ):
        raise _windows_error("Could not inspect pinned fixture handle")
    return information


def _windows_identity(information: _ByHandleFileInformation) -> _FileIdentity:
    return _FileIdentity(
        int(information.volume_serial_number),
        int(information.file_index_high),
        int(information.file_index_low),
    )


def _windows_final_path(handle: int) -> Path:
    buffer = ctypes.create_unicode_buffer(32768)
    length = _kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not length or length >= len(buffer):
        raise _windows_error("Could not resolve pinned fixture directory")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value).resolve(strict=True)


class _ExclusiveFile:
    def __init__(self, path: Path, descriptor: int, *, windows: bool):
        self.path = path
        self._descriptor = descriptor
        self._windows = windows
        self._closed = False

    def verify_regular_single_link(self) -> None:
        if self._windows:
            information = _windows_file_information(self._descriptor)
            if information.file_attributes & (
                FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise SeedSafetyError(f"Fixture file is not ordinary: {self.path}")
            links = int(information.number_of_links)
        else:
            information = os.fstat(self._descriptor)
            if not stat.S_ISREG(information.st_mode):
                raise SeedSafetyError(f"Fixture file is not ordinary: {self.path}")
            links = int(information.st_nlink)
        if links != 1:
            raise SeedSafetyError(
                f"Fixture file must have exactly one hard link: {self.path}"
            )

    def write(self, data: bytes) -> None:
        if self._windows:
            offset = 0
            while offset < len(data):
                chunk = data[offset : offset + 1024 * 1024]
                buffer = ctypes.create_string_buffer(chunk)
                written = wintypes.DWORD()
                if not _kernel32.WriteFile(
                    self._descriptor,
                    buffer,
                    len(chunk),
                    ctypes.byref(written),
                    None,
                ):
                    raise _windows_error(f"Could not write fixture file {self.path}")
                if written.value <= 0:
                    raise SeedSafetyError(f"Short write while publishing {self.path}")
                offset += int(written.value)
            return

        view = memoryview(data)
        while view:
            written = os.write(self._descriptor, view)
            if written <= 0:
                raise SeedSafetyError(f"Short write while publishing {self.path}")
            view = view[written:]

    def flush(self) -> None:
        if self._windows:
            if not _kernel32.FlushFileBuffers(self._descriptor):
                raise _windows_error(f"Could not flush fixture file {self.path}")
        else:
            os.fsync(self._descriptor)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._windows:
            _kernel32.CloseHandle(self._descriptor)
        else:
            os.close(self._descriptor)


class _PinnedDataDirectory:
    """Keep the validated root identity stable while fixture files are published."""

    def __init__(self, path: Path, *, isolated_root: bool = True):
        self.path = path
        self._isolated_root = isolated_root
        self._descriptor: int | None = None
        self._identity: _FileIdentity | tuple[int, int] | None = None
        self._directory_share = FILE_SHARE_READ if os.name == "nt" else 0

    def __enter__(self) -> Self:
        try:
            if os.name == "nt":
                self._descriptor = _open_windows_handle(
                    self.path,
                    access=FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES,
                    share=self._directory_share,
                    creation=OPEN_EXISTING,
                    flags=FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
                )
                information = _windows_file_information(self._descriptor)
                if not information.file_attributes & FILE_ATTRIBUTE_DIRECTORY:
                    raise SeedSafetyError("Fixture root is no longer a directory")
                if information.file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                    raise SeedSafetyError(
                        "Fixture data directory may not be a reparse point"
                    )
                self._identity = _windows_identity(information)
            else:
                flags = os.O_RDONLY
                flags |= getattr(os, "O_DIRECTORY", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                flags |= getattr(os, "O_CLOEXEC", 0)
                self._descriptor = os.open(self.path, flags)
                information = os.fstat(self._descriptor)
                if not stat.S_ISDIR(information.st_mode):
                    raise SeedSafetyError("Fixture root is no longer a directory")
                self._identity = (int(information.st_dev), int(information.st_ino))
            self.verify(expect_empty=True)
            return self
        except OSError as error:
            self._close()
            raise SeedSafetyError(
                f"Could not pin fixture directory {self.path}: {error}"
            ) from error
        except BaseException:
            self._close()
            raise

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._close()

    def _close(self) -> None:
        if self._descriptor is None:
            return
        if os.name == "nt":
            _kernel32.CloseHandle(self._descriptor)
        else:
            os.close(self._descriptor)
        self._descriptor = None

    def _current_path_identity(self) -> _FileIdentity | tuple[int, int]:
        if os.name == "nt":
            handle = _open_windows_handle(
                self.path,
                access=FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES,
                share=self._directory_share,
                creation=OPEN_EXISTING,
                flags=FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            )
            try:
                information = _windows_file_information(handle)
                if information.file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                    raise SeedSafetyError(
                        "Fixture data directory became a reparse point"
                    )
                if _comparison_key(_windows_final_path(handle)) != _comparison_key(
                    self.path
                ):
                    raise SeedSafetyError("Fixture root final path changed")
                return _windows_identity(information)
            finally:
                _kernel32.CloseHandle(handle)

        try:
            information = self.path.lstat()
        except OSError as error:
            raise SeedSafetyError(
                f"Could not revalidate fixture directory {self.path}: {error}"
            ) from error
        if not stat.S_ISDIR(information.st_mode) or stat.S_ISLNK(information.st_mode):
            raise SeedSafetyError("Fixture root identity changed")
        return (int(information.st_dev), int(information.st_ino))

    def verify(self, *, expect_empty: bool = False) -> None:
        if self._descriptor is None or self._identity is None:
            raise SeedSafetyError("Fixture directory guard is not active")
        if self._isolated_root:
            _validate_isolated_data_dir(self.path, require_empty=False)
        else:
            if (
                not self.path.is_absolute()
                or not self.path.exists()
                or not self.path.is_dir()
                or _is_link_like(self.path)
                or _comparison_key(self.path.resolve(strict=True))
                != _comparison_key(self.path)
            ):
                raise SeedSafetyError(
                    f"Fixture child directory identity changed: {self.path}"
                )
        if os.name == "nt":
            information = _windows_file_information(self._descriptor)
            if information.file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                raise SeedSafetyError("Pinned fixture root became a reparse point")
            pinned_identity: _FileIdentity | tuple[int, int] = _windows_identity(
                information
            )
            if _comparison_key(_windows_final_path(self._descriptor)) != _comparison_key(
                self.path
            ):
                raise SeedSafetyError("Pinned fixture root final path changed")
        else:
            information = os.fstat(self._descriptor)
            pinned_identity = (int(information.st_dev), int(information.st_ino))
        if pinned_identity != self._identity:
            raise SeedSafetyError("Pinned fixture root identity changed")
        if self._current_path_identity() != self._identity:
            raise SeedSafetyError("Fixture root identity changed")
        if expect_empty and any(self.path.iterdir()):
            raise SeedSafetyError(f"Refusing to seed a non-empty directory: {self.path}")

    def create_exclusive(self, name: str) -> _ExclusiveFile:
        if self._descriptor is None:
            raise SeedSafetyError("Fixture directory guard is not active")
        if not name or Path(name).name != name:
            raise SeedSafetyError(f"Fixture filename must be a direct child: {name}")
        path = self.path / name
        if os.name == "nt":
            descriptor = _open_windows_handle(
                path,
                access=GENERIC_WRITE | FILE_READ_ATTRIBUTES,
                share=0,
                creation=CREATE_NEW,
                flags=FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
            )
            return _ExclusiveFile(path, descriptor, windows=True)

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=self._descriptor)
        except OSError as error:
            raise SeedSafetyError(
                f"Could not exclusively create fixture file {path}: {error}"
            ) from error
        return _ExclusiveFile(path, descriptor, windows=False)

    def create_directory_exclusive(self, name: str) -> Path:
        if self._descriptor is None:
            raise SeedSafetyError("Fixture directory guard is not active")
        if not name or Path(name).name != name:
            raise SeedSafetyError(f"Fixture dirname must be a direct child: {name}")
        path = self.path / name
        try:
            if os.name == "nt":
                path.mkdir(exist_ok=False)
            else:
                os.mkdir(name, 0o700, dir_fd=self._descriptor)
        except OSError as error:
            raise SeedSafetyError(
                f"Could not exclusively create fixture directory {path}: {error}"
            ) from error
        return path

    def unlink_child(self, name: str) -> None:
        if self._descriptor is None:
            raise SeedSafetyError("Fixture directory guard is not active")
        if not name or Path(name).name != name:
            raise SeedSafetyError(f"Fixture filename must be a direct child: {name}")
        if os.name == "nt":
            (self.path / name).unlink()
        else:
            os.unlink(name, dir_fd=self._descriptor)

    def allow_child_mutations_after_nonempty(self) -> None:
        """Relax Windows sharing only after a held child makes reparse illegal."""
        if self._descriptor is None or self._identity is None:
            raise SeedSafetyError("Fixture directory guard is not active")
        if not any(self.path.iterdir()):
            raise SeedSafetyError(
                f"Fixture directory must be non-empty before relaxing its guard: {self.path}"
            )
        if os.name != "nt":
            return

        descriptor = _open_windows_handle(
            self.path,
            access=FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES,
            share=FILE_SHARE_READ | FILE_SHARE_WRITE,
            creation=OPEN_EXISTING,
            flags=FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
        )
        try:
            information = _windows_file_information(descriptor)
            if (
                information.file_attributes & FILE_ATTRIBUTE_REPARSE_POINT
                or _windows_identity(information) != self._identity
                or _comparison_key(_windows_final_path(descriptor))
                != _comparison_key(self.path)
            ):
                raise SeedSafetyError(
                    f"Fixture directory changed before guard relaxation: {self.path}"
                )
        except BaseException:
            _kernel32.CloseHandle(descriptor)
            raise

        previous = self._descriptor
        self._descriptor = descriptor
        self._directory_share = FILE_SHARE_READ | FILE_SHARE_WRITE
        _kernel32.CloseHandle(previous)
        self.verify()


class _GuardedChildDirectory:
    """Create and pin a child directory before any business file is written."""

    def __init__(self, parent: _PinnedDataDirectory, name: str):
        self._parent = parent
        self._name = name
        self._guard: _PinnedDataDirectory | None = None
        self._sentinel: _ExclusiveFile | None = None

    def __enter__(self) -> _PinnedDataDirectory:
        path = self._parent.create_directory_exclusive(self._name)
        guard = _PinnedDataDirectory(path, isolated_root=False)
        try:
            guard.__enter__()
            sentinel = guard.create_exclusive(SEED_CLAIM_NAME)
            sentinel.verify_regular_single_link()
            sentinel.flush()
            guard.verify()
            if {item.name for item in path.iterdir()} != {SEED_CLAIM_NAME}:
                raise SeedSafetyError(
                    f"Fixture child directory changed during setup: {path}"
                )
        except BaseException:
            if "sentinel" in locals():
                sentinel.close()
            guard.__exit__(None, None, None)
            raise
        self._guard = guard
        self._sentinel = sentinel
        return guard

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._guard is None or self._sentinel is None:
            return
        remove_sentinel = False
        try:
            if exc_type is None:
                self._guard.verify()
                self._guard.allow_child_mutations_after_nonempty()
                remove_sentinel = True
        finally:
            try:
                self._sentinel.close()
                if remove_sentinel:
                    self._guard.unlink_child(SEED_CLAIM_NAME)
            finally:
                self._guard.__exit__(exc_type, exc, traceback)


class _InMemoryAtelierLedger(AtelierLedger):
    """Run real ledger business logic without exposing SQLite sidecar paths."""

    def __init__(self) -> None:
        unique = uuid.uuid4().hex
        self._memory_uri = f"file:fixture-{unique}?mode=memory&cache=shared"
        self._keeper = self._new_connection(configure_storage=False)
        try:
            super().__init__(Path(tempfile.gettempdir()) / f"fixture-{unique}.sqlite3")
        except BaseException:
            self._keeper.close()
            raise

    def _new_connection(self, *, configure_storage: bool) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._memory_uri,
            uri=True,
            timeout=20,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 20000")
        connection.execute("PRAGMA temp_store = MEMORY")
        if configure_storage:
            connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _connect(self, *, configure_storage: bool = True) -> sqlite3.Connection:
        return self._new_connection(configure_storage=configure_storage)

    def _configure_storage(self) -> None:
        connection = self._connect(configure_storage=False)
        try:
            row = connection.execute("PRAGMA journal_mode = MEMORY").fetchone()
            mode = str(row[0]).casefold() if row is not None else ""
            if mode != "memory":
                raise SeedSafetyError(
                    f"In-memory ledger refused MEMORY journal mode: {mode!r}"
                )
            connection.execute("PRAGMA synchronous = NORMAL")
        finally:
            connection.close()

    def serialize_database(self) -> bytes:
        connection = self._connect(configure_storage=False)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]).casefold() != "ok":
                raise SeedSafetyError("In-memory ledger failed SQLite integrity_check")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise SeedSafetyError("In-memory ledger failed foreign_key_check")
            database = connection.serialize()
        finally:
            connection.close()
        if not database.startswith(b"SQLite format 3\x00"):
            raise SeedSafetyError("In-memory ledger serialization is not a SQLite database")
        return database

    def close(self) -> None:
        self._keeper.close()


def _is_link_like(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise SeedSafetyError(f"Could not inspect fixture path: {path}: {error}") from error
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction and is_junction():
        return True
    return bool(getattr(metadata, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def _comparison_key(path: Path) -> str:
    value = os.path.normcase(os.path.normpath(str(path)))
    return value.casefold() if os.name == "nt" else value


def _paths_overlap(first: Path, second: Path) -> bool:
    first_key = _comparison_key(first)
    second_key = _comparison_key(second)
    try:
        common = os.path.commonpath((first_key, second_key))
    except ValueError:
        return False
    return common in {first_key, second_key}


def _protected_roots() -> tuple[Path, ...]:
    roots = (
        PROJECT_ROOT,
        PROJECT_ROOT / "release",
        PROJECT_ROOT / "build" / "portable-candidate-current",
        PROJECT_ROOT / "release" / "ProductAtelier-Portable",
        Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        / "ProductAtelier",
    )
    return tuple(path.resolve(strict=False) for path in roots)


def _validate_isolated_data_dir(
    value: str | os.PathLike[str], *, require_empty: bool
) -> Path:
    requested = Path(value)
    if not requested.is_absolute():
        raise SeedSafetyError("Fixture data directory must be absolute")
    if not requested.exists() or not requested.is_dir():
        raise SeedSafetyError("Fixture data directory must already exist")
    if _is_link_like(requested):
        raise SeedSafetyError("Fixture data directory may not be a reparse point")

    temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    resolved = requested.resolve(strict=True)
    if resolved.parent != temp_root:
        raise SeedSafetyError(
            f"Fixture data directory must be a direct child of {temp_root}"
        )
    if not resolved.name.startswith(ISOLATED_DATA_PREFIX):
        raise SeedSafetyError(
            f"Fixture data directory must use prefix {ISOLATED_DATA_PREFIX}"
        )
    if any(_paths_overlap(resolved, protected) for protected in _protected_roots()):
        raise SeedSafetyError("Fixture data directory overlaps protected Product Atelier data")
    if require_empty and any(resolved.iterdir()):
        raise SeedSafetyError(f"Refusing to seed a non-empty directory: {resolved}")
    return resolved


def validate_fresh_isolated_data_dir(value: str | os.PathLike[str]) -> Path:
    return _validate_isolated_data_dir(value, require_empty=True)


def image_bytes(
    color: tuple[int, int, int] | tuple[int, int, int, int],
    size: tuple[int, int] = (640, 640),
) -> bytes:
    buffer = io.BytesIO()
    mode = "RGBA" if len(color) == 4 else "RGB"
    Image.new(mode, size, color).save(buffer, "PNG")
    return buffer.getvalue()


def _write_bytes_exclusive(
    guard: _PinnedDataDirectory, name: str, data: bytes
) -> str:
    digest = hashlib.sha256(data).hexdigest()
    _write_guarded_exclusive(guard, name, data)
    return digest


def seed_job(
    ledger: AtelierLedger,
    assets: _PinnedDataDirectory,
    output: _PinnedDataDirectory,
    index: int,
) -> dict[str, Any]:
    source_data = image_bytes((220, 90 + index * 20, 45))
    source_name = f"checkpoint-source-{index}.png"
    source_sha256 = _write_bytes_exclusive(assets, source_name, source_data)
    source_path = assets.path / source_name
    source = ledger.register_workspace_asset(
        sha256=source_sha256,
        storage_path=str(source_path),
        mime="image/png",
        size_bytes=len(source_data),
        width=640,
        height=640,
        name=source_name,
        metadata={"original_name": source_name, "offline_checkpoint": True},
        collection_key="product",
    )
    job, _ = ledger.create_job(
        "single",
        [source["id"]],
        engine_key="offline-checkpoint",
        parameters={
            "model": "offline-checkpoint",
            "brief": {"objective": "验证结果与知识建议双向追溯"},
        },
        idempotency_key=f"feedback-checkpoint-job-{index}",
        title=f"反馈追溯验收 {index}",
    )
    item = job["items"][0]
    if ledger.claim_job_item(item["id"]) is None:
        raise SeedSafetyError("Could not claim the synthetic fixture job")

    main_data = image_bytes((40 + index * 25, 135, 190))
    cutout_data = image_bytes((35, 155, 105, 210))
    main_name = f"checkpoint-main-{index}.png"
    cutout_name = f"checkpoint-cutout-{index}.png"
    main_path = output.path / main_name
    cutout_path = output.path / cutout_name
    main_sha256 = _write_bytes_exclusive(output, main_name, main_data)
    cutout_sha256 = _write_bytes_exclusive(output, cutout_name, cutout_data)
    result_ids = ledger.commit_generation_results(
        item["generation_id"],
        source["id"],
        [
            {
                "path": str(main_path),
                "name": main_path.name,
                "role": "result_main",
                "mime": "image/png",
                "width": 640,
                "height": 640,
                "sha256": main_sha256,
                "metadata": {"offline_checkpoint": True},
            },
            {
                "path": str(cutout_path),
                "name": cutout_path.name,
                "role": "result_cutout",
                "mime": "image/png",
                "width": 640,
                "height": 640,
                "sha256": cutout_sha256,
                "metadata": {"offline_checkpoint": True},
            },
        ],
        job_item_id=item["id"],
    )
    completed = ledger.get_job(job["id"], include_attempts=False)
    if completed["status"] != "completed" or len(result_ids) != 2:
        raise SeedSafetyError("Synthetic fixture job did not complete with both results")
    return {
        "job_id": job["id"],
        "session_id": job["session_id"],
        "generation_id": item["generation_id"],
        "source_asset_id": source["id"],
        "main_result_asset_id": result_ids[0],
        "cutout_result_asset_id": result_ids[1],
        "files": [
            {"path": main_path.name, "size": len(main_data), "sha256": main_sha256},
            {"path": cutout_path.name, "size": len(cutout_data), "sha256": cutout_sha256},
        ],
    }


def _write_guarded_exclusive(
    guard: _PinnedDataDirectory, name: str, data: bytes
) -> None:
    handle = guard.create_exclusive(name)
    try:
        handle.verify_regular_single_link()
        handle.write(data)
        handle.flush()
        handle.verify_regular_single_link()
    finally:
        handle.close()


def _publish_database(guard: _PinnedDataDirectory, database: bytes) -> None:
    _write_guarded_exclusive(guard, LEDGER_NAME, database)


def _publish_manifest(
    guard: _PinnedDataDirectory, payload: dict[str, Any]
) -> None:
    serialized = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_guarded_exclusive(guard, FIXTURE_MANIFEST_NAME, serialized)


def _assert_root_entries(data_dir: Path, expected: set[str]) -> None:
    actual = {item.name for item in data_dir.iterdir()}
    if actual != expected:
        unexpected = ", ".join(sorted(actual - expected)) or "none"
        missing = ", ".join(sorted(expected - actual)) or "none"
        raise SeedSafetyError(
            f"Fixture root contents changed (unexpected: {unexpected}; missing: {missing})"
        )


def seed_feedback_checkpoint(value: str | os.PathLike[str]) -> dict[str, Any]:
    data_dir = validate_fresh_isolated_data_dir(value)
    with _PinnedDataDirectory(data_dir) as guard:
        claim = guard.create_exclusive(SEED_CLAIM_NAME)
        completed = False
        try:
            claim.verify_regular_single_link()
            claim.flush()
            guard.verify()
            _assert_root_entries(data_dir, {SEED_CLAIM_NAME})

            with (
                _GuardedChildDirectory(guard, "output") as output,
                _GuardedChildDirectory(guard, "assets") as assets,
            ):
                    guard.verify()
                    output.verify()
                    assets.verify()
                    _assert_root_entries(
                        data_dir, {SEED_CLAIM_NAME, "assets", "output"}
                    )

                    ledger = _InMemoryAtelierLedger()
                    try:
                        memory = MemoryEngine(ledger)

                        seeded: list[dict[str, Any]] = []
                        for index in (1, 2):
                            item = seed_job(ledger, assets, output, index)
                            review = ledger.submit_result_review(
                                f"feedback-checkpoint-review-{index}",
                                job_id=item["job_id"],
                                generation_id=item["generation_id"],
                                result_asset_id=item["main_result_asset_id"],
                                decision="adjust",
                                reason_codes=["packaging_text"],
                                note="包装文字变形",
                                learning_action="record" if index == 1 else "suggest",
                            )
                            ledger.add_feedback(
                                item["session_id"],
                                "adjusted",
                                generation_id=item["generation_id"],
                                asset_id=item["main_result_asset_id"],
                                reason="包装文字变形",
                                structured={
                                    "review_id": review["id"],
                                    "job_id": item["job_id"],
                                    "mode": "single",
                                    "result_asset_id": item["main_result_asset_id"],
                                    "reason_codes": ["packaging_text"],
                                },
                                scope="result",
                                feedback_id=idempotent_id(
                                    "fb", f"result-review:{review['id']}"
                                ),
                            )
                            item["review_id"] = review["id"]
                            seeded.append(item)

                        synthesis = memory.synthesize()
                        if not synthesis["suggestions"]:
                            raise SeedSafetyError(
                                "Synthetic feedback did not produce a knowledge suggestion"
                            )
                        suggestion = synthesis["suggestions"][0]
                        active = seeded[-1]
                        draft = ledger.save_workflow_draft(
                            "single",
                            expected_revision=1,
                            selected_asset_ids=[active["source_asset_id"]],
                            brief={"objective": "验证结果与知识建议双向追溯"},
                            parameters={"model": "offline-checkpoint"},
                            active_job_id=active["job_id"],
                            current_generation_id=active["generation_id"],
                            current_result_asset_id=active["main_result_asset_id"],
                            ui_state={"checkpoint": "feedback-bidirectional"},
                        )
                        stats = ledger.stats()
                        manifest = {
                            "format_version": 1,
                            "fixture": "formal-webview-result-review",
                            "ledger_schema_version": stats["schema_version"],
                            "jobs": seeded,
                            "suggestion_id": suggestion["id"],
                            "draft_revision": draft["revision"],
                        }
                        database = ledger.serialize_database()
                    finally:
                        ledger.close()

                    expected = {SEED_CLAIM_NAME, "assets", "output"}
                    guard.verify()
                    output.verify()
                    assets.verify()
                    _assert_root_entries(data_dir, expected)
                    _publish_database(guard, database)
                    expected.add(LEDGER_NAME)
                    guard.verify()
                    output.verify()
                    assets.verify()
                    _assert_root_entries(data_dir, expected)
                    _publish_manifest(guard, manifest)
                    expected.add(FIXTURE_MANIFEST_NAME)
                    guard.verify()
                    output.verify()
                    assets.verify()
                    _assert_root_entries(data_dir, expected)
                    claim.verify_regular_single_link()
            completed = True
        finally:
            remove_claim = False
            try:
                if completed:
                    guard.allow_child_mutations_after_nonempty()
                    remove_claim = True
            finally:
                claim.close()
                if remove_claim:
                    guard.unlink_child(SEED_CLAIM_NAME)
        return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args(argv)
    try:
        result = seed_feedback_checkpoint(args.data_dir)
    except (OSError, SeedSafetyError, ValueError, KeyError) as error:
        print(f"fixture seed error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
