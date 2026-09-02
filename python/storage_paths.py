from __future__ import annotations

import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable


MODE_DELIVERY_DIRECTORIES = {
    "single": "01_单产品",
    "multi-file": "02_多文件",
    "group-split": "03_合照拆分",
    "cutout-batch": "04_批量抠图",
}


class OutputRootError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _native_io_path(path: str | Path) -> str:
    value = os.path.abspath(os.fspath(path))
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def canonicalize_output_root(value: str | Path) -> Path:
    candidate = Path(value).expanduser()
    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return candidate


def _is_within(candidate: Path, root: Path) -> bool:
    return candidate == root or candidate.is_relative_to(root)


def validate_output_root(
    value: str | Path,
    *,
    default_root: str | Path,
    protected_roots: Iterable[str | Path] = (),
    require_available: bool = True,
    test_write: bool = False,
) -> Path:
    raw = str(value).strip()
    if not raw:
        raise OutputRootError("OUTPUT_ROOT_REQUIRED", "请选择成品交付目录")
    unexpanded = Path(raw).expanduser()
    if not unexpanded.is_absolute():
        raise OutputRootError("OUTPUT_ROOT_NOT_ABSOLUTE", "交付目录必须使用完整绝对路径")

    candidate = canonicalize_output_root(unexpanded)
    default = canonicalize_output_root(default_root)
    if candidate.parent == candidate:
        raise OutputRootError("OUTPUT_ROOT_TOO_BROAD", "不能把整个磁盘根目录设为交付目录")

    if candidate != default:
        for value_root in protected_roots:
            if not str(value_root).strip():
                continue
            protected = canonicalize_output_root(value_root)
            if _is_within(candidate, protected) or _is_within(protected, candidate):
                raise OutputRootError(
                    "OUTPUT_ROOT_PROTECTED",
                    "该位置属于知识库、应用数据或程序目录，不能用于保存成品",
                )

    if require_available:
        if not candidate.exists():
            raise OutputRootError(
                "OUTPUT_ROOT_UNAVAILABLE",
                "交付目录不存在，或所在磁盘当前不可用",
            )
        if not candidate.is_dir():
            raise OutputRootError("OUTPUT_ROOT_NOT_DIRECTORY", "所选位置不是文件夹")

    if test_write:
        probe = candidate / f".product-atelier-write-test-{uuid.uuid4().hex}.tmp"
        try:
            with open(probe, "xb") as handle:
                handle.write(b"Product Atelier output root probe\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise OutputRootError(
                "OUTPUT_ROOT_NOT_WRITABLE",
                "没有写入该交付目录的权限，请选择其他位置",
            ) from exc
        finally:
            probe.unlink(missing_ok=True)
    return candidate


def output_root_status(
    value: str | Path,
    *,
    default_root: str | Path,
    protected_roots: Iterable[str | Path] = (),
) -> dict[str, object]:
    try:
        path = validate_output_root(
            value,
            default_root=default_root,
            protected_roots=protected_roots,
            require_available=True,
            test_write=False,
        )
        return {
            "available": True,
            "code": "OUTPUT_ROOT_READY",
            "message": "新任务将保存到这里；运行中任务保持原目录",
            "path": str(path),
        }
    except OutputRootError as exc:
        return {
            "available": False,
            "code": exc.code,
            "message": exc.message,
            "path": str(canonicalize_output_root(value)),
        }


def _safe_part(value: object, fallback: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "-", str(value or "")).strip("-._")
    return (cleaned or fallback)[:48]


def job_delivery_directory(
    root: str | Path,
    *,
    created_at: str,
    mode: str,
    job_id: str,
    item_id: str,
    item_position: int,
    attempt: int,
) -> Path:
    date_match = re.match(r"^(\d{4}-\d{2}-\d{2})", str(created_at or ""))
    date_part = date_match.group(1) if date_match else datetime.now().strftime("%Y-%m-%d")
    mode_part = MODE_DELIVERY_DIRECTORIES.get(mode, "99_其他任务")
    job_part = _safe_part(job_id, "job")
    item_part = _safe_part(item_id, "item")
    return (
        canonicalize_output_root(root)
        / date_part
        / mode_part
        / f"任务-{job_part}"
        / f"{max(0, int(item_position)) + 1:03d}-{item_part}"
        / f"attempt-{max(1, int(attempt))}"
    )


def publish_staged_file(source: str | Path, target: str | Path) -> Path:
    """Copy across volumes and expose the final name with one atomic replace."""
    source_path = Path(source)
    target_path = Path(target)
    os.makedirs(_native_io_path(target_path.parent), exist_ok=True)
    temporary = target_path.parent / f".{target_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with open(_native_io_path(source_path), "rb") as source_handle, open(
            _native_io_path(temporary), "xb"
        ) as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        os.replace(_native_io_path(temporary), _native_io_path(target_path))
        os.unlink(_native_io_path(source_path))
        return target_path
    finally:
        try:
            os.unlink(_native_io_path(temporary))
        except FileNotFoundError:
            pass
