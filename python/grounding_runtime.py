from __future__ import annotations

import atexit
import base64
import hashlib
import io
import json
import os
import platform
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import requests
from PIL import Image

try:
    from model_artifacts import (
        LOCAL_RECEIPT_NAME,
        load_artifact_manifest,
        verify_artifact,
    )
    from semantic_grounding import GroundingAdapterUnavailable
except ImportError:  # pragma: no cover - package imports used by tests and tools
    from python.model_artifacts import (
        LOCAL_RECEIPT_NAME,
        load_artifact_manifest,
        verify_artifact,
    )
    from python.semantic_grounding import GroundingAdapterUnavailable


GROUNDING_RUNTIME_CONTRACT_VERSION = "2026-08-30.1"
GROUNDING_RUNTIME_MANIFEST_NAME = "grounding-runtime-manifest.json"
GROUNDING_RUNTIME_TOKEN_ENV = "PRODUCT_ATELIER_GROUNDING_WORKER_TOKEN"
DEFAULT_MODEL_ARTIFACT_ID = "grounding-dino-tiny-a2bb814"


class GroundingPackError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def bundled_model_manifest_path() -> Path:
    filename = "grounding-dino-tiny.json"
    candidates = [
        Path(__file__).resolve().parents[1] / "docs" / "model-artifacts" / filename,
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        / "model-artifacts"
        / filename,
        Path(sys.executable).resolve().parent / "_internal" / "model-artifacts" / filename,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(root: Path, value: Any, *, field: str) -> Path:
    relative = Path(str(value or ""))
    if relative.is_absolute() or ".." in relative.parts or not relative.name:
        raise GroundingPackError("UNSAFE_PACK_PATH", f"{field} is unsafe")
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise GroundingPackError("PACK_PATH_ESCAPE", f"{field} leaves the pack root") from exc
    return target


def default_runtime_root() -> Path:
    configured = str(os.environ.get("PRODUCT_ATELIER_GROUNDING_RUNTIMES_DIR", "") or "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / "ProductAtelier-Runtimes"
    return (root / "grounding-dino-transformers").resolve()


def default_model_root(artifact_id: str = DEFAULT_MODEL_ARTIFACT_ID) -> Path:
    configured = str(os.environ.get("PRODUCT_ATELIER_MODELS_DIR", "") or "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / "ProductAtelier-Models"
    return (root / artifact_id).resolve()


def load_runtime_manifest(runtime_root: str | Path) -> dict[str, Any]:
    root = Path(runtime_root).expanduser().resolve()
    manifest_path = root / GROUNDING_RUNTIME_MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise GroundingPackError("RUNTIME_MANIFEST_MISSING", "本地识别运行时缺少清单") from exc
    except (OSError, ValueError, TypeError) as exc:
        raise GroundingPackError("RUNTIME_MANIFEST_INVALID", "本地识别运行时清单无法读取") from exc
    if manifest.get("schema_version") != "1.0":
        raise GroundingPackError("RUNTIME_SCHEMA_UNSUPPORTED", "本地识别运行时清单版本不受支持")
    runtime_id = str(manifest.get("runtime_id") or "").strip()
    if not runtime_id:
        raise GroundingPackError("RUNTIME_ID_MISSING", "本地识别运行时缺少身份")
    if manifest.get("contract_version") != GROUNDING_RUNTIME_CONTRACT_VERSION:
        raise GroundingPackError("RUNTIME_CONTRACT_MISMATCH", "本地识别运行时与当前软件版本不匹配")
    entrypoint = manifest.get("entrypoint")
    if not isinstance(entrypoint, dict):
        raise GroundingPackError("RUNTIME_ENTRYPOINT_MISSING", "本地识别运行时缺少启动入口")
    executable = _safe_relative_path(root, entrypoint.get("path"), field="entrypoint.path")
    expected_size = int(entrypoint.get("bytes") or 0)
    expected_hash = str(entrypoint.get("sha256") or "")
    if expected_size < 1 or len(expected_hash) != 64 or any(
        char not in "0123456789abcdef" for char in expected_hash
    ):
        raise GroundingPackError("RUNTIME_ENTRYPOINT_LOCK_INVALID", "本地识别运行时入口锁无效")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise GroundingPackError("RUNTIME_FILES_MISSING", "本地识别运行时缺少文件清单")
    seen: set[str] = set()
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise GroundingPackError("RUNTIME_FILE_LOCK_INVALID", f"runtime file {index + 1} is invalid")
        relative = Path(str(item.get("path") or ""))
        key = relative.as_posix()
        if relative.is_absolute() or ".." in relative.parts or not relative.name or key in seen:
            raise GroundingPackError("RUNTIME_FILE_LOCK_INVALID", f"runtime file {index + 1} is unsafe")
        seen.add(key)
        size = int(item.get("bytes") or 0)
        digest = str(item.get("sha256") or "")
        if size < 0 or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise GroundingPackError("RUNTIME_FILE_LOCK_INVALID", f"runtime file {key} has an invalid lock")
    entrypoint_key = Path(str(entrypoint["path"])).as_posix()
    if entrypoint_key not in seen:
        raise GroundingPackError("RUNTIME_ENTRYPOINT_UNLOCKED", "本地识别运行时入口未包含在文件清单中")
    entrypoint_file = next(item for item in files if Path(str(item["path"])).as_posix() == entrypoint_key)
    if (
        int(entrypoint_file["bytes"]) != expected_size
        or str(entrypoint_file["sha256"]) != expected_hash
    ):
        raise GroundingPackError("RUNTIME_ENTRYPOINT_LOCK_MISMATCH", "本地识别运行时入口锁不一致")
    model_ids = manifest.get("supported_model_artifact_ids")
    if not isinstance(model_ids, list) or not all(str(item).strip() for item in model_ids):
        raise GroundingPackError("RUNTIME_MODEL_CONTRACT_MISSING", "本地识别运行时未声明支持的模型包")
    target = manifest.get("platform")
    if not isinstance(target, dict):
        raise GroundingPackError("RUNTIME_PLATFORM_MISSING", "本地识别运行时缺少平台信息")
    manifest["runtime_root"] = str(root)
    manifest["manifest_path"] = str(manifest_path)
    manifest["executable_path"] = str(executable)
    return manifest


def verify_runtime_pack(runtime_root: str | Path, *, full: bool = False) -> dict[str, Any]:
    root = Path(runtime_root).expanduser().resolve()
    try:
        manifest = load_runtime_manifest(root)
    except GroundingPackError as exc:
        return {
            "status": "missing" if exc.code == "RUNTIME_MANIFEST_MISSING" else "invalid",
            "code": exc.code,
            "message": exc.message,
            "root": str(root),
        }
    target = manifest["platform"]
    expected_system = str(target.get("system") or "").lower()
    expected_machine = str(target.get("machine") or "").lower()
    current_system = platform.system().lower()
    current_machine = platform.machine().lower()
    if expected_system and expected_system != current_system:
        return {
            "status": "incompatible",
            "code": "RUNTIME_SYSTEM_MISMATCH",
            "message": "本地识别运行时不适用于当前操作系统",
            "root": str(root),
            "runtime_id": manifest["runtime_id"],
        }
    machine_aliases = {
        "amd64": {"amd64", "x86_64"},
        "x86_64": {"amd64", "x86_64"},
        "arm64": {"arm64", "aarch64"},
        "aarch64": {"arm64", "aarch64"},
    }
    if expected_machine and current_machine not in machine_aliases.get(expected_machine, {expected_machine}):
        return {
            "status": "incompatible",
            "code": "RUNTIME_MACHINE_MISMATCH",
            "message": "本地识别运行时不适用于当前处理器架构",
            "root": str(root),
            "runtime_id": manifest["runtime_id"],
        }
    executable = Path(manifest["executable_path"])
    invalid_files = []
    try:
        for expected in manifest["files"]:
            relative = str(expected["path"])
            target_file = _safe_relative_path(root, relative, field=f"runtime.{relative}")
            if not target_file.is_file() or target_file.stat().st_size != int(expected["bytes"]):
                invalid_files.append({"path": relative, "reason": "missing_or_wrong_size"})
                continue
            if full and _sha256(target_file) != str(expected["sha256"]):
                invalid_files.append({"path": relative, "reason": "wrong_hash"})
    except (GroundingPackError, OSError) as exc:
        invalid_files.append({
            "path": "runtime",
            "reason": exc.code if isinstance(exc, GroundingPackError) else "unreadable",
        })
    if invalid_files:
        return {
            "status": "invalid",
            "code": "RUNTIME_FILES_INVALID",
            "message": "本地识别运行时文件不完整或已改变",
            "root": str(root),
            "runtime_id": manifest["runtime_id"],
            "invalid_files": invalid_files,
        }
    return {
        "status": "verified" if full else "ready",
        "code": "",
        "message": "本地识别运行时已完整验证" if full else "本地识别运行时已就绪",
        "root": str(root),
        "runtime_id": manifest["runtime_id"],
        "contract_version": manifest["contract_version"],
        "executable": str(executable),
        "supported_model_artifact_ids": list(manifest["supported_model_artifact_ids"]),
        "manifest": manifest,
    }


def verify_model_pack(
    model_root: str | Path,
    model_manifest_path: str | Path,
    *,
    full: bool = False,
) -> dict[str, Any]:
    root = Path(model_root).expanduser().resolve()
    try:
        manifest = load_artifact_manifest(model_manifest_path)
    except (OSError, ValueError, TypeError) as exc:
        return {
            "status": "invalid",
            "code": "MODEL_CONTRACT_INVALID",
            "message": "软件内置的模型包合同无效",
            "root": str(root),
        }
    if not root.is_dir():
        return {
            "status": "missing",
            "code": "MODEL_ROOT_MISSING",
            "message": "尚未找到本地识别模型包",
            "root": str(root),
            "artifact_id": manifest["artifact_id"],
        }
    if full:
        verification = verify_artifact(root, manifest)
        ok = verification["status"] == "verified"
        return {
            "status": "verified" if ok else "invalid",
            "code": "" if ok else "MODEL_HASH_MISMATCH",
            "message": "本地识别模型包已完整验证" if ok else "本地识别模型包文件不完整或已改变",
            "root": str(root),
            "artifact_id": manifest["artifact_id"],
            "source_revision": manifest["source"]["revision"],
            "verification": verification,
        }
    receipt_path = root / LOCAL_RECEIPT_NAME
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        receipt = {}
    except (OSError, ValueError, TypeError):
        receipt = {}
    receipt_files = {
        str(item.get("path") or ""): item
        for item in receipt.get("files") or []
        if isinstance(item, dict)
    }
    quick_ok = (
        receipt.get("status") == "verified"
        and receipt.get("artifact_id") == manifest["artifact_id"]
        and receipt.get("source_revision") == manifest["source"]["revision"]
    )
    for expected in manifest["files"]:
        relative = str(expected["path"])
        target = _safe_relative_path(root, relative, field=f"model.{relative}")
        locked = receipt_files.get(relative) or {}
        if (
            not target.is_file()
            or target.stat().st_size != int(expected["bytes"])
            or locked.get("sha256") != expected["sha256"]
            or int(locked.get("bytes") or 0) != int(expected["bytes"])
        ):
            quick_ok = False
            break
    return {
        "status": "ready" if quick_ok else "invalid",
        "code": "" if quick_ok else "MODEL_RECEIPT_INVALID",
        "message": "本地识别模型包已就绪" if quick_ok else "本地识别模型包未完成可信校验",
        "root": str(root),
        "artifact_id": manifest["artifact_id"],
        "source_revision": manifest["source"]["revision"],
    }


def grounding_pack_status(
    runtime_root: str | Path | None,
    model_root: str | Path | None,
    model_manifest_path: str | Path,
    *,
    full: bool = False,
) -> dict[str, Any]:
    runtime_value = str(runtime_root or "").strip()
    model_value = str(model_root or "").strip()
    runtime = verify_runtime_pack(runtime_value, full=full) if runtime_value else {
        "status": "not_configured",
        "code": "RUNTIME_NOT_CONFIGURED",
        "message": "尚未选择本地识别运行时",
        "root": "",
    }
    model = verify_model_pack(model_value, model_manifest_path, full=full) if model_value else {
        "status": "not_configured",
        "code": "MODEL_NOT_CONFIGURED",
        "message": "尚未选择本地识别模型包",
        "root": "",
    }
    expected_model = model.get("artifact_id")
    supported = runtime.get("supported_model_artifact_ids") or []
    compatible = bool(expected_model and expected_model in supported)
    ready_statuses = {"ready", "verified"}
    available = (
        runtime.get("status") in ready_statuses
        and model.get("status") in ready_statuses
        and compatible
    )
    if available:
        message = "本地智能选物扩展已验证，可以使用"
        code = ""
    elif runtime.get("status") not in ready_statuses:
        message = str(runtime.get("message") or "本地识别运行时不可用")
        code = str(runtime.get("code") or "RUNTIME_UNAVAILABLE")
    elif model.get("status") not in ready_statuses:
        message = str(model.get("message") or "本地识别模型包不可用")
        code = str(model.get("code") or "MODEL_UNAVAILABLE")
    else:
        message = "当前运行时不支持所选模型包"
        code = "RUNTIME_MODEL_MISMATCH"
    return {
        "available": available,
        "verified": available and full,
        "code": code,
        "message": message,
        "runtime": runtime,
        "model": model,
    }


def probe_grounding_pack(
    runtime_root: str | Path,
    model_root: str | Path,
    model_manifest_path: str | Path,
    *,
    timeout: float = 45.0,
) -> dict[str, Any]:
    status = grounding_pack_status(
        runtime_root,
        model_root,
        model_manifest_path,
        full=True,
    )
    if not status["available"]:
        return {**status, "probe": {"status": "not_run"}}
    manifest = status["runtime"].pop("manifest")
    creation_flags = 0x08000000 if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            [
                str(manifest["executable_path"]),
                "--probe",
                "--model-path",
                str(Path(model_root).expanduser().resolve()),
            ],
            cwd=str(Path(runtime_root).expanduser().resolve()),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout)),
            creationflags=creation_flags,
            check=False,
        )
        probe = json.loads((completed.stdout or "").strip()) if completed.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        probe = {}
    probe_ready = probe.get("status") == "ready"
    return {
        **status,
        "available": bool(status["available"] and probe_ready),
        "verified": bool(status["verified"] and probe_ready),
        "code": "" if probe_ready else "RUNTIME_PROBE_FAILED",
        "message": (
            "本地智能选物扩展已完整验证，运行环境可用"
            if probe_ready
            else "文件校验通过，但本地识别运行环境探测失败"
        ),
        "probe": probe or {"status": "failed"},
    }


class ExternalGroundingWorkerAdapter:
    adapter_id = "external-grounding-runtime"

    def __init__(
        self,
        runtime_root: str | Path,
        model_root: str | Path,
        model_manifest_path: str | Path,
        *,
        startup_timeout: float = 120.0,
        request_timeout: float = 180.0,
        command_override: Sequence[str] | None = None,
    ) -> None:
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.model_root = Path(model_root).expanduser().resolve()
        self.model_manifest_path = Path(model_manifest_path).resolve()
        self.startup_timeout = max(1.0, float(startup_timeout))
        self.request_timeout = max(1.0, float(request_timeout))
        self.command_override = [str(item) for item in command_override] if command_override else None
        self._start_lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._port = 0
        self._token = ""
        self._runtime_id = "source-runtime"
        self._session = requests.Session()
        self._validated = False
        atexit.register(self.close)

    def _validate(self) -> None:
        if self._validated:
            return
        model = verify_model_pack(self.model_root, self.model_manifest_path, full=True)
        if model.get("status") != "verified":
            raise GroundingAdapterUnavailable(str(model.get("code") or "model_pack_invalid").lower())
        if self.command_override:
            self._runtime_id = "source-runtime"
        else:
            runtime = verify_runtime_pack(self.runtime_root, full=True)
            if runtime.get("status") != "verified":
                raise GroundingAdapterUnavailable(str(runtime.get("code") or "runtime_pack_invalid").lower())
            if model.get("artifact_id") not in (runtime.get("supported_model_artifact_ids") or []):
                raise GroundingAdapterUnavailable("runtime_model_mismatch")
            self._runtime_id = str(runtime["runtime_id"])
        self._validated = True

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def _worker_command(self) -> list[str]:
        if self.command_override:
            command = list(self.command_override)
        else:
            manifest = load_runtime_manifest(self.runtime_root)
            command = [str(manifest["executable_path"])]
        return [
            *command,
            "--serve",
            "--port",
            str(self._port),
            "--model-path",
            str(self.model_root),
            "--runtime-id",
            self._runtime_id,
            "--runtime-contract",
            GROUNDING_RUNTIME_CONTRACT_VERSION,
            "--parent-pid",
            str(os.getpid()),
        ]

    def _health(self) -> bool:
        if not self._process or self._process.poll() is not None or not self._port:
            return False
        try:
            response = self._session.get(
                f"http://127.0.0.1:{self._port}/health",
                headers={"X-Product-Atelier-Worker-Token": self._token},
                timeout=1.0,
            )
            payload = response.json() if response.ok else {}
            return (
                payload.get("status") == "ok"
                and payload.get("runtime_id") == self._runtime_id
                and payload.get("contract_version") == GROUNDING_RUNTIME_CONTRACT_VERSION
            )
        except (requests.RequestException, ValueError, TypeError):
            return False

    def _ensure_worker(self) -> None:
        if self._health():
            return
        with self._start_lock:
            if self._health():
                return
            self.close()
            self._validate()
            self._port = self._free_port()
            self._token = secrets.token_urlsafe(32)
            environment = os.environ.copy()
            environment[GROUNDING_RUNTIME_TOKEN_ENV] = self._token
            creation_flags = 0x08000000 if os.name == "nt" else 0
            try:
                self._process = subprocess.Popen(
                    self._worker_command(),
                    cwd=str(self.runtime_root if not self.command_override else Path.cwd()),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creation_flags,
                )
            except OSError as exc:
                self._process = None
                raise GroundingAdapterUnavailable("runtime_start_failed") from exc
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                if self._health():
                    return
                if self._process.poll() is not None:
                    break
                time.sleep(0.1)
            self.close()
            raise GroundingAdapterUnavailable("runtime_health_failed")

    def detect(
        self,
        image: Image.Image,
        query: str,
        *,
        box_threshold: float,
        text_threshold: float,
    ) -> Sequence[Mapping[str, Any]]:
        with self._infer_lock:
            self._ensure_worker()
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=95, subsampling=0)
            payload = {
                "image_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
                "query": str(query or ""),
                "box_threshold": float(box_threshold),
                "text_threshold": float(text_threshold),
            }
            try:
                response = self._session.post(
                    f"http://127.0.0.1:{self._port}/detect",
                    headers={"X-Product-Atelier-Worker-Token": self._token},
                    json=payload,
                    timeout=self.request_timeout,
                )
                data = response.json()
            except (requests.RequestException, ValueError, TypeError) as exc:
                self.close()
                raise GroundingAdapterUnavailable("runtime_request_failed") from exc
            if not response.ok:
                raise GroundingAdapterUnavailable(str(data.get("reason") or "runtime_inference_failed"))
            candidates = data.get("candidates")
            if not isinstance(candidates, list):
                raise GroundingAdapterUnavailable("runtime_response_invalid")
            return [item for item in candidates if isinstance(item, Mapping)]

    def close(self) -> None:
        process = self._process
        self._process = None
        self._port = 0
        self._token = ""
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
