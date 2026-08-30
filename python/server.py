# -*- coding: utf-8 -*-
"""
Product Atelier Desktop - Python Backend Server
================================================
FastAPI backend wrapping the existing AI image processing logic.
Runs as a sidecar process alongside the Tauri desktop app.
All business logic preserved 100% from ecom_workbench.py.
"""
import base64, json, time, io, os, sys, re, mimetypes, threading, traceback, uuid, shutil, hashlib, math, sqlite3
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from datetime import datetime
from typing import Any, Optional
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
import uvicorn

try:
    from asset_store import AssetAccessError, AssetStore, AssetStoreError, AssetValidationError
    from atelier_ledger import (
        AssetPurgeBlockedError,
        AtelierLedger,
        DraftRevisionConflictError,
        IdempotencyConflictError,
        InvalidStatusTransitionError,
        idempotent_id,
    )
    from job_engine import JobEngine, JobExecutionError, JobProcessorResult
    from generation_baseline import (
        GENERATION_TRACE_CONTRACT_VERSION,
        PROMPT_COMPILER_VERSION,
        capability_contract,
        prompt_snapshot,
        unavailable_billing_evidence,
    )
    from knowledge_engine import KnowledgeCompiler, canonicalize_vault_path
    from memory_engine import MemoryEngine
    from grounding_runtime import (
        bundled_model_manifest_path,
        grounding_pack_status,
        probe_grounding_pack,
        verify_model_pack,
        verify_runtime_pack,
    )
    from semantic_cutout import (
        SemanticCutoutError,
        apply_confirmed_regions,
        apply_mask_edits,
        build_confirmed_selection,
        normalize_cutout_selection,
        normalize_mask_edits,
        normalize_regions,
        validate_selection_sources,
    )
    from semantic_grounding import (
        UnavailableGroundingAdapter,
        ground_semantic_candidates,
        grounding_adapter_from_pack,
    )
    from semantic_query import resolve_semantic_query
    from storage_paths import (
        OutputRootError,
        canonicalize_output_root,
        job_delivery_directory,
        output_root_status,
        publish_staged_file,
        validate_output_root,
    )
except ImportError:  # Allows importing as python.server during local tests.
    from python.asset_store import AssetAccessError, AssetStore, AssetStoreError, AssetValidationError
    from python.atelier_ledger import (
        AssetPurgeBlockedError,
        AtelierLedger,
        DraftRevisionConflictError,
        IdempotencyConflictError,
        InvalidStatusTransitionError,
        idempotent_id,
    )
    from python.job_engine import JobEngine, JobExecutionError, JobProcessorResult
    from python.generation_baseline import (
        GENERATION_TRACE_CONTRACT_VERSION,
        PROMPT_COMPILER_VERSION,
        capability_contract,
        prompt_snapshot,
        unavailable_billing_evidence,
    )
    from python.knowledge_engine import KnowledgeCompiler, canonicalize_vault_path
    from python.memory_engine import MemoryEngine
    from python.grounding_runtime import (
        bundled_model_manifest_path,
        grounding_pack_status,
        probe_grounding_pack,
        verify_model_pack,
        verify_runtime_pack,
    )
    from python.semantic_cutout import (
        SemanticCutoutError,
        apply_confirmed_regions,
        apply_mask_edits,
        build_confirmed_selection,
        normalize_cutout_selection,
        normalize_mask_edits,
        normalize_regions,
        validate_selection_sources,
    )
    from python.semantic_grounding import (
        UnavailableGroundingAdapter,
        ground_semantic_candidates,
        grounding_adapter_from_pack,
    )
    from python.semantic_query import resolve_semantic_query
    from python.storage_paths import (
        OutputRootError,
        canonicalize_output_root,
        job_delivery_directory,
        output_root_status,
        publish_staged_file,
        validate_output_root,
    )

# ======================== GUI MODE STDOUT GUARD ========================
# When running as windowed (no console) exe, sys.stdout/sys.stderr may be None.
# Redirect to devnull to prevent print() / uvicorn logging crashes.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
# Also guard uvicorn's access logger which writes to stderr
import logging
logging.getLogger("uvicorn.access").handlers = [logging.NullHandler()]
logging.getLogger("uvicorn.error").handlers = [logging.NullHandler()]

# ======================== CONFIG ========================
def get_app_data_dir():
    """Get platform-appropriate app data directory for config/storage"""
    override = str(os.environ.get("PRODUCT_ATELIER_DATA_DIR", "")).strip()
    if override:
        base = Path(override).expanduser()
        d = base
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        d = base / "ProductAtelier"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
        d = base / "ProductAtelier"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        d = base / "ProductAtelier"
    d.mkdir(parents=True, exist_ok=True)
    return d

APP_DIR = get_app_data_dir()
CONFIG_PATH = APP_DIR / "config.json"
OUTPUT_DIR = APP_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
(OUTPUT_DIR / "multi-products").mkdir(exist_ok=True)
(OUTPUT_DIR / "_tmp").mkdir(exist_ok=True)
HISTORY_DIR = APP_DIR / "history"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)
LEDGER = AtelierLedger(APP_DIR / "atelier.sqlite3")
ASSET_DIR = APP_DIR / "assets"
ASSET_STORE = AssetStore(ASSET_DIR, LEDGER)
try:
    _startup_config = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
except Exception:
    _startup_config = {}
_knowledge_path = str(_startup_config.get("knowledge_base_path", "")).strip()
KNOWLEDGE = KnowledgeCompiler(_knowledge_path) if _knowledge_path else KnowledgeCompiler()
MEMORY = MemoryEngine(LEDGER)
GROUNDING_MODEL_MANIFEST_PATH = bundled_model_manifest_path()

# Legacy config path for migration. Keep the old location discoverable without
# embedding one developer's Windows account in the application.
LEGACY_CONFIG = Path(
    os.environ.get(
        "PRODUCT_ATELIER_LEGACY_CONFIG",
        str(Path.home() / ".codex" / "skills" / "lk-ai-image" / "config.json"),
    )
).expanduser()

BASE_URL = "https://api.lk888.ai/api"

MODEL_OPTIONS = {
    "GPT-Image-2 (最高质量)": "gpt-image-2",
    "Nano Banana Pro (专业商业)": "gemini-3-pro-image-preview",
    "Nano Banana 2 (快速批量)": "gemini-3.1-flash-image-preview",
    "千问-Image (中文优化)": "qwen-image",
}
MAX_GROUP_PRODUCTS = 12
GROUP_PRODUCT_TYPES = frozenset({"food", "packaging", "dish"})
FOLDER_SCAN_MAX_FILES = 500
FOLDER_DELIVERY_PREFIX = "ProductAtelier-已处理-"
FOLDER_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
_FOLDER_DELIVERY_LOCK = threading.RLock()
PRODUCT_ATELIER_VERSION = "1.0.0"
SIDECAR_CONTRACT_VERSION = "2026-08-30.5"
SIDECAR_MANIFEST_FILENAME = "sidecar-manifest.json"
try:
    TRASH_RETENTION_DAYS = max(
        0, int(os.environ.get("PRODUCT_ATELIER_TRASH_RETENTION_DAYS", "30"))
    )
except ValueError:
    TRASH_RETENTION_DAYS = 30


def sidecar_runtime_info() -> dict[str, Any]:
    """Return non-sensitive build identity for stale-sidecar detection."""
    packaged = bool(getattr(sys, "frozen", False))
    info: dict[str, Any] = {
        "product_version": PRODUCT_ATELIER_VERSION,
        "contract_version": SIDECAR_CONTRACT_VERSION,
        "packaged": packaged,
        "git_commit": "source",
        "source_fingerprint": "source",
        "built_at": None,
    }
    if not packaged:
        return info

    manifest_path = Path(sys.executable).resolve().parent / SIDECAR_MANIFEST_FILENAME
    try:
        # Windows PowerShell 5 writes UTF-8 JSON with a BOM by default.  Accept
        # both BOM and BOM-less manifests so the same package verifies on every
        # supported PowerShell version.
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        info["manifest_status"] = "missing_or_invalid"
        return info

    for key in ("git_commit", "source_fingerprint", "built_at"):
        value = manifest.get(key)
        if isinstance(value, str) and value:
            info[key] = value
    manifest_contract = manifest.get("contract_version")
    info["manifest_status"] = (
        "ok" if manifest_contract == SIDECAR_CONTRACT_VERSION else "contract_mismatch"
    )
    return info

NEG_BASE = "模糊,低质量,变形,暗角,暖黄偏色,杂物,水印,文字,logo,阴影过重,噪点,失真,jpeg压缩痕迹,过度曝光,欠曝"
NEG_REMOVE_PLATE = "盘子,碟子,托盘,木板,纸板,餐垫,桌布,玻璃器皿,碗,容器,器皿,摆盘,竹垫,石板,餐布,金属台面,木桌面,大理石桌面,纸杯,塑料盒"

ANGLE_PROMPT = {
    "auto": "AI智能选择最佳拍摄角度",
    "keep": "严格保持参考图中产品的原始拍摄角度和透视关系，不要改变拍摄角度",
    "45top": "略微俯视45度角(three-quarter top-down view)，经典电商主图角度",
    "front": "正面平视角度(eye-level straight front view)，包装类产品首选，标签文字清晰正面可见",
    "30side": "30度斜侧角度(dramatic 3/4 view)，突出产品立体感和层次",
    "90top": "90度正俯视角度(flat lay top-down view)，适合平铺展示",
}

OUTPUT_RATIO_VALUES = frozenset({
    "original", "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9",
})
OUTPUT_RESOLUTION_VALUES = frozenset({"2k", "4k"})
OUTPUT_RATIO_NUMBERS = {
    "1:1": 1.0,
    "2:3": 2 / 3,
    "3:2": 3 / 2,
    "3:4": 3 / 4,
    "4:3": 4 / 3,
    "4:5": 4 / 5,
    "5:4": 5 / 4,
    "9:16": 9 / 16,
    "16:9": 16 / 9,
}
GEMINI_IMAGE_RATIOS = {
    **OUTPUT_RATIO_NUMBERS,
    "21:9": 21 / 9,
    "1:4": 1 / 4,
    "4:1": 4.0,
    "1:8": 1 / 8,
    "8:1": 8.0,
}
GPT_IMAGE_SIZE_PRESETS = {
    "2k": {
        "1:1": "2048x2048", "2:3": "2048x3072", "3:2": "3072x2048",
        "3:4": "1920x2560", "4:3": "2560x1920", "4:5": "2048x2560",
        "5:4": "2560x2048", "9:16": "1440x2560", "16:9": "2560x1440",
    },
    "4k": {
        "1:1": "2880x2880", "2:3": "2304x3456", "3:2": "3456x2304",
        "3:4": "2400x3200", "4:3": "3200x2400", "4:5": "2560x3200",
        "5:4": "3200x2560", "9:16": "2160x3840", "16:9": "3840x2160",
    },
}


def _nearest_ratio_name(value: float, ratios: dict[str, float]) -> str:
    safe_value = max(float(value), 1e-9)
    return min(ratios, key=lambda name: abs(math.log(safe_value / ratios[name])))


def _ratio_name_from_dimensions(width: int, height: int) -> str:
    divisor = math.gcd(max(1, int(width)), max(1, int(height)))
    return f"{max(1, int(width)) // divisor}:{max(1, int(height)) // divisor}"


def _aligned_gpt_size(ratio: float, resolution: str) -> str:
    """Build a provider-valid custom size while preserving the source ratio.

    GPT Image 2 accepts custom dimensions aligned to 16 pixels, a 1:3..3:1
    ratio and at most 8,294,400 pixels.  Named ratios use the provider's
    published presets; this helper exists for the user's exact source ratio.
    """
    bounded_ratio = max(1 / 3, min(float(ratio), 3.0))
    target_pixels = 8_294_400 if resolution == "4k" else 4_194_304
    side_limit = 3840 if resolution == "4k" else 3072
    width = math.sqrt(target_pixels * bounded_ratio)
    height = math.sqrt(target_pixels / bounded_ratio)
    scale = min(1.0, side_limit / max(width, height))
    width = max(16, int(round(width * scale / 16)) * 16)
    height = max(16, int(round(height * scale / 16)) * 16)
    while width * height > 8_294_400:
        if width >= height:
            width -= 16
        else:
            height -= 16
    return f"{width}x{height}"


def resolve_output_spec(
    model: str,
    requested_ratio: str,
    requested_resolution: str,
    source_size: tuple[int, int],
    *,
    explicit: bool = True,
) -> dict[str, Any]:
    """Resolve semantic UI choices into a model-specific provider contract."""
    ratio_name = str(requested_ratio or "1:1").strip().lower()
    resolution = str(requested_resolution or "2k").strip().lower()
    if ratio_name not in OUTPUT_RATIO_VALUES:
        raise JobExecutionError("INVALID_OUTPUT_RATIO", "不支持的输出比例，请重新选择")
    if resolution not in OUTPUT_RESOLUTION_VALUES:
        raise JobExecutionError("INVALID_OUTPUT_RESOLUTION", "不支持的清晰度档位，请重新选择")
    width, height = (int(source_size[0]), int(source_size[1]))
    if width <= 0 or height <= 0:
        raise JobExecutionError("INVALID_SOURCE_IMAGE", "无法读取源图宽高，不能计算输出比例")

    source_ratio = width / height
    model_key = str(model or "").strip()
    is_gpt_image_2 = model_key.startswith("gpt-image-2") or model_key == "tt-image-2"
    is_gemini_image = model_key.startswith("gemini-") and "image" in model_key

    if ratio_name == "original":
        desired_ratio = source_ratio
        desired_label = _ratio_name_from_dimensions(width, height)
    else:
        desired_ratio = OUTPUT_RATIO_NUMBERS[ratio_name]
        desired_label = ratio_name

    if is_gemini_image:
        effective_ratio = _nearest_ratio_name(desired_ratio, GEMINI_IMAGE_RATIOS)
        provider_params = {
            "aspectRatio": effective_ratio,
            "imageSize": resolution.upper(),
        }
        provider_family = "gemini-image"
        provider_size = resolution.upper()
    elif is_gpt_image_2:
        if ratio_name == "original":
            provider_size = _aligned_gpt_size(desired_ratio, resolution)
            size_width, size_height = (int(part) for part in provider_size.split("x", 1))
            effective_ratio = _ratio_name_from_dimensions(size_width, size_height)
        else:
            effective_ratio = ratio_name
            provider_size = GPT_IMAGE_SIZE_PRESETS[resolution][ratio_name]
        provider_params = {"size": provider_size, "quality": "high"}
        provider_family = "gpt-image-2"
    else:
        # Compatibility fallback for non-production/fixture adapters. Production
        # UI exposes only the two capability-checked families above.
        effective_ratio = _nearest_ratio_name(desired_ratio, OUTPUT_RATIO_NUMBERS)
        provider_size = GPT_IMAGE_SIZE_PRESETS[resolution][effective_ratio]
        provider_params = {"size": provider_size}
        provider_family = "generic-image"

    effective_value = (
        GEMINI_IMAGE_RATIOS.get(effective_ratio)
        or OUTPUT_RATIO_NUMBERS.get(effective_ratio)
    )
    if effective_value is None:
        size_width, size_height = (int(part) for part in provider_size.split("x", 1))
        effective_value = size_width / size_height
    return {
        "requested_ratio": ratio_name,
        "requested_resolution": resolution,
        "source_width": width,
        "source_height": height,
        "source_ratio": _ratio_name_from_dimensions(width, height),
        "desired_ratio": desired_label,
        "effective_ratio": effective_ratio,
        "effective_ratio_value": float(effective_value),
        "provider_family": provider_family,
        "provider_params": provider_params,
        "provider_size": provider_size,
        "strict_aspect": bool(explicit),
    }


def _image_size_from_bytes(data):
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except Exception:
        return None, None


def _image_size_from_path(path):
    try:
        with Image.open(path) as image:
            return image.size
    except Exception:
        return None, None


def ledger_begin_task(mode, task_id, file_name, image_bytes, params, session_id=""):
    """Create a traceable session/generation without making generation depend on the ledger."""
    try:
        if session_id:
            session = LEDGER.get_session(session_id, include_timeline=False)
            LEDGER.update_session(session_id, mode=mode, status="processing")
        else:
            brief = {"source": "generation", "file_name": file_name or ""}
            if isinstance(params.get("brief"), dict):
                brief.update(params["brief"])
            session = LEDGER.create_session(
                mode,
                title=file_name or mode,
                category=str(params.get("category", "general")),
                brief=brief,
                intent_locks=params.get("intent_locks") or {},
            )
            session_id = session["id"]
            LEDGER.update_session(session_id, status="processing")
        width, height = _image_size_from_bytes(image_bytes)
        source = LEDGER.add_asset(
            session_id,
            "source",
            name=file_name or "source-image",
            mime=mimetypes.guess_type(file_name or "")[0] or "image/jpeg",
            width=width,
            height=height,
            data=image_bytes,
            metadata={"task_id": task_id},
        )
        generation = LEDGER.add_generation(
            session_id,
            task_id=task_id,
            model=str(params.get("model", "local")),
            parameters=params,
            knowledge_refs=params.get("knowledge_refs") or [],
            status="queued",
        )
        LEDGER.add_event(
            session_id,
            "task.queued",
            {"task_id": task_id, "mode": mode, "source_asset_id": source["id"]},
            generation_id=generation["id"],
        )
        return {
            "session_id": session_id,
            "generation_id": generation["id"],
            "source_asset_id": source["id"],
            "brief": session.get("brief", {}),
            "intent_locks": session.get("intent_locks", {}),
            "category": session.get("category", "general"),
            "brand_profile": session.get("brand_profile", ""),
        }
    except Exception as exc:
        print(f"[ledger] begin failed: {exc}", file=sys.stderr, flush=True)
        return {
            "session_id": session_id or "",
            "generation_id": "",
            "source_asset_id": "",
            "brief": {},
            "intent_locks": {},
            "category": "general",
            "brand_profile": "",
        }


def ledger_record_prompt(context, prompt, negative_prompt="", stage="primary", knowledge_refs=None):
    if not context or not context.get("generation_id"):
        return
    try:
        generation_id = context["generation_id"]
        session_id = context["session_id"]
        changes = {"status": "processing"}
        if stage == "primary":
            changes.update({
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "knowledge_refs": knowledge_refs or [],
            })
        LEDGER.update_generation(generation_id, **changes)
        LEDGER.add_event(
            session_id,
            "prompt.compiled",
            {"stage": stage, "prompt": prompt, "negative_prompt": negative_prompt, "knowledge_refs": knowledge_refs or []},
            generation_id=generation_id,
        )
    except Exception as exc:
        print(f"[ledger] prompt trace failed: {exc}", file=sys.stderr, flush=True)


def ledger_complete_task(context, results):
    if not context or not context.get("generation_id"):
        return
    try:
        session_id = context["session_id"]
        generation_id = context["generation_id"]
        source_asset_id = context.get("source_asset_id") or None
        asset_ids = []
        for role, items in (("result_main", results.get("main", [])), ("result_cutout", results.get("cutout", []))):
            for item in items or []:
                path = str(item.get("path", ""))
                width, height = _image_size_from_path(path) if path else (None, None)
                asset = LEDGER.add_asset(
                    session_id,
                    role,
                    parent_asset_id=source_asset_id,
                    path=path,
                    name=str(item.get("name", Path(path).name if path else role)),
                    mime=mimetypes.guess_type(path)[0] or ("image/png" if role.endswith("cutout") else "image/jpeg"),
                    width=width,
                    height=height,
                    metadata={"generation_id": generation_id},
                )
                asset_ids.append(asset["id"])
        LEDGER.update_generation(
            generation_id,
            status="completed",
            result_asset_ids=asset_ids,
            completed_at=datetime.now().astimezone().isoformat(timespec="milliseconds"),
        )
        LEDGER.update_session(
            session_id,
            status="completed",
            completed_at=datetime.now().astimezone().isoformat(timespec="milliseconds"),
        )
        LEDGER.add_event(
            session_id,
            "task.completed",
            {"result_asset_ids": asset_ids, "result_count": len(asset_ids)},
            generation_id=generation_id,
        )
    except Exception as exc:
        print(f"[ledger] completion trace failed: {exc}", file=sys.stderr, flush=True)


def ledger_fail_task(context, error):
    if not context or not context.get("generation_id"):
        return
    try:
        LEDGER.update_generation(
            context["generation_id"],
            status="error",
            error=str(error),
            completed_at=datetime.now().astimezone().isoformat(timespec="milliseconds"),
        )
        LEDGER.update_session(context["session_id"], status="error")
        LEDGER.add_event(
            context["session_id"],
            "task.failed",
            {"error": str(error)},
            generation_id=context["generation_id"],
        )
    except Exception as exc:
        print(f"[ledger] failure trace failed: {exc}", file=sys.stderr, flush=True)

# ======================== CONFIG PERSISTENCE ========================
_RUNTIME_CONFIG_LOCK = threading.RLock()
_RUNTIME_KNOWLEDGE_PATH = str(KNOWLEDGE.vault_path)
_RUNTIME_OUTPUT_ROOT = canonicalize_output_root(
    str(_startup_config.get("output_root") or OUTPUT_DIR)
)
_RUNTIME_GROUNDING_RUNTIME_ROOT = str(
    _startup_config.get("grounding_runtime_root") or ""
).strip()
_RUNTIME_GROUNDING_MODEL_ROOT = str(
    _startup_config.get("grounding_model_root") or ""
).strip()
_GROUNDING_ADAPTER_LOCK = threading.RLock()
_GROUNDING_ADAPTER_KEY: tuple[str, str] | None = None
_GROUNDING_ADAPTER: Any = None


@contextmanager
def _config_write_lock():
    """Serialize config read-modify-write across sidecar processes."""
    lock_path = CONFIG_PATH.with_name(f"{CONFIG_PATH.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _read_config_unlocked() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        parsed = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def load_config() -> dict:
    # Readers take the same short lock so Windows never has to replace a file
    # while another sidecar still holds it open.
    with _config_write_lock():
        return _read_config_unlocked()


def load_api_key():
    # Check app config first
    cfg = load_config()
    if cfg.get("api_key"):
        return cfg["api_key"]
    # Fallback to legacy config
    if LEGACY_CONFIG.exists():
        cfg = json.loads(LEGACY_CONFIG.read_text(encoding="utf-8"))
        if cfg.get("api_key"):
            return cfg["api_key"]
    return None

def save_config(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _config_write_lock():
        existing = _read_config_unlocked()
        existing.update(cfg)
        temp_path = CONFIG_PATH.with_name(
            f".{CONFIG_PATH.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with open(temp_path, "x", encoding="utf-8") as handle:
                json.dump(existing, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass
            os.replace(temp_path, CONFIG_PATH)
        finally:
            temp_path.unlink(missing_ok=True)


def _output_root_protected_paths() -> tuple[Path, ...]:
    program_root = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[1]
    )
    knowledge_root = getattr(KNOWLEDGE, "vault_path", None)
    roots = [APP_DIR, program_root]
    if knowledge_root:
        roots.append(Path(str(knowledge_root)))
    return tuple(roots)


def _validate_output_root(value: str | Path, *, test_write: bool = False) -> Path:
    return validate_output_root(
        value,
        default_root=OUTPUT_DIR,
        protected_roots=_output_root_protected_paths(),
        require_available=True,
        test_write=test_write,
    )


def _configured_output_roots(cfg: dict | None = None) -> tuple[Path, ...]:
    config = cfg if isinstance(cfg, dict) else load_config()
    values = [OUTPUT_DIR, config.get("output_root")]
    known = config.get("known_output_roots") or []
    if isinstance(known, list):
        values.extend(known)
    roots: list[Path] = []
    for value in values:
        if not str(value or "").strip():
            continue
        try:
            root = validate_output_root(
                value,
                default_root=OUTPUT_DIR,
                protected_roots=_output_root_protected_paths(),
                require_available=False,
                test_write=False,
            )
        except OutputRootError:
            continue
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def _output_root_state() -> dict[str, object]:
    return output_root_status(
        _RUNTIME_OUTPUT_ROOT,
        default_root=OUTPUT_DIR,
        protected_roots=_output_root_protected_paths(),
    )

API_KEY = load_api_key()

def get_api_key():
    global API_KEY
    # The API process serving Settings may be a passive sidecar. Reloading the
    # small atomic config here makes its update visible to the elected worker
    # process before every remote request.
    API_KEY = load_api_key()
    if not API_KEY:
        raise RuntimeError("API Key not configured. Please set it in Settings.")
    return API_KEY

def set_api_key(key: str):
    global API_KEY
    save_config({"api_key": key})
    API_KEY = key


def _normalize_pack_root(value: Any, *, kind: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("请选择本地扩展包目录")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValueError("本地扩展包必须使用绝对路径")
    root = candidate.resolve()
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise ValueError("不能把磁盘根目录或用户主目录作为扩展包")
    if kind == "runtime":
        status = verify_runtime_pack(root, full=False)
    else:
        status = verify_model_pack(root, GROUNDING_MODEL_MANIFEST_PATH, full=False)
    if status.get("status") != "ready":
        raise ValueError(str(status.get("message") or "本地扩展包不可用"))
    return root


def _grounding_pack_state(*, full: bool = False) -> dict[str, Any]:
    status = grounding_pack_status(
        _RUNTIME_GROUNDING_RUNTIME_ROOT,
        _RUNTIME_GROUNDING_MODEL_ROOT,
        GROUNDING_MODEL_MANIFEST_PATH,
        full=full,
    )
    runtime = status.get("runtime")
    if isinstance(runtime, dict):
        runtime.pop("manifest", None)
    return status


def _configured_grounding_adapter():
    global _GROUNDING_ADAPTER, _GROUNDING_ADAPTER_KEY
    runtime_root = _RUNTIME_GROUNDING_RUNTIME_ROOT
    model_root = _RUNTIME_GROUNDING_MODEL_ROOT
    if not runtime_root and not model_root:
        return None
    status = _grounding_pack_state(full=False)
    if not status.get("available"):
        return UnavailableGroundingAdapter(
            str(status.get("code") or "grounding_pack_unavailable").lower()
        )
    key = (runtime_root, model_root)
    with _GROUNDING_ADAPTER_LOCK:
        if _GROUNDING_ADAPTER_KEY != key:
            previous = _GROUNDING_ADAPTER
            _GROUNDING_ADAPTER = grounding_adapter_from_pack(
                runtime_root,
                model_root,
                GROUNDING_MODEL_MANIFEST_PATH,
            )
            _GROUNDING_ADAPTER_KEY = key
            if previous is not None and hasattr(previous, "close"):
                previous.close()
        return _GROUNDING_ADAPTER


def _dispose_grounding_adapter_if_changed(runtime_root: str, model_root: str) -> None:
    global _GROUNDING_ADAPTER, _GROUNDING_ADAPTER_KEY
    next_key = (runtime_root, model_root) if runtime_root and model_root else None
    with _GROUNDING_ADAPTER_LOCK:
        if _GROUNDING_ADAPTER_KEY == next_key:
            return
        previous = _GROUNDING_ADAPTER
        _GROUNDING_ADAPTER = None
        _GROUNDING_ADAPTER_KEY = None
        if previous is not None and hasattr(previous, "close"):
            previous.close()


def refresh_runtime_config() -> dict:
    """Refresh process-local runtime objects from the shared atomic config."""
    global API_KEY, _RUNTIME_KNOWLEDGE_PATH, _RUNTIME_OUTPUT_ROOT
    global _RUNTIME_GROUNDING_RUNTIME_ROOT, _RUNTIME_GROUNDING_MODEL_ROOT
    cfg = load_config()
    with _RUNTIME_CONFIG_LOCK:
        updates: dict[str, object] = {}
        configured_key = str(cfg.get("api_key") or "").strip()
        API_KEY = configured_key or load_api_key()
        configured_path = str(cfg.get("knowledge_base_path") or _RUNTIME_KNOWLEDGE_PATH).strip()
        canonical_path = str(canonicalize_vault_path(configured_path))
        if canonical_path != _RUNTIME_KNOWLEDGE_PATH:
            KNOWLEDGE.set_path(canonical_path)
            _RUNTIME_KNOWLEDGE_PATH = canonical_path
        if str(cfg.get("knowledge_base_path") or "").strip() != canonical_path:
            updates["knowledge_base_path"] = canonical_path

        configured_output = str(cfg.get("output_root") or OUTPUT_DIR).strip()
        try:
            canonical_output = validate_output_root(
                configured_output,
                default_root=OUTPUT_DIR,
                protected_roots=_output_root_protected_paths(),
                require_available=False,
                test_write=False,
            )
        except OutputRootError:
            canonical_output = OUTPUT_DIR.resolve()
        _RUNTIME_OUTPUT_ROOT = canonical_output
        if str(cfg.get("output_root") or "").strip() != str(canonical_output):
            updates["output_root"] = str(canonical_output)

        known_roots = [str(path) for path in _configured_output_roots({
            **cfg,
            "output_root": str(canonical_output),
        })]
        if cfg.get("known_output_roots") != known_roots:
            updates["known_output_roots"] = known_roots
        _RUNTIME_GROUNDING_RUNTIME_ROOT = str(
            cfg.get("grounding_runtime_root") or ""
        ).strip()
        _RUNTIME_GROUNDING_MODEL_ROOT = str(
            cfg.get("grounding_model_root") or ""
        ).strip()
        _dispose_grounding_adapter_if_changed(
            _RUNTIME_GROUNDING_RUNTIME_ROOT,
            _RUNTIME_GROUNDING_MODEL_ROOT,
        )
        if updates:
            save_config(updates)
            cfg = {**cfg, **updates}
    return cfg

def get_settings():
    cfg = refresh_runtime_config()
    return {
        "api_key": "***" if cfg.get("api_key") else "",
        "api_key_set": bool(cfg.get("api_key")),
        "default_model": cfg.get("default_model", "gpt-image-2"),
        "default_platter": cfg.get("default_platter", "auto"),
        "default_angle": cfg.get("default_angle", "auto"),
        "default_fidelity": cfg.get("default_fidelity", 40),
        "auto_refine": cfg.get("auto_refine", True),
        "output_dir": str(_RUNTIME_OUTPUT_ROOT),
        "output_root": str(_RUNTIME_OUTPUT_ROOT),
        "output_root_status": _output_root_state(),
        "internal_data_dir": str(APP_DIR),
        "knowledge_base_path": _RUNTIME_KNOWLEDGE_PATH,
        "knowledge": KNOWLEDGE.status(),
        "grounding_runtime_root": _RUNTIME_GROUNDING_RUNTIME_ROOT,
        "grounding_model_root": _RUNTIME_GROUNDING_MODEL_ROOT,
        "grounding_pack": _grounding_pack_state(full=False),
    }

# ======================== PROGRESS TRACKING ========================
class ProgressTracker:
    def __init__(self):
        self._tasks = {}
        self._lock = threading.Lock()

    def create_task(self, task_id: str):
        with self._lock:
            self._tasks[task_id] = {"progress": 0, "status": "starting", "message": "", "logs": [], "results": None, "error": None}

    def update(self, task_id: str, progress: float = None, status: str = None, message: str = None, log: str = None):
        with self._lock:
            t = self._tasks.get(task_id, {})
            if progress is not None: t["progress"] = progress
            if status is not None: t["status"] = status
            if message is not None: t["message"] = message
            if log is not None:
                t.setdefault("logs", []).append(f"[{datetime.now().strftime('%H:%M:%S')}] {log}")

    def complete(self, task_id: str, results=None, error=None):
        with self._lock:
            t = self._tasks.get(task_id, {})
            if error:
                t["status"] = "error"
                t["error"] = error
            else:
                t["status"] = "completed"
                t["progress"] = 1.0
                t["results"] = results

    def get(self, task_id: str):
        with self._lock:
            return dict(self._tasks.get(task_id, {}))

    def cleanup(self, task_id: str):
        with self._lock:
            self._tasks.pop(task_id, None)

tracker = ProgressTracker()

# ======================== UTILS ========================
def _get_http_session():
    s = requests.Session()
    s.trust_env = True
    s.headers.update({"User-Agent": "ProductAtelier/1.0"})
    try:
        import certifi
        s.verify = certifi.where()
    except Exception:
        s.verify = True
    return s

_http_sessions = threading.local()


def _current_http_session():
    session = getattr(_http_sessions, "session", None)
    if session is None:
        session = _get_http_session()
        _http_sessions.session = session
    return session

def api_request(method, path, body=None, timeout=120):
    url = BASE_URL + path
    key = get_api_key()
    headers = {"Authorization": f"Bearer {key}"}
    try:
        resp = _current_http_session().request(method, url, json=body, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except (requests.exceptions.ProxyError, requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
        log_msg("system", f"代理连接失败({type(e).__name__}),尝试直连...")
        direct = requests.Session()
        direct.trust_env = False
        direct.headers.update({"User-Agent": "ProductAtelier/1.0"})
        try:
            import certifi
            direct.verify = certifi.where()
        except Exception:
            direct.verify = True
        resp = direct.request(method, url, json=body, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

def image_to_reference_payload(img):
    """Return the exact provider payload plus a reproducible transform receipt."""
    source_kind = type(img).__name__
    transform = {"operation": "preserve-bytes"}
    width = height = None
    source_mode = ""
    if isinstance(img, (str, Path)):
        p = Path(img)
        b = p.read_bytes()
        mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
        source_kind = "path"
        try:
            with Image.open(p) as opened:
                width, height = opened.size
                source_mode = opened.mode
        except Exception:
            pass
    elif isinstance(img, Image.Image):
        width, height = img.size
        source_mode = img.mode
        buf = io.BytesIO()
        if img.mode == "RGBA":
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=96)
        b = buf.getvalue()
        mime = "image/jpeg"
        source_kind = "pillow-image"
        transform = {
            "operation": "encode-jpeg",
            "quality": 96,
            "alpha_handling": "flatten-to-rgb" if source_mode == "RGBA" else "none",
        }
    elif isinstance(img, bytes):
        b = img
        mime = "image/jpeg"
        source_kind = "bytes"
    else:
        raise TypeError(f"Unsupported image type: {type(img)}")
    b64 = base64.b64encode(b).decode("ascii")
    return f"data:{mime};base64,{b64}", {
        "source_kind": source_kind,
        "source_width": width,
        "source_height": height,
        "source_mode": source_mode,
        "provider_mime": mime,
        "provider_byte_count": len(b),
        "provider_sha256": hashlib.sha256(b).hexdigest(),
        "transform": transform,
    }


def image_to_data_url(img):
    return image_to_reference_payload(img)[0]

def image_to_bytes(img, fmt="JPEG", quality=96):
    buf = io.BytesIO()
    if fmt == "PNG":
        img.save(buf, format="PNG")
    else:
        if img.mode == "RGBA":
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()

def download_result(url, dest_path):
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = _current_http_session().get(url, timeout=120)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    except Exception:
        direct = requests.Session()
        direct.trust_env = False
        try:
            import certifi
            direct.verify = certifi.where()
        except Exception:
            direct.verify = True
        resp = direct.get(url, timeout=120)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    return str(dest)

def log_msg(task_id, msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(OUTPUT_DIR / "workbench.log", "a", encoding="utf-8") as fp:
            fp.write(line + "\n")
    except:
        pass
    tracker.update(task_id, log=msg)

def save_temp(img, prefix="tmp"):
    p = OUTPUT_DIR / "_tmp" / f"{safe_stem(prefix, 'tmp')}_{uuid.uuid4().hex}.jpg"
    if img.mode == "RGBA":
        img = img.convert("RGB")
    img.save(p, "JPEG", quality=95)
    return str(p)

# ======================== IMAGE PROCESSING ========================
_BGSESSION = None
_BGSESSION_LOCK = threading.Lock()
_BG_INFERENCE_LOCK = threading.Lock()

def _get_bgsession():
    global _BGSESSION
    if _BGSESSION is None:
        with _BGSESSION_LOCK:
            if _BGSESSION is None:
                from rembg import new_session
                _BGSESSION = new_session("birefnet-general")
    return _BGSESSION

def post_process_enhance(img):
    if img.mode != "RGB": img = img.convert("RGB")
    img = ImageOps.autocontrast(img, cutoff=1)
    img = ImageEnhance.Contrast(img).enhance(1.08)
    img = ImageEnhance.Color(img).enhance(1.05)
    img = ImageEnhance.Sharpness(img).enhance(1.15)
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=130, threshold=3))
    return img

def remove_bg_hd(img):
    from rembg import remove
    session = _get_bgsession()
    if isinstance(img, (str, Path)): img = Image.open(img)
    if img.mode != "RGBA": img = img.convert("RGBA")
    # alpha_matting disabled to avoid pymatting/numba dependency (~120MB)
    # BiRefNet already produces a soft alpha mask. rembg's post-processing
    # thresholds that mask to binary, which creates visibly jagged product edges.
    with _BG_INFERENCE_LOCK:
        return remove(img, session=session, alpha_matting=False,
                      post_process_mask=False)

def tight_crop_alpha(img, pad_pct=0.06):
    if img.mode != "RGBA": img = img.convert("RGBA")
    # Transparent PNGs commonly retain non-zero RGB below alpha=0. Cropping the
    # RGBA union therefore keeps the whole canvas; only alpha defines content.
    bbox = img.getchannel("A").getbbox()
    if bbox is None: return img
    w, h = img.size
    pad_x, pad_y = int(w*pad_pct), int(h*pad_pct)
    return img.crop((max(0,bbox[0]-pad_x), max(0,bbox[1]-pad_y), min(w,bbox[2]+pad_x), min(h,bbox[3]+pad_y)))

def crop_product(img, bbox, w, h, pad_pct=0.12):
    x1,y1,x2,y2 = bbox
    bw,bh = x2-x1, y2-y1
    px,py = int(bw*pad_pct), int(bh*pad_pct)
    return img.crop((max(0,x1-px), max(0,y1-py), min(w,x2+px), min(h,y2+py)))

# ======================== AI PIPELINE ========================
def build_negative(platter_mode):
    neg = NEG_BASE
    if platter_mode == "remove": neg += "," + NEG_REMOVE_PLATE
    return neg

def build_single_prompt(product_name, platter_mode="auto", product_type="food", angle="auto"):
    if platter_mode == "remove":
        plate = "产品直接置于纯白背景上悬浮展示，不使用任何盘子、碟子、托盘、木板、器皿、餐垫、容器，画面极简干净"
    elif platter_mode == "keep":
        plate = "保留产品原有的精致器皿或摆盘（如瓷盘、竹垫、玻璃碗、陶瓷板等），器皿质感高端，呈现高级商业摆盘效果，器皿与产品自然搭配"
    else:
        plate = "智能处理：无包装的裸食品保留精致器皿呈现高级商业感；包装类产品（袋装/盒装/瓶装/罐装）直接纯白底展示无需额外器皿"
    angle_desc = ANGLE_PROMPT.get(angle, ANGLE_PROMPT["auto"])
    if angle == "auto":
        angle_desc = "略微俯视30-45度角(three-quarter view)，产品居中端正构图" if product_type != "packaging" else "正面平视角度，产品端正居中构图，包装标签清晰可见"
    elif angle == "keep":
        angle_desc = "严格保持参考图原始角度透视，产品居中端正构图"
    return (f"专业商业影棚拍摄的电商主图，{product_name}，{angle_desc}，产品占据画面70%面积，"
            f"纯白背景(#FFFFFF)，柔和无影灯光，顶部柔光箱加双侧45度补光，无投影，"
            f"{plate}，产品细节清晰锐利，材质质感真实，色彩准确饱和，高光自然，阴影柔和，"
            f"高端电商产品摄影，8K超清，影棚级画质，专业修图，干净极简构图，广告级质感")

def build_stage2_prompt(product_name, platter_mode="auto", product_type="food", angle="auto"):
    plate = "智能保留或去除器皿，整体协调"
    if platter_mode == "remove": plate = "无任何器皿托盘，产品直接纯白底"
    elif platter_mode == "keep": plate = "精致器皿摆盘自然，高级商业感"
    return (f"精修优化这张电商主图：{product_name}，纯白影棚背景，光线完美柔和均匀，{plate}，"
            f"按目标角度修正产品方向使其端正居中，保持角度准确，修正构图使产品占画面70%，"
            f"增强材质细节纹理（食物表皮光泽、包装材质质感、器皿反光），锐化产品边缘，"
            f"提升色彩饱和度和对比度至商业级标准，修复任何变形或不自然的部分，补全缺失的产品细节和边缘，"
            f"确保产品完整不被裁切，边缘清晰干净，白底纯白无杂色无灰斑，最终呈现超高清商业影棚级电商主图效果")


def build_adjustment_prompt(instruction):
    return (
        "对这张已经生成的电商成品图做一次定向局部修改。"
        "必须保持未被用户点名的主体形态、数量、包装、文字、构图、视角、背景、光影、"
        "色彩关系和画布比例不变；不要重新设计整张图，不要增加无关元素。"
        f"本次唯一调整要求：{str(instruction).strip()}。"
        "修改后仍需保持商业成品清晰度、自然边缘和真实材质，原图中正确的部分全部保留。"
    )

def build_multi_stage1_prompt(product_name, product_type="food", completeness="complete", platter_mode="auto", angle="auto"):
    angle_desc = ANGLE_PROMPT.get(angle, ANGLE_PROMPT["auto"])
    if angle == "auto":
        angle_desc = "略微俯视30-45度角，产品居中端正构图" if product_type != "packaging" else "正面平视视角，产品居中端正构图"
    elif angle == "keep":
        angle_desc = "严格保持参考图原始角度透视，产品居中端正构图"
    complete_hint = "AI补全被裁切的产品部分，修复不完整的产品边缘，还原完整产品形态（保持原产品外观颜色质感一致），" if completeness == "cutoff" else ""
    if platter_mode == "remove": plate = "产品直接纯白背景，无器皿无托盘"
    elif platter_mode == "keep": plate = "搭配精致器皿呈现摆盘高级感"
    else: plate = "根据产品类型智能选择是否保留器皿"
    return (f"专业商业影棚电商主图，单个{product_name}，{angle_desc}，纯白背景(#FFFFFF)，柔和无影灯光，"
            f"顶部柔光加双侧补光，{plate}，{complete_hint}产品居中，占据画面70%面积，细节清晰锐利，"
            f"材质质感真实，色彩准确饱和，高端产品摄影，8K画质")

def submit_generate(
    prompt,
    model_key,
    ref_data_url=None,
    size="2048x2048",
    negative_prompt=None,
    output_spec=None,
):
    spec = dict(output_spec or {})
    params = dict(spec.get("provider_params") or {})
    if not params:
        # Preserve compatibility for legacy callers, but use the current GPT
        # parameter name instead of the obsolete universal imageSize field.
        params["size"] = size
    if negative_prompt:
        params["negative_prompt"] = negative_prompt
    if ref_data_url:
        # Current LK media contracts use an array for references even when only
        # one image is supplied. A singular `image` can be silently ignored.
        params["images"] = [ref_data_url]
    resp = api_request(
        "POST",
        "/v1/media/generate",
        body={"model": model_key, "prompt": prompt, "params": params},
        timeout=300,
    )
    if resp.get("code") != 200: raise RuntimeError(f"API error: {resp.get('msg', resp)}")
    return str(resp["data"]["task_id"])

def poll_task(task_id, task_id_ref="?", timeout_sec=480, interval=6):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        time.sleep(interval)
        try:
            st = api_request("GET", f"/v1/skills/task-status?task_id={task_id}", timeout=60)
        except Exception as e:
            log_msg(task_id_ref, f"轮询异常: {e}, 重试...")
            continue
        state = st.get("state","")
        prog = st.get("progress",0)
        log_msg(task_id_ref, f"状态: {state} 进度: {prog}%")
        if st.get("is_final"):
            if state == "success": return st["result_url"]
            else: raise RuntimeError(f"生成失败: {json.dumps(st, ensure_ascii=False)[:300]}")
    raise TimeoutError(f"任务超时 ({timeout_sec}s)")

def ai_i2i(
    prompt,
    ref_img,
    model_key,
    negative_prompt=None,
    size="2048x2048",
    stage="?",
    tid_ref="?",
    on_submitted=None,
    on_evidence=None,
    output_spec=None,
):
    total_started = time.perf_counter()
    phase = "reference.encode"
    evidence = {
        "trace_contract_version": GENERATION_TRACE_CONTRACT_VERSION,
        "timings_ms": {},
        "completed": False,
    }
    tmp = None
    try:
        phase_started = time.perf_counter()
        ref_url, reference_evidence = image_to_reference_payload(ref_img)
        evidence["reference"] = reference_evidence
        evidence["timings_ms"]["reference_encode"] = round(
            (time.perf_counter() - phase_started) * 1000, 3
        )

        phase = "provider.submit"
        log_msg(tid_ref, f"[S{stage}] 提交生成 ({model_key})...")
        phase_started = time.perf_counter()
        tid = submit_generate(
            prompt,
            model_key,
            ref_url,
            size=size,
            negative_prompt=negative_prompt,
            output_spec=output_spec,
        )
        evidence["timings_ms"]["submit"] = round(
            (time.perf_counter() - phase_started) * 1000, 3
        )
        if on_submitted is not None:
            on_submitted(tid)
        log_msg(tid_ref, f"[S{stage}] 任务ID: {tid}")

        phase = "provider.poll"
        phase_started = time.perf_counter()
        result_url = poll_task(tid, task_id_ref=tid_ref)
        evidence["timings_ms"]["poll"] = round(
            (time.perf_counter() - phase_started) * 1000, 3
        )

        phase = "result.download"
        tmp = OUTPUT_DIR / "_tmp" / (
            f"stage_{safe_stem(str(stage), 'stage')}_{uuid.uuid4().hex}.jpg"
        )
        phase_started = time.perf_counter()
        download_result(result_url, tmp)
        evidence["timings_ms"]["download"] = round(
            (time.perf_counter() - phase_started) * 1000, 3
        )

        phase = "result.decode"
        phase_started = time.perf_counter()
        with Image.open(tmp) as downloaded:
            result = downloaded.copy()
        evidence["timings_ms"]["decode"] = round(
            (time.perf_counter() - phase_started) * 1000, 3
        )
        evidence["result"] = {
            "width": result.width,
            "height": result.height,
            "mode": result.mode,
        }
        evidence["completed"] = True
        return result
    except Exception as exc:
        evidence["failure"] = {
            "phase": phase,
            "error_type": type(exc).__name__,
        }
        raise
    finally:
        evidence["timings_ms"]["total"] = round(
            (time.perf_counter() - total_started) * 1000, 3
        )
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        if on_evidence is not None:
            try:
                on_evidence(dict(evidence))
            except Exception as exc:
                print(f"[trace] provider evidence callback failed: {exc}", file=sys.stderr, flush=True)

def vlm_detect_products(image_path, tid_ref="?"):
    img_url = image_to_data_url(image_path)
    prompt = ("请分析这张图片，识别图中所有产品（食品/商品），以严格JSON格式返回结果。\n"
        "要求：\n1. 检测每个独立产品的位置bbox[x1,y1,x2,y2]，坐标为0-1000整数(相对宽高)\n"
        "2. name: 中文具体产品名\n3. ptype: food/packaging/dish\n"
        "4. has_container: true/false，是否有器皿\n5. cutoff: true/false，是否被边缘裁切\n"
        "6. angle_hint: 最佳拍摄角度建议\n\n只返回纯JSON：\n"
        '{"products":[{"bbox":[x1,y1,x2,y2],"name":"产品名","ptype":"food|packaging|dish","has_container":true|false,"cutoff":true|false,"angle_hint":"角度建议"}],"count":N,"scene":"single|multi"}')
    body = {"model": "gemini-3.5-flash", "messages": [{"role": "user", "content": [
        {"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": img_url}}]}],
        "max_tokens": 2048, "temperature": 0.1}
    try:
        resp = api_request("POST", "/v1/chat/completions", body=body, timeout=60)
        text = resp["choices"][0]["message"]["content"].strip()
        if text.startswith("```json"): text = text[7:]
        if text.startswith("```"): text = text[3:]
        if text.endswith("```"): text = text[:-3]
        return json.loads(text.strip())
    except Exception as exc:
        # A group-shot detection failure must never masquerade as one product
        # covering the whole frame: doing so would trigger paid generation for
        # a result that violates the requested workflow. Single-product jobs
        # also fail before spending image-generation quota when recognition is
        # unavailable and no explicit product name was supplied.
        log_msg(tid_ref, f"VLM检测失败: {type(exc).__name__}")
        raise JobExecutionError(
            "PRODUCT_DETECTION_FAILED",
            "Product detection failed before image generation",
            metadata={"cause_type": type(exc).__name__},
        ) from exc

def fidelity_suffix(fidelity):
    if fidelity <= 25: return "极严格保留参考图中产品的原始形态、颜色、纹理、大小比例和所有细节，只允许更换纯白背景和影棚灯光，禁止改变产品本身的任何外观特征，产品必须与参考图完全一致。"
    elif fidelity <= 50: return "严格保留参考图中产品的整体形态、颜色和主要特征，可适度优化光影效果和背景，但产品的款式、颜色、纹理、装饰、文字标识都必须与参考图保持一致。"
    elif fidelity <= 75: return "保留参考图中产品的基本形态和类型，在保持产品辨识度的前提下可适度增强质感、优化构图、提升细节表现，使整体更具商业摄影品质。"
    else: return "在参考图产品基础上进行专业商业摄影级优化，可调整角度、增强细节质感、优化构图和光影，呈现最佳商业影棚效果，产品保持可识别但允许较大美化。"

# ======================== FASTAPI APP ========================
JOB_ENGINE = None


def build_job_engine():
    worker_limit = max(1, min(int(os.environ.get("PRODUCT_ATELIER_JOB_WORKERS", "4")), 12))
    configured_cloud_limit = max(
        1, min(int(os.environ.get("PRODUCT_ATELIER_CLOUD_LIMIT", "2")), 8)
    )
    # Keep one worker lane available for a different engine whenever the pool
    # can run concurrently. This is what lets a local cutout start during a
    # long cloud batch instead of sitting behind cloud tasks waiting on a gate.
    cloud_limit = min(
        configured_cloud_limit,
        max(1, worker_limit - 1) if worker_limit > 1 else 1,
    )
    # rembg/BiRefNet session concurrency stays at one until a dedicated model
    # memory/stability stress suite proves a higher safe value.
    cutout_limit = 1
    return JobEngine(
        LEDGER,
        processors={
            "cloud-workflow": execute_job_workflow,
            "group-workflow": execute_job_workflow,
            "local-cutout": execute_job_workflow,
        },
        max_workers=worker_limit,
        resource_limits={
            "vlm": max(1, min(int(os.environ.get("PRODUCT_ATELIER_VLM_LIMIT", "1")), 4)),
            "cloud-image": cloud_limit,
            "local-cutout": cutout_limit,
        },
        processor_admission_resources={
            "cloud-workflow": "cloud-image",
            "group-workflow": "cloud-image",
            "local-cutout": "local-cutout",
        },
    )


@asynccontextmanager
async def app_lifespan(_app):
    global JOB_ENGINE
    engine = build_job_engine()
    JOB_ENGINE = engine
    engine.start()
    try:
        yield
    finally:
        engine.stop()
        if JOB_ENGINE is engine:
            JOB_ENGINE = None


TRUSTED_LOCAL_ORIGINS = frozenset({
    # Tauri's packaged custom-protocol origins across supported WebView hosts.
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    # The checked-in Vite development URL and its loopback equivalent.
    "http://localhost:1420",
    "http://127.0.0.1:1420",
})


app = FastAPI(title="Product Atelier API", lifespan=app_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(TRUSTED_LOCAL_ORIGINS),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Accept", "Content-Type"],
)


@app.middleware("http")
async def reject_untrusted_browser_origin(request: Request, call_next):
    """Reject browser requests before simple cross-origin POSTs reach an API.

    CORS response headers alone prevent a hostile page from reading a response,
    but a multipart/form-data request can still be sent without a preflight.
    Rejecting an explicit untrusted Origin therefore also protects job creation,
    asset imports, cancellation, and settings mutations. Requests without an
    Origin remain available to the native Rust health probe and local tooling.
    """
    origin = request.headers.get("origin")
    if origin and origin not in TRUSTED_LOCAL_ORIGINS:
        return JSONResponse(
            {
                "detail": {
                    "code": "UNTRUSTED_ORIGIN",
                    "message": "Browser origin is not allowed to access the local sidecar",
                }
            },
            status_code=403,
        )
    return await call_next(request)

@app.get("/api/health")
async def health():
    refresh_runtime_config()
    configured_key = load_api_key()
    return {
        "status": "ok",
        "service": sidecar_runtime_info(),
        "api_key_configured": bool(configured_key),
        "output_dir": str(_RUNTIME_OUTPUT_ROOT),
        "output_root_status": _output_root_state(),
        "ledger": {
            "schema_version": LEDGER.stats()["schema_version"],
            "startup_repair": LEDGER.last_schema_repair,
        },
    }


@app.get("/api/ledger/status")
async def ledger_status():
    return LEDGER.stats()


def workspace_asset_response(asset: dict) -> dict:
    response = {
        "id": asset["id"],
        "name": asset.get("name", ""),
        "mime": asset.get("mime", ""),
        "size_bytes": asset.get("size_bytes", 0),
        "width": asset.get("width"),
        "height": asset.get("height"),
        "sha256": asset.get("sha256", ""),
        "created_at": asset.get("created_at"),
        "metadata": asset.get("metadata", {}),
        "thumbnail_url": f"/api/assets/{asset['id']}/thumbnail",
        "content_url": f"/api/assets/{asset['id']}/content",
    }
    if asset.get("membership"):
        response["membership"] = dict(asset["membership"])
    return response


def result_asset_response(asset: dict) -> dict:
    return {
        "id": asset["id"],
        "name": asset.get("name", ""),
        "mime": asset.get("mime", ""),
        "size_bytes": 0,
        "width": asset.get("width"),
        "height": asset.get("height"),
        "sha256": asset.get("sha256", ""),
        "created_at": asset.get("created_at"),
        "metadata": asset.get("metadata", {}),
        "role": asset.get("role", ""),
        "thumbnail_url": f"/api/assets/{asset['id']}/thumbnail",
        "content_url": f"/api/assets/{asset['id']}/content",
    }


def _resolve_result_asset_path(asset: dict) -> Path:
    if asset.get("role") not in {"result_main", "result_cutout"}:
        raise AssetAccessError("Asset is not an exported generation result", code="ASSET_NOT_FOUND")
    candidate = Path(str(asset.get("path", ""))).resolve(strict=False)
    allowed_roots = _configured_output_roots()
    asset_metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
    declared_value = str(asset_metadata.get("output_root") or "").strip()
    declared_root = canonicalize_output_root(declared_value) if declared_value else None
    if declared_root is not None:
        roots = (declared_root,) if declared_root in allowed_roots else ()
    else:
        # Results created before output-root snapshots existed use the root that
        # contains their persisted path, including the legacy internal root.
        roots = tuple(root for root in allowed_roots if candidate.is_relative_to(root))
    if not roots or not any(candidate.is_relative_to(root) for root in roots):
        raise AssetAccessError("Generation result path is outside the allowed root")
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise AssetAccessError("Generation result file is unavailable", code="ASSET_FILE_MISSING") from exc
    if not resolved.is_file() or not any(resolved.is_relative_to(root) for root in roots):
        raise AssetAccessError("Generation result path is outside the allowed root")
    return resolved


def _thumbnail_for_path(path: Path, max_size: int = 512) -> bytes:
    max_size = max(32, min(int(max_size), 1024))
    with Image.open(path) as image:
        image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        if image.mode in {"RGBA", "LA"}:
            background = Image.new("RGB", image.size, "white")
            background.paste(image.convert("RGB"), mask=image.getchannel("A"))
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=86, optimize=True)
        return buffer.getvalue()


def raise_asset_http_error(exc: AssetStoreError) -> None:
    if isinstance(exc, AssetAccessError):
        status_code = 404 if exc.code in {"ASSET_NOT_FOUND", "ASSET_FILE_MISSING"} else 403
    elif isinstance(exc, AssetValidationError):
        if exc.code == "FILE_TOO_LARGE":
            status_code = 413
        elif exc.code in {"UNSUPPORTED_EXTENSION", "UNSUPPORTED_IMAGE_FORMAT", "EXTENSION_MISMATCH"}:
            status_code = 415
        else:
            status_code = 400
    else:
        status_code = 500
    raise HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


@app.get("/api/assets")
async def list_workspace_assets(limit: int = 500):
    assets = LEDGER.list_workspace_assets(limit)
    return {
        "assets": [workspace_asset_response(asset) for asset in assets],
        "count": len(assets),
    }


def _validate_collection_key(collection: str) -> str:
    collection = str(collection).strip()
    if collection not in {"product", "group", "cutout"}:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_COLLECTION", "message": f"Unsupported asset collection: {collection}"},
        )
    return collection


@app.post("/api/assets/import")
async def import_workspace_asset(file: UploadFile = File(...), collection: str = "product"):
    collection = _validate_collection_key(collection)
    try:
        asset = await run_in_threadpool(
            ASSET_STORE.import_stream,
            file.file,
            file.filename or "upload",
            collection,
        )
        return workspace_asset_response(asset)
    except AssetStoreError as exc:
        raise_asset_http_error(exc)
    finally:
        await file.close()


@app.post("/api/assets/import-batch")
async def import_workspace_assets(
    files: list[UploadFile] = File(...),
    collection: str = "product",
):
    collection = _validate_collection_key(collection)
    if not files:
        raise HTTPException(status_code=400, detail={"code": "EMPTY_BATCH", "message": "No files provided"})
    if len(files) > 100:
        raise HTTPException(
            status_code=400,
            detail={"code": "TOO_MANY_FILES", "message": "A workspace import accepts at most 100 files"},
        )
    imported = []
    errors = []
    for position, file in enumerate(files):
        try:
            asset = await run_in_threadpool(
                ASSET_STORE.import_stream,
                file.file,
                file.filename or f"upload-{position}",
                collection,
            )
            imported.append(workspace_asset_response(asset))
        except AssetStoreError as exc:
            errors.append({
                "position": position,
                "name": file.filename or "",
                "code": exc.code,
                "message": str(exc),
            })
        finally:
            await file.close()
    return {"assets": imported, "errors": errors, "count": len(imported)}


class FolderSourceRequest(BaseModel):
    folder_path: str

    class Config:
        extra = "forbid"


def _resolve_source_folder(value: str) -> Path:
    raw = str(value or "").strip().strip('"').strip("'")
    if not raw:
        raise ValueError("请输入图片文件夹地址")
    try:
        folder = Path(raw).expanduser().resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ValueError("文件夹不存在或当前无法访问") from exc
    if not folder.is_dir():
        raise ValueError("输入路径不是文件夹")
    return folder


def _folder_delivery_name() -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{FOLDER_DELIVERY_PREFIX}{timestamp}-{uuid.uuid4().hex[:6]}"


@app.post("/api/folder-sources/import")
async def import_folder_sources(request: FolderSourceRequest):
    """Import one flat image folder into the durable product workspace.

    Folder traversal is intentionally non-recursive. Existing Product Atelier
    delivery folders are directories and therefore never re-imported.
    """
    try:
        folder = _resolve_source_folder(request.folder_path)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_FOLDER", "message": str(exc)},
        )
    try:
        image_paths = sorted(
            path for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in FOLDER_IMAGE_EXTENSIONS
        )
    except OSError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": "FOLDER_ACCESS_DENIED", "message": "无法读取这个文件夹"},
        ) from exc
    if not image_paths:
        raise HTTPException(
            status_code=400,
            detail={"code": "EMPTY_IMAGE_FOLDER", "message": "文件夹内没有 JPG、PNG 或 WEBP 图片"},
        )
    if len(image_paths) > FOLDER_SCAN_MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "FOLDER_TOO_LARGE",
                "message": f"当前单次最多接收 {FOLDER_SCAN_MAX_FILES} 张，检测到 {len(image_paths)} 张",
            },
        )

    imported = []
    errors = []
    source_names = {}
    seen_asset_ids = set()
    for position, path in enumerate(image_paths):
        try:
            with path.open("rb") as handle:
                asset = await run_in_threadpool(
                    ASSET_STORE.import_stream,
                    handle,
                    path.name,
                    "product",
                )
            asset_id = str(asset["id"])
            if asset_id in seen_asset_ids:
                errors.append({
                    "position": position,
                    "name": path.name,
                    "code": "DUPLICATE_CONTENT",
                    "message": "与本文件夹中另一张图片内容完全相同，已去重",
                })
                continue
            seen_asset_ids.add(asset_id)
            source_names[asset_id] = path.name
            imported.append(workspace_asset_response(asset))
        except (AssetStoreError, OSError) as exc:
            errors.append({
                "position": position,
                "name": path.name,
                "code": getattr(exc, "code", "FILE_READ_ERROR"),
                "message": str(exc),
            })

    if not imported:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "FOLDER_IMPORT_FAILED",
                "message": "文件夹中的图片均未能导入",
                "errors": errors[:20],
            },
        )
    delivery_root = folder / _folder_delivery_name()
    folder_batch = {
        "batch_id": f"folder-{uuid.uuid4().hex}",
        "source_folder": str(folder),
        "delivery_root": str(delivery_root),
        "asset_ids": [asset["id"] for asset in imported],
        "source_names": source_names,
        "detected_count": len(image_paths),
        "imported_count": len(imported),
        "error_count": len(errors),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    return {
        "folder_batch": folder_batch,
        "assets": imported,
        "errors": errors,
        "count": len(imported),
    }


@app.get("/api/assets/{asset_id}")
async def get_workspace_asset(asset_id: str):
    try:
        asset = LEDGER.get_asset(asset_id)
        if asset.get("role") == "workspace_source":
            return workspace_asset_response(LEDGER.get_workspace_asset(asset_id))
        if asset.get("role") in {"result_main", "result_cutout"}:
            return result_asset_response(asset)
        raise KeyError(f"asset is not externally readable: {asset_id}")
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "ASSET_NOT_FOUND", "message": str(exc)},
        )


@app.get("/api/assets/{asset_id}/content")
async def get_workspace_asset_content(asset_id: str):
    try:
        asset = LEDGER.get_asset(asset_id)
        if asset.get("role") == "workspace_source":
            asset, path = await run_in_threadpool(ASSET_STORE.resolve_asset_path, asset_id)
        else:
            path = await run_in_threadpool(_resolve_result_asset_path, asset)
        return FileResponse(str(path), media_type=asset["mime"], filename=asset["name"])
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "ASSET_NOT_FOUND", "message": str(exc)},
        )
    except AssetStoreError as exc:
        raise_asset_http_error(exc)


@app.get("/api/assets/{asset_id}/thumbnail")
async def get_workspace_asset_thumbnail(asset_id: str, size: int = 512):
    try:
        asset = LEDGER.get_asset(asset_id)
        if asset.get("role") == "workspace_source":
            content = await run_in_threadpool(ASSET_STORE.thumbnail_bytes, asset_id, size)
        else:
            path = await run_in_threadpool(_resolve_result_asset_path, asset)
            content = await run_in_threadpool(_thumbnail_for_path, path, size)
        return StreamingResponse(
            io.BytesIO(content),
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=3600"},
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "ASSET_NOT_FOUND", "message": str(exc)},
        )
    except AssetStoreError as exc:
        raise_asset_http_error(exc)


class WorkflowDraftRequest(BaseModel):
    expected_revision: int
    selected_asset_ids: list[str] = Field(default_factory=list)
    brief: dict[str, Any] = Field(default_factory=dict)
    intent: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    active_job_id: Optional[str] = None
    current_generation_id: Optional[str] = None
    current_result_asset_id: Optional[str] = None
    compare_state: dict[str, Any] = Field(default_factory=dict)
    ui_state: dict[str, Any] = Field(default_factory=dict)
    mask_state: dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"


class WorkflowCompletionRequest(BaseModel):
    expected_revision: int
    client_request_id: str
    job_id: str
    result_asset_id: str

    class Config:
        extra = "forbid"


class CollectionOrderRequest(BaseModel):
    asset_ids: list[str]

    class Config:
        extra = "forbid"


class ExecutionTraceRequest(BaseModel):
    client_request_id: str
    stage: str
    status: str
    job_item_id: Optional[str] = None
    generation_id: Optional[str] = None
    user_input: dict[str, Any] = Field(default_factory=dict)
    compiled_prompt: str = ""
    applied_knowledge: list[Any] = Field(default_factory=list)
    ignored_fields: list[Any] = Field(default_factory=list)
    model: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""

    class Config:
        extra = "forbid"


class ResultReviewRequest(BaseModel):
    client_request_id: str
    result_asset_id: str
    decision: str
    generation_id: Optional[str] = None
    reason_codes: list[str] = Field(default_factory=list)
    note: str = ""
    learning_action: str = "none"

    class Config:
        extra = "forbid"


class ResultAdjustmentRequest(BaseModel):
    client_request_id: str
    result_asset_id: str
    generation_id: Optional[str] = None
    reason_codes: list[str] = Field(default_factory=list)
    note: str

    class Config:
        extra = "forbid"


@app.get("/api/collections/{collection_key}/assets")
async def list_scoped_assets(
    collection_key: str,
    limit: int = 100,
    offset: int = 0,
    include_trashed: bool = False,
):
    collection_key = _validate_collection_key(collection_key)
    assets = LEDGER.list_collection_assets(
        collection_key,
        include_trashed=include_trashed,
        limit=limit,
        offset=offset,
    )
    return {
        "collection": collection_key,
        "assets": [workspace_asset_response(asset) for asset in assets],
        "count": len(assets),
        "total": LEDGER.count_collection_assets(
            collection_key, include_trashed=include_trashed
        ),
        "limit": max(1, min(int(limit), 2000)),
        "offset": max(0, int(offset)),
    }


@app.put("/api/collections/{collection_key}/order")
async def reorder_scoped_assets(
    collection_key: str, request: CollectionOrderRequest
):
    collection_key = _validate_collection_key(collection_key)
    try:
        assets = LEDGER.reorder_collection_assets(collection_key, request.asset_ids)
        return {
            "collection": collection_key,
            "assets": [workspace_asset_response(asset) for asset in assets],
            "count": len(assets),
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_COLLECTION_ORDER", "message": str(exc)},
        )


@app.post("/api/collections/{collection_key}/assets/{asset_id}")
@app.post("/api/collections/{collection_key}/assets/{asset_id}/restore")
async def restore_scoped_asset(collection_key: str, asset_id: str):
    collection_key = _validate_collection_key(collection_key)
    try:
        asset = LEDGER.add_asset_to_collection(asset_id, collection_key)
        return {"asset": workspace_asset_response(asset), "restored": True}
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "ASSET_NOT_FOUND", "message": str(exc)},
        )


@app.delete("/api/collections/{collection_key}/assets/{asset_id}")
async def remove_scoped_asset(collection_key: str, asset_id: str):
    collection_key = _validate_collection_key(collection_key)
    try:
        asset = LEDGER.remove_asset_from_collection(asset_id, collection_key)
        return {"asset": workspace_asset_response(asset), "removed": True}
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "COLLECTION_MEMBER_NOT_FOUND", "message": str(exc)},
        )


@app.get("/api/trash")
async def list_asset_trash(collection: Optional[str] = None, limit: int = 200):
    keys = [_validate_collection_key(collection)] if collection else ["product", "group", "cutout"]
    groups = {}
    for key in keys:
        items = [
            asset for asset in LEDGER.list_collection_assets(
                key, include_trashed=True, limit=limit
            ) if asset["membership"]["status"] == "trashed"
        ]
        groups[key] = [workspace_asset_response(asset) for asset in items]
    return {"collections": groups, "count": sum(len(items) for items in groups.values())}


def _public_reference_summary(summary: dict) -> dict:
    payload = dict(summary)
    payload.pop("storage_path", None)
    return payload


@app.get("/api/assets/{asset_id}/references")
async def get_asset_references(asset_id: str):
    try:
        return _public_reference_summary(
            LEDGER.asset_reference_summary(
                asset_id, retention_days=TRASH_RETENTION_DAYS
            )
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "ASSET_NOT_FOUND", "message": str(exc)},
        )


@app.delete("/api/trash/assets/{asset_id}")
async def purge_asset_from_trash(asset_id: str, confirm_asset_id: str):
    if str(confirm_asset_id).strip() != asset_id:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "PURGE_CONFIRMATION_MISMATCH",
                "message": "confirm_asset_id must exactly match the requested asset",
            },
        )
    try:
        result = await run_in_threadpool(
            ASSET_STORE.purge_asset,
            asset_id,
            retention_days=TRASH_RETENTION_DAYS,
        )
        return _public_reference_summary(result)
    except AssetPurgeBlockedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ASSET_PURGE_BLOCKED",
                "message": str(exc),
                "summary": _public_reference_summary(exc.summary),
            },
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "ASSET_NOT_FOUND", "message": str(exc)},
        )
    except AssetStoreError as exc:
        raise_asset_http_error(exc)


def _workspace_recent_results(jobs: list[dict], limit: int = 20) -> list[dict]:
    results = []
    seen = set()
    for job in jobs:
        for item in job.get("items", []):
            for asset_id in item.get("result_asset_ids", []):
                if asset_id in seen:
                    continue
                seen.add(asset_id)
                try:
                    asset = LEDGER.get_asset(asset_id)
                except KeyError:
                    continue
                results.append(result_asset_response(asset))
                if len(results) >= limit:
                    return results
    return results


def _active_memory_engine() -> MemoryEngine:
    return MEMORY if MEMORY.ledger is LEDGER else MemoryEngine(LEDGER)


def _review_feedback_id(review_id: str) -> str:
    return idempotent_id("fb", f"result-review:{review_id}")


def _enrich_result_reviews(reviews: list[dict]) -> list[dict]:
    if not reviews:
        return []
    feedback_rows = LEDGER.list_feedback(limit=2000)
    feedback_by_id = {str(item["id"]): item for item in feedback_rows}
    suggestions = []
    for status in ("pending", "approved", "rejected", "dismissed"):
        suggestions.extend(LEDGER.list_memory_suggestions(status=status, limit=200))
    adjustment_jobs = {}
    if any(str(review.get("learning_action") or "") == "regenerate" for review in reviews):
        for job in LEDGER.list_jobs(limit=500):
            adjustment = (
                job.get("parameters", {}).get("adjustment")
                if isinstance(job.get("parameters"), dict)
                else None
            )
            if isinstance(adjustment, dict) and adjustment.get("review_id"):
                adjustment_jobs[str(adjustment["review_id"])] = job
    memory = _active_memory_engine()
    enriched = []
    for raw in reviews:
        review = dict(raw)
        action = str(review.get("learning_action") or "none")
        feedback = feedback_by_id.get(_review_feedback_id(str(review.get("id") or "")))
        if action in {"record", "suggest"}:
            receipt = memory.learning_receipt(
                feedback,
                feedback_rows=feedback_rows,
                suggestions=suggestions,
            )
            if feedback is None:
                receipt = {**receipt, "status": "evidence_missing", "next_action": "retry"}
        elif action == "regenerate":
            derived_job = adjustment_jobs.get(str(review.get("id") or ""))
            derived_status = str((derived_job or {}).get("status") or "")
            receipt_status = {
                "queued": "adjustment_queued",
                "running": "adjustment_running",
                "paused": "adjustment_queued",
                "canceling": "adjustment_running",
                "interrupted": "adjustment_failed",
                "completed": "adjustment_completed",
                "partial": "adjustment_partial",
                "failed": "adjustment_failed",
                "canceled": "adjustment_failed",
            }.get(derived_status, "regenerate_deferred")
            receipt = {
                "status": receipt_status,
                "extracted_rule": False,
                "independent_sessions": 0,
                "threshold": 0,
                "suggestion_id": "",
                "suggestion_status": "",
                "next_action": "open_derived_job" if derived_job else "retry",
            }
            review["derived_job_id"] = str((derived_job or {}).get("id") or "")
            review["derived_job_status"] = derived_status
        else:
            receipt = memory.learning_receipt(None)
        review["feedback_id"] = str(feedback.get("id") or "") if feedback else ""
        review["learning_receipt"] = receipt
        review["suggestion_id"] = str(receipt.get("suggestion_id") or "")
        enriched.append(review)
    return enriched


def _enrich_memory_suggestions(suggestions: list[dict]) -> list[dict]:
    """Attach result-level evidence cursors without exposing local file paths."""
    if not suggestions:
        return []
    feedback_by_id = {
        str(item.get("id") or ""): item for item in LEDGER.list_feedback(limit=2000)
    }
    jobs: dict[str, dict | None] = {}
    enriched = []
    for raw in suggestions:
        suggestion = dict(raw)
        source_results = []
        seen = set()
        for evidence in suggestion.get("evidence") or []:
            if not isinstance(evidence, dict):
                continue
            feedback_id = str(evidence.get("feedback_id") or "")
            feedback = feedback_by_id.get(feedback_id) or {}
            structured = (
                feedback.get("structured")
                if isinstance(feedback.get("structured"), dict)
                else {}
            )
            job_id = str(evidence.get("job_id") or structured.get("job_id") or "")
            result_asset_id = str(
                evidence.get("result_asset_id")
                or structured.get("result_asset_id")
                or feedback.get("asset_id")
                or ""
            )
            if not job_id or not result_asset_id:
                continue
            key = (job_id, result_asset_id, feedback_id)
            if key in seen:
                continue
            seen.add(key)
            if job_id not in jobs:
                try:
                    jobs[job_id] = LEDGER.get_job(job_id, include_attempts=False)
                except KeyError:
                    jobs[job_id] = None
            job = jobs[job_id]
            if job is None:
                continue
            source_results.append({
                "feedback_id": feedback_id,
                "review_id": str(
                    evidence.get("review_id") or structured.get("review_id") or ""
                ),
                "session_id": str(
                    evidence.get("session_id")
                    or feedback.get("session_id")
                    or job.get("session_id")
                    or ""
                ),
                "job_id": job_id,
                "generation_id": str(
                    evidence.get("generation_id")
                    or feedback.get("generation_id")
                    or ""
                ),
                "result_asset_id": result_asset_id,
                "mode": str(evidence.get("mode") or job.get("mode") or ""),
                "job_title": str(job.get("title") or job.get("mode") or ""),
                "signal": str(evidence.get("signal") or feedback.get("signal") or ""),
                "reason": str(evidence.get("reason") or feedback.get("reason") or ""),
                "created_at": str(
                    evidence.get("created_at") or feedback.get("created_at") or ""
                ),
            })
        suggestion["source_results"] = source_results
        enriched.append(suggestion)
    return enriched


@app.get("/api/workspaces/{mode}")
async def get_workflow_workspace(mode: str, asset_limit: int = 200, job_limit: int = 20):
    try:
        draft = LEDGER.get_workflow_draft(mode)
        assets = LEDGER.list_collection_assets(
            draft["collection_key"], limit=asset_limit
        )
        jobs = LEDGER.list_jobs(job_limit, mode=mode)
        recent_reviews = []
        for job in jobs:
            recent_reviews.extend(LEDGER.list_result_reviews(job["id"], limit=20))
        return {
            "mode": mode,
            "collection": draft["collection_key"],
            "draft": draft,
            "assets": [workspace_asset_response(asset) for asset in assets],
            "asset_total": LEDGER.count_collection_assets(draft["collection_key"]),
            "jobs": jobs,
            "active_jobs": [
                job for job in jobs
                if job.get("status") not in {"completed", "failed", "canceled"}
            ],
            "recent_results": _workspace_recent_results(jobs),
            "recent_reviews": _enrich_result_reviews(recent_reviews[:50]),
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_WORKFLOW", "message": str(exc)},
        )


@app.put("/api/workspaces/{mode}/draft")
async def save_workflow_workspace_draft(mode: str, request: WorkflowDraftRequest):
    try:
        draft = LEDGER.save_workflow_draft(
            mode,
            expected_revision=request.expected_revision,
            selected_asset_ids=request.selected_asset_ids,
            brief=request.brief,
            intent=request.intent,
            parameters=request.parameters,
            active_job_id=request.active_job_id,
            current_generation_id=request.current_generation_id,
            current_result_asset_id=request.current_result_asset_id,
            compare_state=request.compare_state,
            ui_state=request.ui_state,
            mask_state=request.mask_state,
        )
        return {"draft": draft}
    except DraftRevisionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DRAFT_REVISION_CONFLICT",
                "message": str(exc),
                "current": LEDGER.get_workflow_draft(mode),
            },
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "DRAFT_REFERENCE_NOT_FOUND", "message": str(exc)},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_DRAFT", "message": str(exc)},
        )


@app.post("/api/workspaces/{mode}/complete")
async def complete_workflow_workspace(mode: str, request: WorkflowCompletionRequest):
    try:
        return LEDGER.complete_workflow(
            mode,
            expected_revision=request.expected_revision,
            client_request_id=request.client_request_id,
            job_id=request.job_id,
            result_asset_id=request.result_asset_id,
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "IDEMPOTENCY_CONFLICT", "message": str(exc)},
        )
    except DraftRevisionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DRAFT_REVISION_CONFLICT",
                "message": str(exc),
                "current": LEDGER.get_workflow_draft(mode),
            },
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "COMPLETION_REFERENCE_NOT_FOUND", "message": str(exc)},
        )
    except (sqlite3.IntegrityError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_COMPLETION", "message": str(exc)},
        )


class SemanticCutoutRequest(BaseModel):
    asset_id: str
    query: str
    model_query: str = ""
    target_count: int = 1
    regions: list[dict[str, Any]] = Field(default_factory=list)
    mask_edits: list[dict[str, Any]] = Field(default_factory=list)

    class Config:
        extra = "forbid"


def _semantic_cutout_source(asset_id: str) -> tuple[dict[str, Any], Path]:
    try:
        return ASSET_STORE.resolve_asset_path(str(asset_id or "").strip())
    except AssetStoreError as exc:
        raise_asset_http_error(exc)


def _semantic_cutout_error(exc: SemanticCutoutError) -> None:
    raise HTTPException(
        status_code=400,
        detail={"code": exc.code, "stage": exc.stage, "message": exc.message},
    )


@app.post("/api/semantic-cutout/preview")
async def preview_semantic_cutout(request: SemanticCutoutRequest):
    asset, path = _semantic_cutout_source(request.asset_id)
    try:
        normalized = normalize_cutout_selection({
            "strategy": "semantic",
            "query": request.query,
            "target_count": request.target_count,
            "sources": {},
        })
        regions = normalize_regions(request.regions, normalized["query"]) if request.regions else []
    except SemanticCutoutError as exc:
        _semantic_cutout_error(exc)
    width = int(asset.get("width") or 0)
    height = int(asset.get("height") or 0)
    if width <= 0 or height <= 0:
        try:
            with Image.open(path) as source:
                width, height = source.size
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "INVALID_SOURCE_IMAGE",
                    "stage": "recognition",
                    "message": "源图片无法读取，请重新导入",
                },
            ) from exc
    grounding = {
        "status": "manual_regions",
        "adapter_id": "manual-box",
        "available": False,
        "attempted": False,
        "candidates": [],
        "review_candidates": [],
        "confidence_threshold": 0.0,
        "elapsed_ms": 0.0,
        "reason": "user_regions",
        "message": "请检查框选目标后确认",
    }
    query_mapping = resolve_semantic_query(normalized["query"], request.model_query)
    if not regions:
        if query_mapping["mapped"]:
            grounding = await run_in_threadpool(
                ground_semantic_candidates,
                path,
                query_mapping["model_query"],
                normalized["target_count"],
                adapter=_configured_grounding_adapter(),
            )
            grounding = dict(grounding)
            grounding["message"] = (
                f"{query_mapping['message']}；"
                f"{grounding.get('message') or '请检查候选后确认'}"
            )
        else:
            grounding = {
                "status": "query_unmapped",
                "adapter_id": "query-mapping",
                "available": False,
                "attempted": False,
                "candidates": [],
                "review_candidates": [],
                "confidence_threshold": 0.0,
                "elapsed_ms": 0.0,
                "reason": query_mapping["status"],
                "message": query_mapping["message"],
            }
        regions = list(grounding.get("candidates") or [])
    suggested_regions = list(grounding.get("review_candidates") or [])
    candidate_status = str(grounding.get("status") or "unavailable")
    needs_confirmation = bool(regions)
    needs_review = bool(suggested_regions)
    return {
        "preview": {
            "status": (
                "needs_confirmation"
                if needs_confirmation
                else "needs_review"
                if needs_review
                else "needs_manual_grounding"
            ),
            "source_asset_id": str(asset["id"]),
            "query": normalized["query"],
            "model_query": query_mapping["model_query"],
            "query_mapping": query_mapping,
            "target_count": normalized["target_count"],
            "regions": regions,
            "suggested_regions": suggested_regions,
            "source": {"width": width, "height": height},
            "automatic_grounding_available": bool(grounding.get("available")),
            "grounding": grounding,
            "fallback": "manual-box",
            "message": str(grounding.get("message") or (
                "请检查框选目标后确认"
                if needs_confirmation
                else "请在原图上框选目标"
            )),
            "requires_confirmation": True,
            "candidate_status": candidate_status,
        }
    }


@app.post("/api/semantic-cutout/confirm")
async def confirm_semantic_cutout(request: SemanticCutoutRequest):
    asset, _path = _semantic_cutout_source(request.asset_id)
    try:
        query_mapping = resolve_semantic_query(request.query, request.model_query)
        selection = build_confirmed_selection(
            source_asset_id=str(asset["id"]),
            query=request.query,
            model_query=query_mapping["model_query"],
            target_count=request.target_count,
            regions=request.regions,
            mask_edits=request.mask_edits,
        )
    except SemanticCutoutError as exc:
        _semantic_cutout_error(exc)
    return {"selection": selection}


def _render_semantic_mask_preview(
    path: Path,
    regions: list[dict[str, Any]],
    mask_edits: list[dict[str, Any]],
) -> dict[str, Any]:
    with Image.open(path) as source:
        segmented = remove_bg_hd(source.copy())
    segmented = apply_confirmed_regions(segmented, regions)
    segmented = apply_mask_edits(segmented, mask_edits, regions)
    alpha = segmented.getchannel("A")
    alpha.thumbnail((1400, 1100), Image.Resampling.LANCZOS)
    overlay = Image.new("RGBA", alpha.size, (37, 190, 137, 0))
    overlay.putalpha(alpha)
    encoded = base64.b64encode(image_to_bytes(overlay, "PNG")).decode("ascii")
    return {
        "width": alpha.width,
        "height": alpha.height,
        "data_url": f"data:image/png;base64,{encoded}",
        "edit_count": len(mask_edits),
    }


@app.post("/api/semantic-cutout/mask-preview")
async def preview_semantic_cutout_mask(request: SemanticCutoutRequest):
    asset, path = _semantic_cutout_source(request.asset_id)
    try:
        selection = build_confirmed_selection(
            source_asset_id=str(asset["id"]),
            query=request.query,
            model_query=request.model_query,
            target_count=request.target_count,
            regions=request.regions,
            mask_edits=request.mask_edits,
        )
        source_plan = selection["sources"][str(asset["id"])]
        mask_edits = normalize_mask_edits(source_plan.get("mask_edits"))
        preview = await run_in_threadpool(
            _render_semantic_mask_preview,
            path,
            source_plan["regions"],
            mask_edits,
        )
    except SemanticCutoutError as exc:
        _semantic_cutout_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "SEMANTIC_MASK_PREVIEW_FAILED",
                "stage": "segmentation",
                "message": "本地蒙版预览失败；选区仍可确认并执行",
            },
        ) from exc
    return {"mask_preview": preview}


class JobCreateRequest(BaseModel):
    mode: str
    source_asset_ids: list[str]
    parameters: dict[str, Any] = Field(default_factory=dict)
    client_request_id: str = ""
    requested_concurrency: Optional[int] = None
    max_attempts: int = 2

    class Config:
        extra = "forbid"


class JobRetryRequest(BaseModel):
    item_ids: Optional[list[str]] = None

    class Config:
        extra = "forbid"


def _engine_key_for_mode(mode: str) -> str:
    return {
        "single": "cloud-workflow",
        "multi-file": "cloud-workflow",
        "group-split": "group-workflow",
        "cutout-batch": "local-cutout",
    }[mode]


def _default_job_concurrency(mode: str, item_count: int) -> int:
    if mode in {"single", "group-split"}:
        return 1
    return max(1, min(item_count, 4))


def _normalize_folder_delivery(value: Any) -> dict[str, Any] | None:
    if value in (None, "", {}):
        return None
    if not isinstance(value, dict):
        raise ValueError("folder_delivery must be an object")
    source_folder = _resolve_source_folder(str(value.get("source_folder", "")))
    raw_delivery = str(value.get("delivery_root", "")).strip()
    if not raw_delivery:
        raise ValueError("folder delivery path is required")
    delivery_root = Path(raw_delivery).expanduser().resolve(strict=False)
    if delivery_root.parent != source_folder:
        raise ValueError("folder delivery must stay directly inside its source folder")
    if not delivery_root.name.startswith(FOLDER_DELIVERY_PREFIX):
        raise ValueError("folder delivery name is not owned by Product Atelier")
    source_names = value.get("source_names") or {}
    if not isinstance(source_names, dict):
        raise ValueError("folder delivery source_names must be an object")
    normalized_names = {
        str(asset_id): Path(str(name)).name
        for asset_id, name in source_names.items()
        if str(asset_id).strip() and Path(str(name)).name
    }
    return {
        "batch_id": str(value.get("batch_id") or "").strip()[:120],
        "source_folder": str(source_folder),
        "delivery_root": str(delivery_root),
        "source_names": normalized_names,
        "part_index": max(1, int(value.get("part_index", 1))),
        "part_count": max(1, int(value.get("part_count", 1))),
    }


def _normalize_job_parameters(mode: str, parameters: dict) -> dict:
    normalized = dict(parameters or {})
    output_spec_explicit = "output_ratio" in normalized or "output_resolution" in normalized
    requested_output = str(normalized.get("output_root") or _RUNTIME_OUTPUT_ROOT).strip()
    output_root = _validate_output_root(requested_output, test_write=True)
    configured_roots = _configured_output_roots()
    if output_root != _RUNTIME_OUTPUT_ROOT and output_root not in configured_roots:
        raise OutputRootError(
            "OUTPUT_ROOT_NOT_CONFIGURED",
            "该交付目录不在已保存位置中，请先到设置页重新选择",
        )
    normalized["output_root"] = str(output_root)
    default_model = {
        "single": "gpt-image-2",
        "multi-file": "gpt-image-2",
        "group-split": "gemini-3.1-flash-image-preview",
        "cutout-batch": "local-rembg/birefnet-general",
    }.get(mode, "")
    if mode == "cutout-batch":
        # This workflow never invokes the model selector shown for cloud jobs.
        # Persist the model actually used by remove_bg_hd/_get_bgsession.
        normalized["model"] = default_model
        normalized["cutout_selection"] = normalize_cutout_selection(
            normalized.get("cutout_selection")
        )
    elif not str(normalized.get("model") or "").strip():
        normalized["model"] = default_model
    if mode == "cutout-batch":
        normalized.pop("output_ratio", None)
        normalized.pop("output_resolution", None)
    else:
        output_ratio = str(normalized.get("output_ratio") or "1:1").strip().lower()
        output_resolution = str(normalized.get("output_resolution") or "2k").strip().lower()
        if output_ratio not in OUTPUT_RATIO_VALUES:
            raise ValueError("output_ratio is not supported")
        if output_resolution not in OUTPUT_RESOLUTION_VALUES:
            raise ValueError("output_resolution is not supported")
        normalized["output_ratio"] = output_ratio
        normalized["output_resolution"] = output_resolution
        normalized["output_spec_explicit"] = bool(output_spec_explicit)
    folder_delivery = _normalize_folder_delivery(normalized.get("folder_delivery"))
    if folder_delivery is not None:
        if mode != "multi-file":
            raise ValueError("folder delivery is only available in multi-file mode")
        normalized["folder_delivery"] = folder_delivery
    else:
        normalized.pop("folder_delivery", None)
    return normalized


def _validate_job_request(mode: str, source_asset_ids: list[str], parameters: dict):
    if mode not in {"single", "multi-file", "group-split", "cutout-batch"}:
        raise ValueError(f"unsupported job mode: {mode}")
    if not source_asset_ids:
        raise ValueError("at least one source asset is required")
    if mode in {"single", "group-split"} and len(source_asset_ids) != 1:
        raise ValueError(f"{mode} requires exactly one source asset")
    if mode == "multi-file":
        if len(source_asset_ids) > 20:
            raise ValueError("multi-file accepts at most 20 source assets")
        variations = int(parameters.get("variations", parameters.get("batch", 1)))
        if variations < 1 or variations > 4:
            raise ValueError("variations must be between 1 and 4")
        if len(source_asset_ids) * variations > 24:
            raise ValueError("one multi-file job can plan at most 24 generated variations")
    if mode == "single":
        batch = int(parameters.get("batch", parameters.get("variations", 1)))
        if batch < 1 or batch > 4:
            raise ValueError("batch must be between 1 and 4")
    if mode == "group-split" and not isinstance(parameters.get("refine", True), bool):
        raise ValueError("group-split refine must be a boolean")
    if mode == "cutout-batch" and len(source_asset_ids) > 24:
        raise ValueError("cutout-batch accepts at most 24 source assets")
    if mode == "cutout-batch":
        validate_selection_sources(
            parameters.get("cutout_selection") or {"strategy": "foreground"},
            source_asset_ids,
        )


def _wake_job_engine():
    engine = JOB_ENGINE
    if engine is not None and engine.is_running:
        engine.wake()


@app.post("/api/jobs")
async def create_durable_job(request: JobCreateRequest):
    mode = str(request.mode).strip()
    source_asset_ids = [str(asset_id).strip() for asset_id in request.source_asset_ids]
    try:
        refresh_runtime_config()
        parameters = _normalize_job_parameters(mode, request.parameters or {})
        _validate_job_request(mode, source_asset_ids, parameters)
        requested_concurrency = (
            request.requested_concurrency
            if request.requested_concurrency is not None
            else _default_job_concurrency(mode, len(source_asset_ids))
        )
        job, created = LEDGER.create_job(
            mode,
            source_asset_ids,
            engine_key=_engine_key_for_mode(mode),
            parameters=parameters,
            idempotency_key=str(request.client_request_id or "").strip(),
            requested_concurrency=requested_concurrency,
            max_attempts=request.max_attempts,
            title={
                "single": "单产品任务",
                "multi-file": f"多文件任务 · {len(source_asset_ids)} 张",
                "group-split": "合照拆分任务",
                "cutout-batch": f"批量抠图 · {len(source_asset_ids)} 张",
            }[mode],
        )
        _wake_job_engine()
        return {"job": job, "created": created}
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "IDEMPOTENCY_CONFLICT", "message": str(exc)},
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "SOURCE_ASSET_NOT_FOUND", "message": str(exc)},
        )
    except OutputRootError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": exc.message},
        )
    except SemanticCutoutError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "stage": exc.stage, "message": exc.message},
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_JOB_REQUEST", "message": str(exc)},
        )


@app.get("/api/jobs")
async def list_durable_jobs(limit: int = 100):
    jobs = LEDGER.list_jobs(limit)
    return {"jobs": jobs, "count": len(jobs)}


@app.get("/api/jobs/runtime")
async def durable_job_runtime():
    """Expose read-only executor ownership and resource use for the task center."""
    engine = JOB_ENGINE
    if engine is None:
        return {
            "running": False,
            "leader": False,
            "in_flight": 0,
            "resource_in_use": {},
            "resource_limits": {},
            "unreconciled_workers": [],
        }
    snapshot = engine.snapshot()
    return {
        "running": bool(snapshot.get("running")),
        "leader": bool(snapshot.get("leader")),
        "in_flight": int(snapshot.get("in_flight", 0)),
        "resource_in_use": dict(snapshot.get("resource_in_use") or {}),
        "resource_limits": dict(snapshot.get("resource_limits") or {}),
        "unreconciled_workers": list(snapshot.get("unreconciled_workers") or []),
    }


@app.get("/api/jobs/{job_id}")
async def get_durable_job(job_id: str):
    try:
        return {"job": LEDGER.get_job(job_id)}
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "JOB_NOT_FOUND", "message": str(exc)},
        )


@app.get("/api/jobs/{job_id}/traces")
async def list_job_traces(job_id: str, limit: int = 200):
    try:
        LEDGER.get_job(job_id, include_attempts=False)
        traces = LEDGER.list_execution_traces(job_id, limit)
        return {"traces": traces, "count": len(traces)}
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "JOB_NOT_FOUND", "message": str(exc)},
        )


@app.post("/api/jobs/{job_id}/traces")
async def create_job_trace(job_id: str, request: ExecutionTraceRequest):
    try:
        LEDGER.get_job(job_id, include_attempts=False)
        trace = LEDGER.record_execution_trace(
            request.client_request_id,
            job_id=job_id,
            job_item_id=request.job_item_id,
            generation_id=request.generation_id,
            stage=request.stage,
            status=request.status,
            user_input=request.user_input,
            compiled_prompt=request.compiled_prompt,
            applied_knowledge=request.applied_knowledge,
            ignored_fields=request.ignored_fields,
            model=request.model,
            parameters=request.parameters,
            output=request.output,
            error_code=request.error_code,
            error_message=request.error_message,
        )
        return {"trace": trace}
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "IDEMPOTENCY_CONFLICT", "message": str(exc)},
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "TRACE_REFERENCE_NOT_FOUND", "message": str(exc)},
        )
    except (sqlite3.IntegrityError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_TRACE", "message": str(exc)},
        )


@app.get("/api/jobs/{job_id}/reviews")
async def list_job_reviews(job_id: str, limit: int = 200):
    try:
        LEDGER.get_job(job_id, include_attempts=False)
        reviews = _enrich_result_reviews(LEDGER.list_result_reviews(job_id, limit))
        return {"reviews": reviews, "count": len(reviews)}
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "JOB_NOT_FOUND", "message": str(exc)},
        )


@app.post("/api/jobs/{job_id}/reviews")
async def create_job_review(job_id: str, request: ResultReviewRequest):
    try:
        LEDGER.get_job(job_id, include_attempts=False)
        review = LEDGER.submit_result_review(
            request.client_request_id,
            job_id=job_id,
            generation_id=request.generation_id,
            result_asset_id=request.result_asset_id,
            decision=request.decision,
            reason_codes=request.reason_codes,
            note=request.note,
            learning_action=request.learning_action,
        )
        if request.learning_action in {"record", "suggest"}:
            job = LEDGER.get_job(job_id, include_attempts=False)
            signal = {
                "adopt": "adopted",
                "adjust": "adjusted",
                "reject": "rejected",
            }[request.decision]
            LEDGER.add_feedback(
                str(job["session_id"]),
                signal,
                generation_id=request.generation_id,
                asset_id=request.result_asset_id,
                reason=request.note,
                structured={
                    "review_id": review["id"],
                    "job_id": job_id,
                    "mode": job["mode"],
                    "result_asset_id": request.result_asset_id,
                    "reason_codes": request.reason_codes,
                },
                scope="result",
                feedback_id=_review_feedback_id(review["id"]),
            )
            if request.learning_action == "suggest":
                _active_memory_engine().synthesize()
        return {"review": _enrich_result_reviews([review])[0]}
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "IDEMPOTENCY_CONFLICT", "message": str(exc)},
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "RESULT_NOT_FOUND", "message": str(exc)},
        )
    except (sqlite3.IntegrityError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_REVIEW", "message": str(exc)},
        )


def _job_result_owner(job: dict, result_asset_id: str, generation_id: str = "") -> dict:
    target = str(result_asset_id or "").strip()
    requested_generation = str(generation_id or "").strip()
    for item in job.get("items") or []:
        if target not in {str(asset_id) for asset_id in item.get("result_asset_ids") or []}:
            continue
        if requested_generation and str(item.get("generation_id") or "") != requested_generation:
            raise ValueError("result does not belong to the requested generation")
        return item
    raise ValueError("result does not belong to the requested job")


@app.post("/api/jobs/{job_id}/adjustments")
async def create_result_adjustment(job_id: str, request: ResultAdjustmentRequest):
    """Create one immutable, single-pass edit derived from an existing main result."""
    try:
        refresh_runtime_config()
        request_id = str(request.client_request_id or "").strip()
        note = str(request.note or "").strip()
        if not request_id or len(request_id) > 180:
            raise ValueError("client_request_id is required and must be at most 180 characters")
        if not note:
            raise ValueError("an immediate adjustment requires a concrete instruction")
        if len(note) > 1200:
            raise ValueError("adjustment instruction is too long")

        parent_job = LEDGER.get_job(job_id, include_attempts=False)
        owner = _job_result_owner(
            parent_job,
            request.result_asset_id,
            str(request.generation_id or ""),
        )
        parent_generation_id = str(owner.get("generation_id") or "")
        if not parent_generation_id:
            raise ValueError("result generation lineage is unavailable")
        LEDGER.get_generation(parent_generation_id)
        parent_asset = LEDGER.get_asset(str(request.result_asset_id))
        if str(parent_asset.get("role") or "") != "result_main":
            raise ValueError("only a commercial main result can be adjusted immediately")
        _resolve_result_asset_path(parent_asset)

        review_request_id = f"adjust-review:{request_id}"
        review = LEDGER.submit_result_review(
            review_request_id,
            job_id=job_id,
            generation_id=parent_generation_id,
            result_asset_id=str(request.result_asset_id),
            decision="adjust",
            reason_codes=request.reason_codes or ["adjusted"],
            note=note,
            learning_action="regenerate",
        )

        snapshot_parameters = (
            parent_job.get("snapshot", {}).get("parameters")
            if isinstance(parent_job.get("snapshot"), dict)
            else None
        )
        parameters = dict(
            snapshot_parameters
            if isinstance(snapshot_parameters, dict)
            else parent_job.get("parameters") or {}
        )
        previous_adjustment = (
            parameters.get("adjustment")
            if isinstance(parameters.get("adjustment"), dict)
            else {}
        )
        try:
            version = max(2, int(previous_adjustment.get("version", 1)) + 1)
        except (TypeError, ValueError):
            version = 2
        root_job_id = str(previous_adjustment.get("root_job_id") or job_id)
        parameters.pop("folder_delivery", None)
        parameters["batch"] = 1
        parameters["variations"] = 1
        parameters["output_ratio"] = "original"
        parameters["output_resolution"] = str(
            parameters.get("output_resolution") or "2k"
        ).lower()
        parameters["adjustment"] = {
            "version": version,
            "root_job_id": root_job_id,
            "parent_job_id": job_id,
            "parent_generation_id": parent_generation_id,
            "parent_result_asset_id": str(request.result_asset_id),
            "review_id": str(review["id"]),
            "instruction": note,
        }
        mode = str(parent_job.get("mode") or "")
        parameters = _normalize_job_parameters(mode, parameters)
        source_asset_ids = [str(owner.get("source_asset_id") or "")]
        _validate_job_request(mode, source_asset_ids, parameters)
        derived_job, created = LEDGER.create_job(
            mode,
            source_asset_ids,
            engine_key=_engine_key_for_mode(mode),
            parameters=parameters,
            idempotency_key=f"adjustment:{request_id}",
            requested_concurrency=1,
            max_attempts=2,
            title=f"结果调整 · V{version}",
        )
        _wake_job_engine()
        enriched_review = _enrich_result_reviews([review])[0]
        return {
            "review": enriched_review,
            "job": derived_job,
            "created": created,
            "lineage": {
                "root_job_id": root_job_id,
                "parent_job_id": job_id,
                "parent_generation_id": parent_generation_id,
                "parent_result_asset_id": str(request.result_asset_id),
                "version": version,
            },
        }
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "IDEMPOTENCY_CONFLICT", "message": str(exc)},
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "RESULT_NOT_FOUND", "message": str(exc)},
        )
    except AssetStoreError as exc:
        raise_asset_http_error(exc)
    except OutputRootError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": exc.message},
        )
    except (sqlite3.IntegrityError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_ADJUSTMENT", "message": str(exc)},
        )


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_durable_job(job_id: str):
    try:
        engine = JOB_ENGINE
        job = (
            engine.request_cancel(job_id)
            if engine is not None and engine.is_running
            else LEDGER.request_job_cancel(job_id)
        )
        return {"job": job}
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "JOB_NOT_FOUND", "message": str(exc)},
        )


@app.post("/api/jobs/{job_id}/pause")
async def pause_durable_job(job_id: str):
    try:
        engine = JOB_ENGINE
        job = (
            engine.pause_job(job_id)
            if engine is not None and engine.is_running
            else LEDGER.pause_job(job_id)
        )
        return {"job": job}
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "JOB_NOT_FOUND", "message": str(exc)},
        )
    except InvalidStatusTransitionError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "JOB_NOT_PAUSABLE", "message": str(exc)},
        )


@app.post("/api/jobs/{job_id}/resume")
async def resume_durable_job(job_id: str):
    try:
        engine = JOB_ENGINE
        job = (
            engine.resume_job(job_id)
            if engine is not None and engine.is_running
            else LEDGER.resume_job(job_id)
        )
        return {"job": job}
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "JOB_NOT_FOUND", "message": str(exc)},
        )
    except InvalidStatusTransitionError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "JOB_NOT_RESUMABLE", "message": str(exc)},
        )


@app.post("/api/jobs/{job_id}/retry")
async def retry_durable_job(job_id: str, request: Optional[JobRetryRequest] = None):
    try:
        item_ids = request.item_ids if request is not None else None
        engine = JOB_ENGINE
        job = (
            engine.retry_failed(job_id, item_ids)
            if engine is not None and engine.is_running
            else LEDGER.retry_job_items(job_id, item_ids)
        )
        return {"job": job}
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "JOB_OR_ITEM_NOT_FOUND", "message": str(exc)},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "NO_RETRYABLE_ITEMS", "message": str(exc)},
        )


@app.get("/api/knowledge/status")
async def knowledge_status():
    refresh_runtime_config()
    return KNOWLEDGE.status()


@app.post("/api/knowledge/reload")
@app.post("/api/reload-knowledge")
async def reload_knowledge(data: dict | None = None):
    try:
        path = str((data or {}).get("path", "")).strip()
        if path:
            save_config({"knowledge_base_path": str(canonicalize_vault_path(path))})
            refresh_runtime_config()
            return KNOWLEDGE.status()
        refresh_runtime_config()
        return KNOWLEDGE.reload()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/knowledge/compile")
async def compile_knowledge(data: dict):
    try:
        refresh_runtime_config()
        context = dict(data if isinstance(data, dict) else {})
        context["approved_memory_rules"] = _approved_memory_rules(context)
        return KNOWLEDGE.compile(context)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/sessions")
async def list_creation_sessions(limit: int = 30):
    return LEDGER.list_sessions(limit)


@app.post("/api/sessions")
async def create_creation_session(data: dict):
    try:
        return LEDGER.create_session(
            str(data.get("mode", "single")),
            title=str(data.get("title", "")),
            project_name=str(data.get("project_name", "")),
            designer_profile=str(data.get("designer_profile", "default")),
            brand_profile=str(data.get("brand_profile", "")),
            category=str(data.get("category", "general")),
            brief=data.get("brief") if isinstance(data.get("brief"), dict) else {},
            intent_locks=data.get("intent_locks") if isinstance(data.get("intent_locks"), dict) else {},
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/sessions/{session_id}")
async def get_creation_session(session_id: str):
    try:
        return LEDGER.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.patch("/api/sessions/{session_id}")
async def update_creation_session(session_id: str, data: dict):
    try:
        allowed = {
            key: value for key, value in data.items()
            if key in {
                "mode", "status", "title", "project_name", "designer_profile",
                "brand_profile", "category", "brief", "intent_locks",
            }
        }
        return LEDGER.update_session(session_id, **allowed)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/sessions/{session_id}/compile")
async def compile_creation_session(session_id: str, data: dict | None = None):
    try:
        session = LEDGER.get_session(session_id, include_timeline=False)
        context = dict(session.get("brief") or {})
        context.update({
            "mode": session.get("mode", "single"),
            "category": session.get("category", "general"),
            "brand_profile": session.get("brand_profile", ""),
            "intent_locks": session.get("intent_locks") or {},
        })
        context.update(data or {})
        context["approved_memory_rules"] = _approved_memory_rules(context)
        bundle = KNOWLEDGE.compile(context)
        LEDGER.update_session(
            session_id,
            brief=bundle["creative_brief"],
            intent_locks=bundle["creative_brief"]["intent_locks"],
        )
        LEDGER.add_event(
            session_id,
            "knowledge.compiled",
            {
                "source_ids": [source.get("id", "") for source in bundle["sources"]],
                "source_count": len(bundle["sources"]),
                "conflicts": bundle["conflicts"],
            },
        )
        return bundle
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/sessions/{session_id}/events")
async def record_creation_event(session_id: str, data: dict):
    try:
        event_type = str(data.get("event_type", "")).strip()
        if not event_type:
            raise ValueError("event_type is required")
        event_id = LEDGER.add_event(
            session_id,
            event_type,
            data.get("payload") if isinstance(data.get("payload"), dict) else {},
            generation_id=data.get("generation_id") or None,
        )
        return {"id": event_id, "recorded": True}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/sessions/{session_id}/feedback")
async def record_creation_feedback(session_id: str, data: dict):
    try:
        signal = str(data.get("signal", "")).strip()
        if signal not in {"adopted", "rejected", "adjusted", "final_artwork", "note"}:
            raise ValueError("unsupported feedback signal")
        feedback = LEDGER.add_feedback(
            session_id,
            signal,
            generation_id=data.get("generation_id") or None,
            asset_id=data.get("asset_id") or None,
            reason=str(data.get("reason", "")),
            structured=data.get("structured") if isinstance(data.get("structured"), dict) else {},
            scope=str(data.get("scope", "session")),
        )
        try:
            synthesis = MEMORY.synthesize()
        except Exception as exc:
            synthesis = {"error": str(exc), "pending_suggestions": LEDGER.stats()["pending_memory"]}
        return {"feedback": feedback, "synthesis": synthesis}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/memory/suggestions")
async def list_memory_suggestions(status: str = "pending", limit: int = 50):
    return _enrich_memory_suggestions(
        LEDGER.list_memory_suggestions(status=status, limit=limit)
    )


@app.get("/api/memory/suggestions/{suggestion_id}")
async def get_memory_suggestion(suggestion_id: str):
    try:
        return _enrich_memory_suggestions(
            [LEDGER.get_memory_suggestion(suggestion_id)]
        )[0]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/memory/synthesize")
async def synthesize_memory_suggestions():
    try:
        return MEMORY.synthesize()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/memory/suggestions/{suggestion_id}/review")
async def review_memory_suggestion(suggestion_id: str, data: dict):
    try:
        return LEDGER.review_memory_suggestion(suggestion_id, str(data.get("status", "")))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.get("/api/settings")
async def get_app_settings():
    return get_settings()

@app.post("/api/settings")
async def update_settings(data: dict):
    allowed = {
        "default_model",
        "default_platter",
        "default_angle",
        "default_fidelity",
        "auto_refine",
        "knowledge_base_path",
    }
    save = {key: value for key, value in data.items() if key in allowed}
    if "api_key" in data and data["api_key"]:
        save["api_key"] = str(data["api_key"])
    try:
        if "output_root" in data:
            selected = _validate_output_root(str(data.get("output_root") or ""), test_write=True)
            existing = load_config()
            prior_roots = existing.get("known_output_roots") or []
            if not isinstance(prior_roots, list):
                prior_roots = []
            candidates = [
                *prior_roots,
                existing.get("output_root"),
                OUTPUT_DIR,
                selected,
            ]
            known_roots = _configured_output_roots({
                **existing,
                "output_root": str(selected),
                "known_output_roots": candidates,
            })
            save["output_root"] = str(selected)
            save["known_output_roots"] = [str(path) for path in known_roots]
        if "grounding_runtime_root" in data:
            raw_runtime = str(data.get("grounding_runtime_root") or "").strip()
            save["grounding_runtime_root"] = (
                str(_normalize_pack_root(raw_runtime, kind="runtime"))
                if raw_runtime
                else ""
            )
        if "grounding_model_root" in data:
            raw_model = str(data.get("grounding_model_root") or "").strip()
            save["grounding_model_root"] = (
                str(_normalize_pack_root(raw_model, kind="model"))
                if raw_model
                else ""
            )
        if save:
            save_config(save)
        refresh_runtime_config()
        return get_settings()
    except OutputRootError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": exc.message},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_GROUNDING_PACK", "message": str(exc)},
        )


@app.post("/api/grounding-pack/verify")
async def verify_grounding_pack():
    if not _RUNTIME_GROUNDING_RUNTIME_ROOT or not _RUNTIME_GROUNDING_MODEL_ROOT:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "GROUNDING_PACK_NOT_CONFIGURED",
                "message": "请先选择本地识别运行时和模型包",
            },
        )
    return await run_in_threadpool(
        probe_grounding_pack,
        _RUNTIME_GROUNDING_RUNTIME_ROOT,
        _RUNTIME_GROUNDING_MODEL_ROOT,
        GROUNDING_MODEL_MANIFEST_PATH,
    )

@app.get("/api/balance")
async def balance():
    try:
        resp = api_request("GET", "/v1/skills/balance", timeout=15)
        d = resp.get("data", resp)
        return {"balance": d.get("balance", resp.get("balance", "?")), "error": None}
    except Exception as e:
        return {"balance": None, "error": str(e)}

@app.get("/api/progress/{task_id}")
async def get_progress(task_id: str):
    try:
        job = LEDGER.get_job(task_id)
    except KeyError:
        return tracker.get(task_id)
    result_assets = []
    for item in job.get("items", []):
        for asset_id in item.get("result_asset_ids", []):
            try:
                result_assets.append(LEDGER.get_asset(asset_id))
            except KeyError:
                continue
    results = {
        "main": [result_asset_response(asset) for asset in result_assets if asset.get("role") == "result_main"],
        "cutout": [result_asset_response(asset) for asset in result_assets if asset.get("role") == "result_cutout"],
    }
    error_items = [item for item in job.get("items", []) if item.get("error_message")]
    return {
        "task_id": job["id"],
        "session_id": job["session_id"],
        "status": job["status"],
        "progress": job["progress"],
        "message": {
            "queued": "任务已排队",
            "running": "任务处理中",
            "paused": "任务已暂停领取新项",
            "canceling": "正在安全取消",
            "completed": "任务已完成",
            "partial": "部分项目已完成",
            "failed": "任务失败",
            "canceled": "任务已取消",
            "interrupted": "任务已中断",
        }.get(job["status"], job["status"]),
        "results": results,
        "error": error_items[0]["error_message"] if error_items else None,
        "job": job,
    }

def make_prompt(base, fid):
    return base + "，" + fidelity_suffix(fid)


def parse_form_object(value, fallback=None):
    if isinstance(value, dict):
        return value
    if not value:
        return dict(fallback or {})
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else dict(fallback or {})
    except Exception:
        return dict(fallback or {})


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_BATCH_FILES = 24


def validate_image_bytes(name, data):
    if not data:
        raise ValueError(f"{name or '图片'} 是空文件")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"{name or '图片'} 超过 20 MB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except Exception as exc:
        raise ValueError(f"{name or '图片'} 不是可读取的 JPG、PNG 或 WebP 图片") from exc


def safe_stem(name, fallback="image"):
    stem = Path(name or fallback).stem
    stem = re.sub(r"[^\w\-\u4e00-\u9fff]+", "_", stem, flags=re.UNICODE).strip("_")
    return (stem or fallback)[:48]


async def read_upload_batch(files, max_files=MAX_BATCH_FILES):
    if not files:
        raise HTTPException(status_code=400, detail="请至少选择一张图片")
    if len(files) > max_files:
        raise HTTPException(status_code=400, detail=f"单次最多选择 {max_files} 张图片")
    uploads = []
    total_bytes = 0
    for index, upload in enumerate(files):
        name = upload.filename or f"image-{index+1}.png"
        data = await upload.read()
        try:
            validate_image_bytes(name, data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        total_bytes += len(data)
        if total_bytes > 160 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="本批图片总大小超过 160 MB，请拆成两批")
        uploads.append((name, data))
    return uploads


def run_single_task(task_id, img_bytes, name, model_key, batch, platter, fidelity, angle, ledger_context=None):
    try:
        tracker.create_task(task_id)
        tracker.update(task_id, progress=0.02, status="processing", message="读取图片...")
        ref_img = Image.open(io.BytesIO(img_bytes))
        product_type = "food"
        if not name or not name.strip():
            log_msg(task_id, "VLM识别产品中...")
            tmp = save_temp(ref_img, "vlm")
            det = vlm_detect_products(tmp, task_id)
            if det.get("products"):
                p = det["products"][0]
                name = p.get("name","产品")
                product_type = p.get("ptype","food")
                if product_type == "packaging": platter = "remove"
                log_msg(task_id, f"VLM: {name} (类型={product_type})")

        log_msg(task_id, f"单产品开始 | 产品: {name} | 模型: {model_key} | 摆盘: {platter} | 角度: {angle} | 还原度: {fidelity}% | 数量: {batch}")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        neg = build_negative(platter)
        main_results, cut_results = [], []
        per = 0.9 / batch
        knowledge_context = {
            "mode": "single",
            "category": ledger_context.get("category", product_type) if ledger_context else product_type,
            "output_kind": "ecommerce-main",
            "platter": platter,
            "angle": angle,
            "fidelity": fidelity,
            "product_name": name,
            "brand_profile": ledger_context.get("brand_profile", "") if ledger_context else "",
            "intent_locks": ledger_context.get("intent_locks", {}) if ledger_context else {},
        }
        if ledger_context and isinstance(ledger_context.get("brief"), dict):
            knowledge_context.update(ledger_context["brief"])
            knowledge_context.update({"category": product_type, "product_name": name, "platter": platter, "angle": angle, "fidelity": fidelity})

        for i in range(batch):
            tracker.update(task_id, progress=0.05 + i*per, message=f"AI生成第{i+1}/{batch}张...")
            log_msg(task_id, f"--- 批次 {i+1}/{batch} ---")
            stage1 = KNOWLEDGE.enrich_prompt(
                make_prompt(build_single_prompt(name, platter, product_type, angle), fidelity),
                neg,
                knowledge_context,
            )
            p1 = stage1["prompt"]
            neg1 = stage1["negative_prompt"]
            ledger_record_prompt(
                ledger_context,
                p1,
                negative_prompt=neg1,
                stage="primary" if i == 0 else f"primary-variation-{i+1}",
                knowledge_refs=stage1["sources"],
            )
            img1 = ai_i2i(p1, ref_img, model_key, negative_prompt=neg1, stage=f"1-{i+1}", tid_ref=task_id)
            tracker.update(task_id, progress=0.35 + i*per + per*0.3, message=f"精修 {i+1}/{batch}...")
            stage2 = KNOWLEDGE.enrich_prompt(
                make_prompt(build_stage2_prompt(name, platter, product_type, angle), fidelity),
                neg,
                knowledge_context,
            )
            p2 = stage2["prompt"]
            neg2 = stage2["negative_prompt"]
            ledger_record_prompt(
                ledger_context,
                p2,
                negative_prompt=neg2,
                stage=f"refine-{i+1}",
                knowledge_refs=stage2["sources"],
            )
            img2 = ai_i2i(p2, img1, model_key, negative_prompt=neg2, stage=f"2-{i+1}", tid_ref=task_id)
            main_img = post_process_enhance(img2)
            mp = OUTPUT_DIR / f"product_{ts}_{i+1}_main.jpg"
            main_img.save(mp, "JPEG", quality=96)
            main_results.append({"name": mp.name, "data": base64.b64encode(image_to_bytes(main_img)).decode(), "path": str(mp)})
            log_msg(task_id, f"主图: {mp.name}")
            tracker.update(task_id, progress=0.7 + i*per + per*0.6, message=f"抠图 {i+1}/{batch}...")
            cut = remove_bg_hd(main_img)
            cut = tight_crop_alpha(cut)
            cp = OUTPUT_DIR / f"product_{ts}_{i+1}_cutout.png"
            cut.save(cp, "PNG")
            cut_results.append({"name": cp.name, "data": base64.b64encode(image_to_bytes(cut, "PNG")).decode(), "path": str(cp)})
            log_msg(task_id, f"PNG: {cp.name}")

        results = {"main": main_results, "cutout": cut_results, "product_name": name}
        ledger_complete_task(ledger_context, results)
        tracker.complete(task_id, results=results)
        log_msg(task_id, "=== 完成! ===")
    except Exception as e:
        traceback.print_exc()
        tracker.complete(task_id, error=str(e))
        ledger_fail_task(ledger_context, e)

def run_multi_task(task_id, img_bytes, model_key, platter_default, do_refine, fidelity, angle, ledger_context=None):
    try:
        tracker.create_task(task_id)
        tracker.update(task_id, progress=0.02, message="读取图片...")
        img = Image.open(io.BytesIO(img_bytes))
        w, h = img.size
        tracker.update(task_id, progress=0.04, message="VLM识别产品...")
        tmp = save_temp(img, "vlm_m")
        det = vlm_detect_products(tmp, task_id)
        products = det.get("products", [])
        count = len(products)
        log_msg(task_id, f"检测到 {count} 个产品")
        if count == 0:
            error = "未检测到产品"
            tracker.complete(task_id, error=error)
            ledger_fail_task(ledger_context, error)
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        batch_dir = OUTPUT_DIR / "multi-products" / ts
        batch_dir.mkdir(parents=True, exist_ok=True)
        main_results, cut_results = [], []
        per = 0.92 / count
        for idx, p in enumerate(products):
            pname = p.get("name", f"产品{idx+1}")
            ptype = p.get("ptype", "food")
            has_cont = p.get("has_container", False)
            cutoff = p.get("cutoff", False)
            bbn = p.get("bbox", [0,0,1000,1000])
            bbox = (int(bbn[0]/1000*w), int(bbn[1]/1000*h), int(bbn[2]/1000*w), int(bbn[3]/1000*h))
            pmode = "remove" if ptype=="packaging" else ("keep" if (platter_default=="keep" or (platter_default=="auto" and has_cont)) else "remove")
            pad = 0.20 if cutoff else 0.12
            cropped = crop_product(img, bbox, w, h, pad_pct=pad)
            safe = pname.replace("/","_").replace("\\","_").replace(":","_")[:20]
            neg = build_negative(pmode)
            knowledge_context = {
                "mode": "group-split",
                "category": ptype,
                "output_kind": "ecommerce-main",
                "platter": pmode,
                "angle": angle,
                "fidelity": fidelity,
                "product_name": pname,
                "brand_profile": ledger_context.get("brand_profile", "") if ledger_context else "",
                "intent_locks": ledger_context.get("intent_locks", {}) if ledger_context else {},
            }
            if ledger_context and isinstance(ledger_context.get("brief"), dict):
                knowledge_context.update(ledger_context["brief"])
                knowledge_context.update({"category": ptype, "product_name": pname, "platter": pmode, "angle": angle, "fidelity": fidelity})
            log_msg(task_id, f"--- [{idx+1}/{count}] {pname} | {ptype} | 器皿={has_cont} | 裁切={cutoff} | 摆盘={pmode} ---")
            tracker.update(task_id, progress=0.06 + idx*per, message=f"AI处理 {pname} ({idx+1}/{count})...")
            stage1 = KNOWLEDGE.enrich_prompt(
                make_prompt(build_multi_stage1_prompt(pname, ptype, "cutoff" if cutoff else "complete", pmode, angle), fidelity),
                neg,
                knowledge_context,
            )
            p1 = stage1["prompt"]
            neg1 = stage1["negative_prompt"]
            ledger_record_prompt(
                ledger_context,
                p1,
                negative_prompt=neg1,
                stage="primary" if idx == 0 else f"product-{idx+1}-primary",
                knowledge_refs=stage1["sources"],
            )
            mimg = ai_i2i(p1, cropped, model_key, negative_prompt=neg1, size="2048x2048", stage=f"1-{idx+1}", tid_ref=task_id)
            if do_refine:
                stage2 = KNOWLEDGE.enrich_prompt(
                    make_prompt(build_stage2_prompt(pname, pmode, ptype, angle), fidelity),
                    neg,
                    knowledge_context,
                )
                p2 = stage2["prompt"]
                neg2 = stage2["negative_prompt"]
                ledger_record_prompt(
                    ledger_context,
                    p2,
                    negative_prompt=neg2,
                    stage=f"product-{idx+1}-refine",
                    knowledge_refs=stage2["sources"],
                )
                mimg = ai_i2i(p2, mimg, model_key, negative_prompt=neg2, size="2048x2048", stage=f"2-{idx+1}", tid_ref=task_id)
            mimg = post_process_enhance(mimg)
            mp = batch_dir / f"{idx+1:02d}_{safe}_main.jpg"
            mimg.save(mp, "JPEG", quality=96)
            main_results.append({"name": mp.name, "data": base64.b64encode(image_to_bytes(mimg)).decode(), "path": str(mp)})
            cut = remove_bg_hd(mimg)
            cut = tight_crop_alpha(cut)
            cp = batch_dir / f"{idx+1:02d}_{safe}_cutout.png"
            cut.save(cp, "PNG")
            cut_results.append({"name": cp.name, "data": base64.b64encode(image_to_bytes(cut, "PNG")).decode(), "path": str(cp)})
        results = {"main": main_results, "cutout": cut_results, "count": count}
        ledger_complete_task(ledger_context, results)
        tracker.complete(task_id, results=results)
        log_msg(task_id, f"=== 完成! 共{count}个产品 ===")
    except Exception as e:
        traceback.print_exc()
        tracker.complete(task_id, error=str(e))
        ledger_fail_task(ledger_context, e)


def run_multi_file_task(task_id, uploads, session_id, model_key, variations, platter, fidelity, angle):
    """Process independent source images as a visible queue, not as one group shot."""
    tracker.create_task(task_id)
    items = []
    all_main, all_cutout = [], []
    success = 0
    failed = 0
    total = len(uploads)
    for index, (file_name, image_bytes) in enumerate(uploads):
        child_task_id = f"{task_id}_item_{index+1}"
        tracker.update(
            task_id,
            progress=index / max(total, 1),
            status="processing",
            message=f"处理 {file_name}（{index+1}/{total}）",
            log=f"开始独立处理：{file_name}",
        )
        context = ledger_begin_task(
            "multi-file",
            child_task_id,
            file_name,
            image_bytes,
            {
                "model": model_key,
                "batch": variations,
                "platter": platter,
                "fidelity": fidelity,
                "angle": angle,
                "product_name": safe_stem(file_name, f"产品{index+1}"),
            },
            session_id=session_id,
        )
        run_single_task(
            child_task_id,
            image_bytes,
            safe_stem(file_name, f"产品{index+1}"),
            model_key,
            variations,
            platter,
            fidelity,
            angle,
            context,
        )
        child = tracker.get(child_task_id)
        if child.get("status") == "completed":
            result = child.get("results") or {}
            all_main.extend(result.get("main") or [])
            all_cutout.extend(result.get("cutout") or [])
            success += 1
            items.append({"file": file_name, "task_id": child_task_id, "status": "completed", "results": result})
        else:
            failed += 1
            items.append({"file": file_name, "task_id": child_task_id, "status": "error", "error": child.get("error", "处理失败")})
        tracker.update(task_id, progress=(index + 1) / total, message=f"已完成 {index+1}/{total}")

    final_status = "completed" if failed == 0 else ("partial" if success else "error")
    try:
        LEDGER.update_session(
            session_id,
            status=final_status,
            completed_at=datetime.now().astimezone().isoformat(timespec="milliseconds"),
        )
        LEDGER.add_event(
            session_id,
            "queue.completed",
            {"mode": "multi-file", "total": total, "success": success, "failed": failed},
        )
    except Exception as exc:
        print(f"[ledger] queue completion failed: {exc}", file=sys.stderr, flush=True)
    results = {
        "mode": "multi-file",
        "items": items,
        "main": all_main,
        "cutout": all_cutout,
        "total": total,
        "success": success,
        "failed": failed,
    }
    if success:
        tracker.complete(task_id, results=results)
    else:
        tracker.complete(task_id, error="批量任务全部失败")


def run_cutout_batch_task(task_id, uploads, session_id):
    """Run true multi-file background removal with per-file provenance."""
    tracker.create_task(task_id)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    batch_dir = OUTPUT_DIR / "cutout-batch" / ts
    batch_dir.mkdir(parents=True, exist_ok=True)
    results = []
    failures = []
    total = len(uploads)
    for index, (file_name, image_bytes) in enumerate(uploads):
        child_task_id = f"{task_id}_item_{index+1}"
        tracker.update(
            task_id,
            progress=index / max(total, 1),
            status="processing",
            message=f"抠图 {file_name}（{index+1}/{total}）",
            log=f"开始抠图：{file_name}",
        )
        context = ledger_begin_task(
            "cutout-batch",
            child_task_id,
            file_name,
            image_bytes,
            {"model": "local-rembg", "operation": "background-removal"},
            session_id=session_id,
        )
        try:
            image = Image.open(io.BytesIO(image_bytes))
            cutout = tight_crop_alpha(remove_bg_hd(image))
            path = batch_dir / f"{index+1:02d}_{safe_stem(file_name)}_cutout.png"
            cutout.save(path, "PNG")
            item = {
                "source_name": file_name,
                "name": path.name,
                "path": str(path),
                "data": base64.b64encode(image_to_bytes(cutout, "PNG")).decode(),
            }
            results.append(item)
            ledger_complete_task(context, {"main": [], "cutout": [item]})
        except Exception as exc:
            failures.append({"source_name": file_name, "error": str(exc)})
            ledger_fail_task(context, exc)
        tracker.update(task_id, progress=(index + 1) / total, message=f"已完成 {index+1}/{total}")

    final_status = "completed" if not failures else ("partial" if results else "error")
    try:
        LEDGER.update_session(
            session_id,
            status=final_status,
            completed_at=datetime.now().astimezone().isoformat(timespec="milliseconds"),
        )
        LEDGER.add_event(
            session_id,
            "queue.completed",
            {"mode": "cutout-batch", "total": total, "success": len(results), "failed": len(failures)},
        )
    except Exception as exc:
        print(f"[ledger] cutout queue completion failed: {exc}", file=sys.stderr, flush=True)
    payload = {
        "mode": "cutout-batch",
        "main": [],
        "cutout": results,
        "failures": failures,
        "total": total,
        "success": len(results),
        "failed": len(failures),
    }
    if results:
        tracker.complete(task_id, results=payload)
    else:
        tracker.complete(task_id, error="批量抠图全部失败")


# ======================== DURABLE JOB WORKFLOWS ========================
def _job_trace_context(ctx):
    session = LEDGER.get_session(str(ctx.job["session_id"]), include_timeline=False)
    snapshot = dict(ctx.job.get("snapshot") or {})
    parameters = dict(ctx.job.get("parameters") or {})
    brief = snapshot.get("brief") if isinstance(snapshot.get("brief"), dict) else {}
    if not brief and isinstance(parameters.get("brief"), dict):
        brief = dict(parameters["brief"])
    intent = snapshot.get("intent") if isinstance(snapshot.get("intent"), dict) else {}
    if not intent and isinstance(parameters.get("intent_locks"), dict):
        intent = dict(parameters["intent_locks"])
    return {
        "job_id": str(ctx.job_id),
        "job_item_id": str(ctx.item_id),
        "attempt_count": max(1, int(ctx.item.get("attempt_count", 1))),
        "session_id": str(ctx.job["session_id"]),
        "generation_id": str(ctx.item.get("generation_id") or ""),
        "source_asset_id": str(ctx.item["source_asset_id"]),
        "brief": brief or session.get("brief", {}),
        "intent_locks": intent or session.get("intent_locks", {}),
        "category": session.get("category", "general"),
        "brand_profile": session.get("brand_profile", ""),
        "model": str(parameters.get("model") or ""),
        "parameters": parameters,
    }


def _record_execution_trace_safe(
    trace,
    stage,
    status,
    *,
    compiled_prompt="",
    applied_knowledge=None,
    ignored_fields=None,
    parameters=None,
    output=None,
    error_code="",
    error_message="",
):
    """Write execution evidence without making observability break the task."""
    try:
        request_id = (
            f"{trace['job_id']}:{trace['job_item_id']}:"
            f"{trace['attempt_count']}:{stage}"
        )
        return LEDGER.record_execution_trace(
            request_id,
            job_id=trace["job_id"],
            job_item_id=trace["job_item_id"],
            generation_id=trace["generation_id"],
            stage=stage,
            status=status,
            user_input={
                "brief": trace.get("brief") or {},
                "intent_locks": trace.get("intent_locks") or {},
            },
            compiled_prompt=compiled_prompt,
            applied_knowledge=applied_knowledge or [],
            ignored_fields=ignored_fields or [],
            model=trace.get("model", ""),
            parameters=parameters if parameters is not None else trace.get("parameters", {}),
            output=output or {},
            error_code=error_code,
            error_message=error_message,
        )
    except Exception as exc:
        print(f"[ledger] execution trace failed: {exc}", file=sys.stderr, flush=True)
        return None


def _record_job_prompt(
    trace,
    prompt,
    negative_prompt,
    stage,
    knowledge_bundle,
    *,
    base_prompt="",
):
    knowledge_refs = list((knowledge_bundle or {}).get("sources") or [])
    applied_evidence = KnowledgeCompiler.execution_evidence(knowledge_bundle)
    base_prompt = str(base_prompt or prompt)
    snapshot = prompt_snapshot(
        base_prompt=base_prompt,
        compiled_prompt=prompt,
        negative_prompt=negative_prompt,
        knowledge_evidence=applied_evidence,
    )
    generation_id = trace["generation_id"]
    changes = {"status": "running", "prompt_version": PROMPT_COMPILER_VERSION}
    if stage == "primary":
        changes.update({
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "knowledge_refs": knowledge_refs or [],
        })
    LEDGER.update_generation(generation_id, **changes)
    LEDGER.add_event(
        trace["session_id"],
        "prompt.compiled",
        {
            "stage": stage,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "knowledge_refs": knowledge_refs or [],
        },
        generation_id=generation_id,
    )
    _record_execution_trace_safe(
        trace,
        f"prompt.{stage}",
        "completed",
        compiled_prompt=prompt,
        applied_knowledge=applied_evidence,
        parameters={
            **dict(trace.get("parameters") or {}),
            "trace_contract_version": GENERATION_TRACE_CONTRACT_VERSION,
            "prompt_version": PROMPT_COMPILER_VERSION,
            "base_prompt": base_prompt,
            "negative_prompt": negative_prompt,
        },
        output={"prompt_stage": stage, "prompt_snapshot": snapshot},
    )


def _job_output_root(ctx, *, test_write: bool = True) -> Path:
    parameters = dict(ctx.job.get("parameters") or {})
    # Jobs created before custom delivery roots existed retain the legacy path.
    raw_root = str(parameters.get("output_root") or OUTPUT_DIR).strip()
    try:
        return _validate_output_root(raw_root, test_write=test_write)
    except OutputRootError as exc:
        raise JobExecutionError(exc.code, exc.message) from exc


def _attempt_directory(ctx):
    job_part = safe_stem(str(ctx.job_id), "job")
    item_part = safe_stem(str(ctx.item_id), "item")
    attempt = max(1, int(ctx.item.get("attempt_count", 1)))
    directory = OUTPUT_DIR / "_attempts" / job_part / item_part / f"attempt-{attempt}-{uuid.uuid4().hex}"
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def _stage_output(image, directory, name, role):
    path = directory / name
    if role == "result_cutout":
        image.save(path, "PNG")
        mime = "image/png"
    else:
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.save(path, "JPEG", quality=96)
        mime = "image/jpeg"
    return {
        "temp_path": path,
        "name": name,
        "role": role,
        "mime": mime,
        "width": image.width,
        "height": image.height,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _publish_folder_delivery(ctx, source_asset, published_outputs):
    delivery = dict((ctx.job.get("parameters") or {}).get("folder_delivery") or {})
    if not delivery:
        return [], None
    delivery_root = Path(str(delivery["delivery_root"])).resolve(strict=False)
    source_folder = Path(str(delivery["source_folder"])).resolve(strict=True)
    if delivery_root.parent != source_folder or not delivery_root.name.startswith(FOLDER_DELIVERY_PREFIX):
        raise JobExecutionError("INVALID_DELIVERY_PATH", "Folder delivery path failed its safety check")

    source_id = str(ctx.item["source_asset_id"])
    source_name = str((delivery.get("source_names") or {}).get(source_id) or source_asset.get("name") or "image")
    source_stem = safe_stem(Path(source_name).stem, "image")
    part_index = max(1, int(delivery.get("part_index", 1)))
    item_index = max(1, int(ctx.item.get("position", 0)) + 1)
    role_counts = {}
    delivered = []
    for output in published_outputs:
        role = str(output.get("role") or "result_main")
        role_counts[role] = role_counts.get(role, 0) + 1
        if role == "result_cutout":
            category = "02_透明PNG"
            label = "透明图"
            suffix = ".png"
        elif role == "result_main":
            category = "01_商业主图"
            label = "主图"
            suffix = ".jpg"
        else:
            category = "03_其他结果"
            label = "结果"
            suffix = Path(str(output.get("name") or "")).suffix.lower() or ".bin"
        category_dir = delivery_root / category
        category_dir.mkdir(parents=True, exist_ok=True)
        target_name = (
            f"{source_stem}__P{part_index:02d}-I{item_index:03d}"
            f"-{label}{role_counts[role]:02d}{suffix}"
        )
        target = category_dir / target_name
        temp = category_dir / f".{target_name}.{uuid.uuid4().hex}.tmp"
        try:
            shutil.copy2(Path(str(output["path"])), temp)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        delivered.append({
            "role": role,
            "path": str(target),
            "relative_path": str(target.relative_to(delivery_root)),
        })
    return delivered, delivery


def _update_folder_delivery_manifest(ctx, source_asset, delivered, delivery):
    if not delivered or not delivery:
        return ""
    delivery_root = Path(str(delivery["delivery_root"])).resolve(strict=False)
    manifest_path = delivery_root / "处理记录.json"
    with _FOLDER_DELIVERY_LOCK:
        manifest = {
            "format": "product-atelier-folder-delivery-v1",
            "batch_id": str(delivery.get("batch_id") or ""),
            "source_folder": str(delivery.get("source_folder") or ""),
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "items": {},
        }
        if manifest_path.exists():
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    manifest.update(existing)
                    manifest["items"] = dict(existing.get("items") or {})
            except (OSError, ValueError, TypeError):
                pass
        manifest["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        manifest["items"][str(ctx.item_id)] = {
            "job_id": str(ctx.job_id),
            "source_asset_id": str(ctx.item["source_asset_id"]),
            "source_name": str(source_asset.get("name") or ""),
            "status": "completed",
            "files": delivered,
        }
        temp_path = delivery_root / f".{manifest_path.name}.{uuid.uuid4().hex}.tmp"
        try:
            temp_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp_path, manifest_path)
        finally:
            temp_path.unlink(missing_ok=True)
    return str(manifest_path)


def _staged_job_result(ctx, trace, stage_dir, outputs, metadata, source_asset, output_root):
    target_dir = job_delivery_directory(
        output_root,
        created_at=str(ctx.job.get("created_at") or ""),
        mode=str(ctx.job.get("mode") or ""),
        job_id=str(ctx.job_id),
        item_id=str(ctx.item_id),
        item_position=int(ctx.item.get("position", 0)),
        attempt=max(1, int(ctx.item.get("attempt_count", 1))),
    )
    metadata = {**dict(metadata), "output_root": str(output_root)}
    moved_paths = []
    delivered_paths = []
    committed_asset_ids = []
    durable_committed = False

    def cleanup():
        if durable_committed:
            # Result rows and the item/attempt/job terminal state were committed
            # together. No later read or housekeeping failure may roll back that
            # already-published user result outside the owning transaction.
            return
        if committed_asset_ids:
            LEDGER.discard_generation_results(
                trace["generation_id"],
                committed_asset_ids,
                reason="job_attempt_canceled_or_failed",
            )
            committed_asset_ids.clear()
        for path in list(moved_paths):
            Path(path).unlink(missing_ok=True)
        moved_paths.clear()
        for path in list(delivered_paths):
            Path(path).unlink(missing_ok=True)
        delivered_paths.clear()
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
        if target_dir.exists() and not any(target_dir.iterdir()):
            target_dir.rmdir()

    def commit():
        nonlocal durable_committed
        publish_started = time.perf_counter()
        published = []
        try:
            try:
                _job_output_root(ctx, test_write=True)
                target_dir.mkdir(parents=True, exist_ok=False)
            except (OSError, OutputRootError) as exc:
                raise JobExecutionError(
                    "OUTPUT_ROOT_WRITE_FAILED",
                    "成品交付目录当前无法写入，请恢复磁盘连接或重新选择目录",
                ) from exc
            for output in outputs:
                target = target_dir / str(output["name"])
                try:
                    publish_staged_file(output["temp_path"], target)
                except OSError as exc:
                    raise JobExecutionError(
                        "OUTPUT_ROOT_WRITE_FAILED",
                        "成品交付目录当前无法写入，请恢复磁盘连接或重新选择目录",
                    ) from exc
                moved_paths.append(target)
                published.append({
                    key: value for key, value in output.items() if key != "temp_path"
                } | {
                    "path": str(target),
                    "metadata": {
                        **(output.get("metadata") if isinstance(output.get("metadata"), dict) else {}),
                        "output_root": str(output_root),
                    },
                })
            # Remove the now-empty private stage before the durable transaction.
            # Once the transaction returns, there must be no fallible cleanup
            # step capable of turning a completed item into a result-less one.
            if stage_dir.exists():
                shutil.rmtree(stage_dir)
            delivered, delivery = _publish_folder_delivery(ctx, source_asset, published)
            delivered_paths.extend(entry["path"] for entry in delivered)
            asset_ids = LEDGER.commit_generation_results(
                trace["generation_id"],
                trace["source_asset_id"],
                published,
                job_item_id=str(ctx.item_id),
                attempt_metadata={**dict(ctx.metadata), **dict(metadata)},
            )
            durable_committed = True
            committed_asset_ids.extend(asset_ids)
            manifest_path = ""
            try:
                manifest_path = _update_folder_delivery_manifest(
                    ctx, source_asset, delivered, delivery
                )
            except Exception as exc:
                print(f"[folder-delivery] manifest update failed: {exc}", file=sys.stderr, flush=True)
            _record_execution_trace_safe(
                trace,
                "result.publish",
                "completed",
                output={
                    "elapsed_ms": round((time.perf_counter() - publish_started) * 1000, 3),
                    "result_asset_ids": list(committed_asset_ids),
                    "output_count": len(committed_asset_ids),
                    "output_root": str(output_root),
                    "delivery_files": [entry["relative_path"] for entry in delivered],
                    "actual_files": [
                        {
                            "name": entry.get("name"),
                            "role": entry.get("role"),
                            "width": entry.get("width"),
                            "height": entry.get("height"),
                        }
                        for entry in published
                    ],
                },
            )
            workflow_started = trace.get("workflow_started_perf")
            workflow_elapsed_ms = (
                round((time.perf_counter() - workflow_started) * 1000, 3)
                if isinstance(workflow_started, (int, float))
                else None
            )
            _record_execution_trace_safe(
                trace,
                "workflow.complete",
                "completed",
                output={
                    "elapsed_ms": workflow_elapsed_ms,
                    "output_count": len(committed_asset_ids),
                    "mode": str(ctx.job.get("mode") or ""),
                },
            )
            return {
                "result_asset_ids": list(committed_asset_ids),
                "output_count": len(committed_asset_ids),
                "output_root": str(output_root),
                "delivery_root": str(delivery.get("delivery_root") or "") if delivery else "",
                "delivery_files": [entry["relative_path"] for entry in delivered],
                "delivery_manifest": manifest_path,
            }
        except Exception:
            cleanup()
            raise

    return JobProcessorResult(
        metadata=metadata,
        commit=commit,
        cleanup=cleanup,
        durable_completion=True,
    )


def _output_measurement(image: Image.Image, output_spec: dict[str, Any]) -> dict[str, Any]:
    width, height = image.size
    actual_ratio = width / max(1, height)
    expected_ratio = max(float(output_spec.get("effective_ratio_value") or 1.0), 1e-9)
    ratio_error = abs(math.log(max(actual_ratio, 1e-9) / expected_ratio))
    return {
        "actual_width": width,
        "actual_height": height,
        "actual_ratio": _ratio_name_from_dimensions(width, height),
        "actual_megapixels": round((width * height) / 1_000_000, 3),
        "effective_ratio": output_spec.get("effective_ratio"),
        "requested_resolution": output_spec.get("requested_resolution"),
        "aspect_matches": ratio_error <= math.log(1.04),
        "aspect_error_percent": round((math.exp(ratio_error) - 1) * 100, 2),
    }


def _cloud_job_call(
    ctx,
    prompt,
    reference,
    model,
    *,
    negative_prompt,
    stage,
    output_spec,
    trace,
):
    remote_tasks = list(dict(ctx.metadata).get("remote_tasks", []))
    trace_stage = f"provider.image.{stage}"
    provider_evidence = {}
    outer_started = time.perf_counter()
    provider_parameters = {
        "model": model,
        "requested_ratio": output_spec.get("requested_ratio"),
        "effective_ratio": output_spec.get("effective_ratio"),
        "requested_resolution": output_spec.get("requested_resolution"),
        "provider_params": output_spec.get("provider_params"),
        "capability_contract": capability_contract(
            model, str(output_spec.get("provider_family") or "")
        ),
    }

    def remember_remote(task_id):
        remote_tasks.append({"stage": stage, "task_id": str(task_id)})
        payload = {"remote_tasks": list(remote_tasks)}
        try:
            ctx.record_metadata(payload)
        except Exception:
            # The remote task id is still essential when cancellation raced the
            # submission response. The ledger accepts metadata while canceling.
            LEDGER.update_task_attempt_metadata(str(ctx.item_id), payload)
            raise

    def remember_evidence(evidence):
        provider_evidence.clear()
        provider_evidence.update(dict(evidence or {}))

    try:
        with ctx.resource("cloud-image"):
            generated = ai_i2i(
                prompt,
                reference,
                model,
                negative_prompt=negative_prompt,
                size=str(output_spec.get("provider_size") or "2048x2048"),
                stage=stage,
                tid_ref=str(ctx.job_id),
                on_submitted=remember_remote,
                on_evidence=remember_evidence,
                output_spec=output_spec,
            )
    except Exception as exc:
        timings = dict(provider_evidence.get("timings_ms") or {})
        elapsed_ms = timings.get("total")
        if not isinstance(elapsed_ms, (int, float)):
            elapsed_ms = round((time.perf_counter() - outer_started) * 1000, 3)
        _record_execution_trace_safe(
            trace,
            trace_stage,
            "failed",
            parameters=provider_parameters,
            output={
                "elapsed_ms": elapsed_ms,
                "timings_ms": timings,
                "reference": provider_evidence.get("reference") or {},
                "failure": provider_evidence.get("failure") or {
                    "phase": "provider.call",
                    "error_type": type(exc).__name__,
                },
                "billing": unavailable_billing_evidence(),
            },
            error_code=str(getattr(exc, "code", "PROVIDER_IMAGE_FAILED")),
            error_message=str(exc) or type(exc).__name__,
        )
        raise
    measurement = _output_measurement(generated, output_spec)
    timings = dict(provider_evidence.get("timings_ms") or {})
    elapsed_ms = timings.get("total")
    if not isinstance(elapsed_ms, (int, float)):
        elapsed_ms = round((time.perf_counter() - outer_started) * 1000, 3)
    measurement.update({
        "elapsed_ms": elapsed_ms,
        "timings_ms": timings,
        "reference": provider_evidence.get("reference") or {},
        "billing": unavailable_billing_evidence(),
    })
    if output_spec.get("strict_aspect") and not measurement["aspect_matches"]:
        _record_execution_trace_safe(
            trace,
            trace_stage,
            "failed",
            parameters=provider_parameters,
            output=measurement,
            error_code="OUTPUT_ASPECT_MISMATCH",
            error_message="供应商返回比例与本次有效规格不一致",
        )
        raise JobExecutionError(
            "OUTPUT_ASPECT_MISMATCH",
            (
                f"供应商返回 {measurement['actual_width']}×{measurement['actual_height']}，"
                f"与本次有效比例 {output_spec.get('effective_ratio')} 不一致；"
                "已停止后续精修，避免继续消耗额度"
            ),
            metadata={**measurement, "output_spec": dict(output_spec)},
        )
    _record_execution_trace_safe(
        trace,
        trace_stage,
        "completed",
        parameters=provider_parameters,
        output=measurement,
    )
    return generated


def _approved_memory_rules(context: dict | None = None) -> list[dict]:
    """Resolve user-approved memory suggestions into executable prompt rules.

    This is the bridge that makes feedback actually affect future generation:
    a suggestion only becomes an active constraint once the user has reviewed it
    and marked it ``approved``. Unreviewed or rejected suggestions never reach the
    prompt, so feedback cannot silently override formal knowledge.
    """
    try:
        suggestions = LEDGER.list_memory_suggestions(status="approved", limit=50)
    except Exception:
        return []
    scope = dict(context or {})
    category = str(scope.get("category", "general"))
    rules: list[dict] = []
    for suggestion in suggestions:
        proposal = suggestion.get("proposed_value") or {}
        directive = str(proposal.get("directive") or proposal.get("label") or "").strip()
        if not directive:
            continue
        scope_category = str(suggestion.get("category") or "general")
        if scope_category not in ("general", category):
            continue
        rules.append({
            "id": str(suggestion.get("rule_key") or suggestion.get("id") or ""),
            "label": str(proposal.get("label") or "已批准记忆反馈"),
            "text": directive,
        })
    return rules


def _job_knowledge_context(trace, **values):
    context = {
        "brand_profile": trace.get("brand_profile", ""),
        "intent_locks": trace.get("intent_locks", {}),
        **values,
    }
    if isinstance(trace.get("brief"), dict):
        context = {**trace["brief"], **context}
    context["approved_memory_rules"] = _approved_memory_rules(context)
    return context


def _run_local_stage(trace, stage, operation, *, parameters=None):
    """Time one local operation and keep its failure boundary visible."""
    started = time.perf_counter()
    try:
        value = operation()
    except Exception as exc:
        _record_execution_trace_safe(
            trace,
            stage,
            "failed",
            parameters=parameters or {},
            output={"elapsed_ms": round((time.perf_counter() - started) * 1000, 3)},
            error_code=str(getattr(exc, "code", "LOCAL_STAGE_FAILED")),
            error_message=str(exc) or type(exc).__name__,
        )
        raise
    output = {"elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}
    if isinstance(value, Image.Image):
        output.update({"width": value.width, "height": value.height, "mode": value.mode})
    elif isinstance(value, dict):
        output.update({
            key: value[key]
            for key in ("name", "role", "width", "height", "sha256")
            if key in value
        })
    _record_execution_trace_safe(
        trace,
        stage,
        "completed",
        parameters=parameters or {},
        output=output,
    )
    return value


def _execute_single_job(ctx, source_asset, image, stage_dir, trace):
    params = dict(ctx.job.get("parameters") or {})
    mode = str(ctx.job["mode"])
    model = str(params.get("model") or "gpt-image-2")
    batch = int(params.get("variations" if mode == "multi-file" else "batch", 1))
    if batch < 1 or batch > 4:
        raise JobExecutionError("INVALID_VARIATION_COUNT", "Each source supports 1 to 4 variations")
    platter = str(params.get("platter") or "auto")
    fidelity = max(0, min(int(params.get("fidelity", 40)), 100))
    angle = str(params.get("angle") or "auto")
    output_spec = resolve_output_spec(
        model,
        str(params.get("output_ratio") or "1:1"),
        str(params.get("output_resolution") or "2k"),
        image.size,
        explicit=bool(params.get("output_spec_explicit", False)),
    )
    _record_execution_trace_safe(
        trace,
        "output.spec",
        "completed",
        parameters={
            "requested_ratio": output_spec["requested_ratio"],
            "requested_resolution": output_spec["requested_resolution"],
            "source_width": output_spec["source_width"],
            "source_height": output_spec["source_height"],
        },
        output={
            "desired_ratio": output_spec["desired_ratio"],
            "effective_ratio": output_spec["effective_ratio"],
            "provider_family": output_spec["provider_family"],
            "provider_params": output_spec["provider_params"],
        },
    )
    product_name = str(params.get("product_name") or "").strip()
    product_type = "food"
    if mode == "multi-file" and not product_name:
        product_name = safe_stem(source_asset.get("name", ""), "产品")
    if not product_name:
        ctx.progress(0.03, {"phase": "vlm"})
        vlm_started = time.perf_counter()
        try:
            with ctx.resource("vlm"):
                detection = vlm_detect_products(str(source_asset["path"]), str(ctx.job_id))
        except Exception as exc:
            _record_execution_trace_safe(
                trace,
                "vlm.detect",
                "failed",
                parameters={"model": "gemini-3.5-flash", "purpose": "product-detection"},
                output={
                    "elapsed_ms": round((time.perf_counter() - vlm_started) * 1000, 3),
                    "billing": unavailable_billing_evidence(),
                },
                error_code=str(getattr(exc, "code", "VLM_DETECTION_FAILED")),
                error_message=str(exc) or type(exc).__name__,
            )
            raise
        _record_execution_trace_safe(
            trace,
            "vlm.detect",
            "completed",
            parameters={"model": "gemini-3.5-flash", "purpose": "product-detection"},
            output={
                "detected_products": len(detection.get("products") or []),
                "elapsed_ms": round((time.perf_counter() - vlm_started) * 1000, 3),
                "billing": unavailable_billing_evidence(),
            },
        )
        products = detection.get("products") or []
        if products:
            product = products[0]
            product_name = str(product.get("name") or "产品")
            product_type = str(product.get("ptype") or "food")
            if product_type == "packaging":
                platter = "remove"
        else:
            product_name = "产品"
    else:
        _record_execution_trace_safe(
            trace,
            "vlm.detect",
            "skipped",
            output={
                "reason": "product_name_supplied",
                "product_name": product_name,
                "elapsed_ms": 0.0,
                "billing": {"status": "not-applicable", "amount": 0},
            },
        )

    negative = build_negative(platter)
    outputs = []
    per = 0.92 / batch
    for index in range(batch):
        ctx.progress(0.05 + per * index, {"phase": "cloud-primary", "variation": index + 1})
        knowledge_context = _job_knowledge_context(
            trace,
            mode=mode,
            category=product_type,
            output_kind="ecommerce-main",
            platter=platter,
            angle=angle,
            fidelity=fidelity,
            product_name=product_name,
        )
        base_stage1_prompt = make_prompt(
            build_single_prompt(product_name, platter, product_type, angle), fidelity
        )
        stage1 = KNOWLEDGE.enrich_prompt(
            base_stage1_prompt, negative, knowledge_context
        )
        prompt_stage = "primary" if index == 0 else f"primary-variation-{index + 1}"
        _record_job_prompt(
            trace,
            stage1["prompt"],
            stage1["negative_prompt"],
            prompt_stage,
            stage1,
            base_prompt=base_stage1_prompt,
        )
        generated = _cloud_job_call(
            ctx,
            stage1["prompt"],
            image,
            model,
            negative_prompt=stage1["negative_prompt"],
            stage=f"1-{index + 1}",
            output_spec=output_spec,
            trace=trace,
        )
        ctx.progress(0.05 + per * index + per * 0.36, {"phase": "cloud-refine", "variation": index + 1})
        base_stage2_prompt = make_prompt(
            build_stage2_prompt(product_name, platter, product_type, angle), fidelity
        )
        stage2 = KNOWLEDGE.enrich_prompt(
            base_stage2_prompt, negative, knowledge_context
        )
        _record_job_prompt(
            trace,
            stage2["prompt"],
            stage2["negative_prompt"],
            f"refine-{index + 1}",
            stage2,
            base_prompt=base_stage2_prompt,
        )
        generated = _cloud_job_call(
            ctx,
            stage2["prompt"],
            generated,
            model,
            negative_prompt=stage2["negative_prompt"],
            stage=f"2-{index + 1}",
            output_spec=output_spec,
            trace=trace,
        )
        main_image = _run_local_stage(
            trace,
            f"local.enhance.{index + 1}",
            lambda: post_process_enhance(generated),
            parameters={"operation": "post-process-enhance"},
        )
        outputs.append(_run_local_stage(
            trace,
            f"local.save.main.{index + 1}",
            lambda: _stage_output(
                main_image, stage_dir, f"{index + 1:02d}_main.jpg", "result_main"
            ),
            parameters={"format": "jpeg", "quality": 96},
        ))
        ctx.progress(0.05 + per * index + per * 0.76, {"phase": "local-cutout", "variation": index + 1})
        with ctx.resource("local-cutout"):
            cutout = _run_local_stage(
                trace,
                f"local.cutout.{index + 1}",
                lambda: tight_crop_alpha(remove_bg_hd(main_image)),
                parameters={
                    "model": "local-rembg/birefnet-general",
                    "alpha_mode": "native-soft",
                    "post_process_mask": False,
                },
            )
        outputs.append(_run_local_stage(
            trace,
            f"local.save.cutout.{index + 1}",
            lambda: _stage_output(
                cutout, stage_dir, f"{index + 1:02d}_cutout.png", "result_cutout"
            ),
            parameters={"format": "png"},
        ))
    return outputs, {
        "product_name": product_name,
        "variation_count": batch,
        "output_spec": output_spec,
        "actual_main_dimensions": [
            {"width": output["width"], "height": output["height"]}
            for output in outputs if output["role"] == "result_main"
        ],
    }


def _execute_adjustment_job(ctx, source_asset, image, stage_dir, trace):
    params = dict(ctx.job.get("parameters") or {})
    adjustment = (
        params.get("adjustment")
        if isinstance(params.get("adjustment"), dict)
        else {}
    )
    instruction = str(adjustment.get("instruction") or "").strip()
    parent_result_asset_id = str(
        adjustment.get("parent_result_asset_id") or ""
    ).strip()
    if not instruction or not parent_result_asset_id:
        raise JobExecutionError(
            "INVALID_ADJUSTMENT_REFERENCE",
            "调整任务缺少上一版本或具体修改要求",
        )
    model = str(params.get("model") or "gpt-image-2")
    fidelity = max(0, min(int(params.get("fidelity", 40)), 100))
    output_spec = resolve_output_spec(
        model,
        "original",
        str(params.get("output_resolution") or "2k"),
        image.size,
        explicit=True,
    )
    _record_execution_trace_safe(
        trace,
        "output.spec",
        "completed",
        parameters={
            "requested_ratio": "original",
            "requested_resolution": output_spec["requested_resolution"],
            "source_width": output_spec["source_width"],
            "source_height": output_spec["source_height"],
        },
        output={
            "desired_ratio": output_spec["desired_ratio"],
            "effective_ratio": output_spec["effective_ratio"],
            "provider_family": output_spec["provider_family"],
            "provider_params": output_spec["provider_params"],
        },
    )
    knowledge_context = _job_knowledge_context(
        trace,
        mode=str(ctx.job.get("mode") or "single"),
        category=str(trace.get("category") or "general"),
        output_kind="result-adjustment",
        product_name=str(params.get("product_name") or "产品"),
    )
    negative = (
        NEG_BASE
        + ",重构整张画面,改变未提及内容,改变产品数量,改变包装文字,改变画布比例"
    )
    base_prompt = make_prompt(build_adjustment_prompt(instruction), fidelity)
    prompt_bundle = KNOWLEDGE.enrich_prompt(
        base_prompt, negative, knowledge_context
    )
    _record_job_prompt(
        trace,
        prompt_bundle["prompt"],
        prompt_bundle["negative_prompt"],
        "primary",
        prompt_bundle,
        base_prompt=base_prompt,
    )
    ctx.progress(0.08, {"phase": "cloud-adjustment", "version": adjustment.get("version")})
    generated = _cloud_job_call(
        ctx,
        prompt_bundle["prompt"],
        image,
        model,
        negative_prompt=prompt_bundle["negative_prompt"],
        stage="adjustment-1",
        output_spec=output_spec,
        trace=trace,
    )
    ctx.progress(0.9, {"phase": "publishing-adjustment"})
    output = _run_local_stage(
        trace,
        "local.save.adjustment.1",
        lambda: _stage_output(
            generated, stage_dir, "01_adjusted_main.jpg", "result_main"
        ),
        parameters={"format": "jpeg", "quality": 96},
    )
    output["parent_asset_id"] = parent_result_asset_id
    output["metadata"] = {
        "adjustment": {
            "version": adjustment.get("version"),
            "root_job_id": adjustment.get("root_job_id"),
            "parent_job_id": adjustment.get("parent_job_id"),
            "parent_generation_id": adjustment.get("parent_generation_id"),
            "parent_result_asset_id": parent_result_asset_id,
            "review_id": adjustment.get("review_id"),
        }
    }
    return [output], {
        "operation": "result-adjustment",
        "version": adjustment.get("version"),
        "parent_result_asset_id": parent_result_asset_id,
        "paid_image_stages": 1,
        "output_spec": output_spec,
        "actual_main_dimensions": [
            {"width": output["width"], "height": output["height"]}
        ],
    }


def _validated_group_products(detection):
    if not isinstance(detection, dict):
        raise JobExecutionError(
            "INVALID_PRODUCT_DETECTION",
            "Product detection did not return an object",
        )
    raw_products = detection.get("products")
    if not isinstance(raw_products, list):
        raise JobExecutionError(
            "INVALID_PRODUCT_DETECTION",
            "Product detection did not return a product list",
        )
    if not raw_products:
        raise JobExecutionError(
            "NO_PRODUCTS_DETECTED",
            "No products were detected in the group image",
        )
    if len(raw_products) > MAX_GROUP_PRODUCTS:
        raise JobExecutionError(
            "TOO_MANY_PRODUCTS_DETECTED",
            f"A group image supports at most {MAX_GROUP_PRODUCTS} detected products",
            metadata={"detected_products": len(raw_products), "product_limit": MAX_GROUP_PRODUCTS},
        )
    declared_count = detection.get("count")
    if (
        isinstance(declared_count, bool)
        or not isinstance(declared_count, int)
        or declared_count != len(raw_products)
    ):
        raise JobExecutionError(
            "INVALID_PRODUCT_DETECTION",
            "Product detection count does not match the product list",
        )

    normalized_products = []
    for index, product in enumerate(raw_products):
        if not isinstance(product, dict):
            raise JobExecutionError(
                "INVALID_PRODUCT_DETECTION",
                f"Detected product {index + 1} is not an object",
            )
        product_type = str(product.get("ptype") or "").strip()
        if product_type not in GROUP_PRODUCT_TYPES:
            raise JobExecutionError(
                "INVALID_PRODUCT_DETECTION",
                f"Detected product {index + 1} has an unsupported type",
            )
        has_container = product.get("has_container", False)
        cutoff = product.get("cutoff", False)
        if not isinstance(has_container, bool) or not isinstance(cutoff, bool):
            raise JobExecutionError(
                "INVALID_PRODUCT_DETECTION",
                f"Detected product {index + 1} has invalid boolean fields",
            )
        raw_bbox = product.get("bbox")
        if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
            raise JobExecutionError(
                "INVALID_PRODUCT_DETECTION",
                f"Detected product {index + 1} has an invalid bounding box",
            )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in raw_bbox
        ):
            raise JobExecutionError(
                "INVALID_PRODUCT_DETECTION",
                f"Detected product {index + 1} has non-numeric bounding box coordinates",
            )
        bbox = [max(0, min(int(value), 1000)) for value in raw_bbox]
        x1, y1, x2, y2 = bbox
        if x2 <= x1 or y2 <= y1:
            raise JobExecutionError(
                "INVALID_PRODUCT_DETECTION",
                f"Detected product {index + 1} has an empty bounding box",
            )
        name = str(product.get("name") or f"产品{index + 1}").strip()[:100]
        normalized_products.append({
            "name": name or f"产品{index + 1}",
            "ptype": product_type,
            "has_container": has_container,
            "cutoff": cutoff,
            "bbox": bbox,
        })
    return normalized_products


def _execute_group_job(ctx, source_asset, image, stage_dir, trace):
    params = dict(ctx.job.get("parameters") or {})
    model = str(params.get("model") or "gemini-3.1-flash-image-preview")
    platter_default = str(params.get("platter") or "auto")
    do_refine = bool(params.get("refine", True))
    fidelity = max(0, min(int(params.get("fidelity", 35)), 100))
    angle = str(params.get("angle") or "auto")
    ctx.progress(0.03, {"phase": "vlm"})
    vlm_started = time.perf_counter()
    try:
        with ctx.resource("vlm"):
            detection = vlm_detect_products(str(source_asset["path"]), str(ctx.job_id))
        # Validate the complete untrusted VLM response before the first paid
        # image call. A late malformed product must not waste earlier calls.
        products = _validated_group_products(detection)
    except Exception as exc:
        _record_execution_trace_safe(
            trace,
            "vlm.detect",
            "failed",
            parameters={"model": "gemini-3.5-flash", "purpose": "group-detection"},
            output={
                "elapsed_ms": round((time.perf_counter() - vlm_started) * 1000, 3),
                "billing": unavailable_billing_evidence(),
            },
            error_code=str(getattr(exc, "code", "VLM_DETECTION_FAILED")),
            error_message=str(exc) or type(exc).__name__,
        )
        raise
    _record_execution_trace_safe(
        trace,
        "vlm.detect",
        "completed",
        parameters={"model": "gemini-3.5-flash", "purpose": "group-detection"},
        output={
            "detected_products": len(products),
            "product_names": [product["name"] for product in products],
            "elapsed_ms": round((time.perf_counter() - vlm_started) * 1000, 3),
            "billing": unavailable_billing_evidence(),
        },
    )

    width, height = image.size
    outputs = []
    output_specs = []
    per = 0.92 / len(products)
    for index, product in enumerate(products):
        name = str(product.get("name") or f"产品{index + 1}")
        product_type = str(product.get("ptype") or "food")
        has_container = bool(product.get("has_container", False))
        cutoff = bool(product.get("cutoff", False))
        x1, y1, x2, y2 = product["bbox"]
        bbox = (
            int(x1 / 1000 * width), int(y1 / 1000 * height),
            int(x2 / 1000 * width), int(y2 / 1000 * height),
        )
        platter = "remove" if product_type == "packaging" else (
            "keep" if platter_default == "keep" or (platter_default == "auto" and has_container)
            else "remove"
        )
        cropped = crop_product(image, bbox, width, height, pad_pct=0.20 if cutoff else 0.12)
        output_spec = resolve_output_spec(
            model,
            str(params.get("output_ratio") or "1:1"),
            str(params.get("output_resolution") or "2k"),
            cropped.size,
            explicit=bool(params.get("output_spec_explicit", False)),
        )
        output_specs.append({"product_index": index + 1, "product_name": name, **output_spec})
        _record_execution_trace_safe(
            trace,
            f"output.spec.product-{index + 1}",
            "completed",
            parameters={
                "requested_ratio": output_spec["requested_ratio"],
                "requested_resolution": output_spec["requested_resolution"],
                "source_width": output_spec["source_width"],
                "source_height": output_spec["source_height"],
            },
            output={
                "desired_ratio": output_spec["desired_ratio"],
                "effective_ratio": output_spec["effective_ratio"],
                "provider_family": output_spec["provider_family"],
                "provider_params": output_spec["provider_params"],
            },
        )
        negative = build_negative(platter)
        knowledge_context = _job_knowledge_context(
            trace,
            mode="group-split",
            category=product_type,
            output_kind="ecommerce-main",
            platter=platter,
            angle=angle,
            fidelity=fidelity,
            product_name=name,
        )
        ctx.progress(0.05 + index * per, {"phase": "cloud-primary", "product": index + 1})
        base_stage1_prompt = make_prompt(
            build_multi_stage1_prompt(
                name, product_type, "cutoff" if cutoff else "complete", platter, angle
            ),
            fidelity,
        )
        stage1 = KNOWLEDGE.enrich_prompt(
            base_stage1_prompt, negative, knowledge_context
        )
        _record_job_prompt(
            trace,
            stage1["prompt"],
            stage1["negative_prompt"],
            "primary" if index == 0 else f"product-{index + 1}-primary",
            stage1,
            base_prompt=base_stage1_prompt,
        )
        generated = _cloud_job_call(
            ctx,
            stage1["prompt"],
            cropped,
            model,
            negative_prompt=stage1["negative_prompt"],
            stage=f"1-{index + 1}",
            output_spec=output_spec,
            trace=trace,
        )
        if do_refine:
            ctx.progress(0.05 + index * per + per * 0.38, {"phase": "cloud-refine", "product": index + 1})
            base_stage2_prompt = make_prompt(
                build_stage2_prompt(name, platter, product_type, angle), fidelity
            )
            stage2 = KNOWLEDGE.enrich_prompt(
                base_stage2_prompt, negative, knowledge_context
            )
            _record_job_prompt(
                trace,
                stage2["prompt"],
                stage2["negative_prompt"],
                f"product-{index + 1}-refine",
                stage2,
                base_prompt=base_stage2_prompt,
            )
            generated = _cloud_job_call(
                ctx,
                stage2["prompt"],
                generated,
                model,
                negative_prompt=stage2["negative_prompt"],
                stage=f"2-{index + 1}",
                output_spec=output_spec,
                trace=trace,
            )
        main_image = _run_local_stage(
            trace,
            f"local.enhance.{index + 1}",
            lambda: post_process_enhance(generated),
            parameters={"operation": "post-process-enhance"},
        )
        safe_name = safe_stem(name, f"product-{index + 1}")
        outputs.append(_run_local_stage(
            trace,
            f"local.save.main.{index + 1}",
            lambda: _stage_output(
                main_image,
                stage_dir,
                f"{index + 1:02d}_{safe_name}_main.jpg",
                "result_main",
            ),
            parameters={"format": "jpeg", "quality": 96},
        ))
        ctx.progress(0.05 + index * per + per * 0.78, {"phase": "local-cutout", "product": index + 1})
        with ctx.resource("local-cutout"):
            cutout = _run_local_stage(
                trace,
                f"local.cutout.{index + 1}",
                lambda: tight_crop_alpha(remove_bg_hd(main_image)),
                parameters={
                    "model": "local-rembg/birefnet-general",
                    "alpha_mode": "native-soft",
                    "post_process_mask": False,
                },
            )
        outputs.append(_run_local_stage(
            trace,
            f"local.save.cutout.{index + 1}",
            lambda: _stage_output(
                cutout,
                stage_dir,
                f"{index + 1:02d}_{safe_name}_cutout.png",
                "result_cutout",
            ),
            parameters={"format": "png"},
        ))
    return outputs, {
        "detected_products": len(products),
        "refined": do_refine,
        "output_specs": output_specs,
        "actual_main_dimensions": [
            {"width": output["width"], "height": output["height"]}
            for output in outputs if output["role"] == "result_main"
        ],
    }


def _execute_cutout_job(ctx, image, stage_dir, trace):
    selection = trace.get("parameters", {}).get("cutout_selection") or {
        "strategy": "foreground"
    }
    semantic = selection.get("strategy") == "semantic"
    ctx.progress(0.08, {"phase": "semantic-cutout" if semantic else "local-cutout"})
    segment_started = time.perf_counter()
    try:
        with ctx.resource("local-cutout"):
            segmented = remove_bg_hd(image)
            if semantic:
                source_plan = (selection.get("sources") or {}).get(trace["source_asset_id"])
                if not source_plan:
                    raise SemanticCutoutError(
                        "SEMANTIC_CONFIRMATION_REQUIRED",
                        "当前源图缺少已确认选区",
                        stage="selection",
                    )
                segmented = apply_confirmed_regions(segmented, source_plan.get("regions"))
                segmented = apply_mask_edits(
                    segmented,
                    source_plan.get("mask_edits"),
                    source_plan.get("regions"),
                )
            cutout = tight_crop_alpha(segmented)
    except SemanticCutoutError as exc:
        _record_execution_trace_safe(
            trace,
            f"cutout.{exc.stage}",
            "failed",
            error_code=exc.code,
            error_message=exc.message,
            parameters={"strategy": selection.get("strategy", "foreground")},
        )
        raise JobExecutionError(exc.code, exc.message) from exc
    except Exception as exc:
        _record_execution_trace_safe(
            trace,
            "cutout.segment",
            "failed",
            parameters={"strategy": selection.get("strategy", "foreground")},
            output={"elapsed_ms": round((time.perf_counter() - segment_started) * 1000, 3)},
            error_code=str(getattr(exc, "code", "CUTOUT_FAILED")),
            error_message=str(exc) or type(exc).__name__,
        )
        raise
    segment_elapsed_ms = round((time.perf_counter() - segment_started) * 1000, 3)
    output = _run_local_stage(
        trace,
        "local.save.cutout.1",
        lambda: _stage_output(cutout, stage_dir, "01_cutout.png", "result_cutout"),
        parameters={"format": "png"},
    )
    ignored_fields = []
    if trace.get("brief") and not semantic:
        ignored_fields.append("brief")
    if trace.get("intent_locks"):
        ignored_fields.append("intent_locks")
    source_plan = (selection.get("sources") or {}).get(trace["source_asset_id"], {})
    _record_execution_trace_safe(
        trace,
        "cutout.segment",
        "completed",
        ignored_fields=ignored_fields,
        parameters={
            "model": "local-rembg/birefnet-general",
            "alpha_mode": "native-soft",
            "post_process_mask": False,
            "operation": (
                "confirmed-region-segmentation" if semantic else "foreground-segmentation"
            ),
            "strategy": selection.get("strategy", "foreground"),
            "selection_method": source_plan.get("method", "all-foreground"),
            "query": selection.get("query", ""),
            "model_query": selection.get("model_query", ""),
            "target_count": selection.get("target_count", 0),
            "confirmation_digest": source_plan.get("digest", ""),
            "mask_edit_count": len(source_plan.get("mask_edits") or []),
        },
        output={
            "output_name": output["name"],
            "selection_prompt_supported": False,
            "text_grounding_supported": source_plan.get("method") in {
                "model-candidate-confirmed", "model-assisted-confirmed",
            },
            "manual_grounding_confirmed": semantic,
            "human_confirmation_required": semantic,
            "selected_region_count": len(source_plan.get("regions") or []),
            "mask_edit_count": len(source_plan.get("mask_edits") or []),
            "elapsed_ms": segment_elapsed_ms,
        },
    )
    return [output], {
        "operation": "semantic-selection-cutout" if semantic else "background-removal",
        "selection_method": source_plan.get("method", "all-foreground"),
        "selected_region_count": len(source_plan.get("regions") or []),
        "mask_edit_count": len(source_plan.get("mask_edits") or []),
    }


def execute_job_workflow(ctx):
    """Execute one durable item from its persisted source asset id."""
    refresh_runtime_config()
    ctx.checkpoint()
    output_root = _job_output_root(ctx, test_write=True)
    source_asset, source_path = ASSET_STORE.resolve_asset_path(str(ctx.item["source_asset_id"]))
    source_asset = {**source_asset, "path": str(source_path)}
    try:
        with Image.open(source_path) as opened:
            image = opened.copy()
    except Exception as exc:
        raise JobExecutionError("INVALID_SOURCE_IMAGE", "The persisted source image cannot be decoded") from exc
    trace = _job_trace_context(ctx)
    trace["workflow_started_perf"] = time.perf_counter()
    _record_execution_trace_safe(
        trace,
        "workflow.start",
        "started",
        output={
            "mode": str(ctx.job["mode"]),
            "source_asset_id": trace["source_asset_id"],
            "trace_contract_version": GENERATION_TRACE_CONTRACT_VERSION,
            "prompt_version": PROMPT_COMPILER_VERSION,
        },
    )
    stage_dir = _attempt_directory(ctx)
    try:
        mode = str(ctx.job["mode"])
        parameters = dict(ctx.job.get("parameters") or {})
        adjustment = (
            parameters.get("adjustment")
            if isinstance(parameters.get("adjustment"), dict)
            else {}
        )
        if adjustment:
            try:
                reference_asset = LEDGER.get_asset(
                    str(adjustment.get("parent_result_asset_id") or "")
                )
                reference_path = _resolve_result_asset_path(reference_asset)
                with Image.open(reference_path) as opened:
                    reference_image = opened.copy()
            except (AssetStoreError, KeyError, OSError, ValueError) as exc:
                raise JobExecutionError(
                    "INVALID_ADJUSTMENT_REFERENCE",
                    "上一版本结果已不可读取，未发起新的付费处理",
                ) from exc
            _record_execution_trace_safe(
                trace,
                "adjustment.reference",
                "completed",
                parameters={
                    "parent_job_id": adjustment.get("parent_job_id"),
                    "parent_generation_id": adjustment.get("parent_generation_id"),
                },
                output={
                    "parent_result_asset_id": reference_asset.get("id"),
                    "width": reference_image.width,
                    "height": reference_image.height,
                    "version": adjustment.get("version"),
                },
            )
            outputs, metadata = _execute_adjustment_job(
                ctx, source_asset, reference_image, stage_dir, trace
            )
        elif mode in {"single", "multi-file"}:
            outputs, metadata = _execute_single_job(ctx, source_asset, image, stage_dir, trace)
        elif mode == "group-split":
            outputs, metadata = _execute_group_job(ctx, source_asset, image, stage_dir, trace)
        elif mode == "cutout-batch":
            outputs, metadata = _execute_cutout_job(ctx, image, stage_dir, trace)
        else:
            raise JobExecutionError("UNSUPPORTED_JOB_MODE", f"Unsupported job mode: {mode}")
        ctx.progress(0.98, {"phase": "publishing"})
        return _staged_job_result(
            ctx,
            trace,
            stage_dir,
            outputs,
            {"mode": mode, **metadata},
            source_asset,
            output_root,
        )
    except Exception as exc:
        _record_execution_trace_safe(
            trace,
            "workflow.failed",
            "failed",
            error_code=str(getattr(exc, "code", "PROCESSOR_ERROR")),
            error_message=str(exc) or type(exc).__name__,
            output={
                "exception_type": type(exc).__name__,
                "elapsed_ms": round(
                    (time.perf_counter() - trace["workflow_started_perf"]) * 1000, 3
                ),
            },
        )
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
        raise


async def _persist_legacy_upload(upload, fallback_name):
    if upload is None:
        return None
    try:
        return await run_in_threadpool(
            ASSET_STORE.import_stream,
            upload.file,
            upload.filename or fallback_name,
        )
    except AssetStoreError as exc:
        raise_asset_http_error(exc)
    finally:
        await upload.close()


def _parse_source_asset_ids(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(asset_id).strip() for asset_id in parsed if str(asset_id).strip()]
    except (TypeError, ValueError):
        pass
    return [part.strip() for part in str(value).split(",") if part.strip()]


async def _submit_legacy_job(mode, source_asset_ids, parameters):
    response = await create_durable_job(JobCreateRequest(
        mode=mode,
        source_asset_ids=source_asset_ids,
        parameters=parameters,
        client_request_id=f"legacy-{uuid.uuid4()}",
    ))
    return response["job"]


@app.post("/api/single")
async def process_single(
    file: Optional[UploadFile] = File(None),
    source_asset_id: str = Form(""),
    product_name: str = Form(""),
    model: str = Form("gpt-image-2"),
    batch: int = Form(1),
    platter: str = Form("auto"),
    fidelity: int = Form(40),
    angle: str = Form("auto"),
    session_id: str = Form(""),
    category: str = Form("general"),
    brief: str = Form(""),
    intent_locks: str = Form(""),
):
    source_ids = [source_asset_id.strip()] if source_asset_id.strip() else []
    imported = await _persist_legacy_upload(file, "single-product.png")
    if imported is not None:
        source_ids = [imported["id"]]
    job = await _submit_legacy_job("single", source_ids, {
        "model": model,
        "batch": batch,
        "platter": platter,
        "fidelity": fidelity,
        "angle": angle,
        "product_name": product_name,
        "category": category,
        "brief": parse_form_object(brief),
        "intent_locks": parse_form_object(
            intent_locks,
            {"subject_shape": True, "product_count": True, "angle": angle == "keep"},
        ),
        "legacy_session_id": session_id,
    })
    return {
        "task_id": job["id"],
        "job_id": job["id"],
        "session_id": job["session_id"],
        "generation_id": job["items"][0]["generation_id"],
    }

@app.post("/api/group-split")
@app.post("/api/multi")
async def process_multi(
    file: Optional[UploadFile] = File(None),
    source_asset_id: str = Form(""),
    model: str = Form("gemini-3.1-flash-image-preview"),
    platter: str = Form("auto"),
    refine: bool = Form(True),
    fidelity: int = Form(35),
    angle: str = Form("auto"),
    session_id: str = Form(""),
    category: str = Form("general"),
    brief: str = Form(""),
    intent_locks: str = Form(""),
):
    source_ids = [source_asset_id.strip()] if source_asset_id.strip() else []
    imported = await _persist_legacy_upload(file, "group-shot.png")
    if imported is not None:
        source_ids = [imported["id"]]
    job = await _submit_legacy_job("group-split", source_ids, {
        "model": model,
        "platter": platter,
        "refine": refine,
        "fidelity": fidelity,
        "angle": angle,
        "category": category,
        "brief": parse_form_object(brief),
        "intent_locks": parse_form_object(
            intent_locks,
            {"subject_shape": True, "product_count": True},
        ),
        "legacy_session_id": session_id,
    })
    return {
        "task_id": job["id"],
        "job_id": job["id"],
        "session_id": job["session_id"],
        "generation_id": job["items"][0]["generation_id"],
    }


@app.post("/api/multi-file")
async def process_multi_file(
    files: Optional[list[UploadFile]] = File(None),
    source_asset_ids: str = Form(""),
    model: str = Form("gpt-image-2"),
    variations: int = Form(1),
    platter: str = Form("auto"),
    fidelity: int = Form(40),
    angle: str = Form("auto"),
    session_id: str = Form(""),
    category: str = Form("general"),
    brief: str = Form(""),
    intent_locks: str = Form(""),
):
    source_ids = _parse_source_asset_ids(source_asset_ids)
    for index, upload in enumerate(files or []):
        imported = await _persist_legacy_upload(upload, f"multi-{index + 1}.png")
        source_ids.append(imported["id"])
    variations = max(1, min(int(variations), 4))
    if len(source_ids) * variations > 24:
        raise HTTPException(status_code=400, detail="本批最多生成 24 张，请减少文件数或每图方案数")
    brief_data = parse_form_object(brief)
    locks = parse_form_object(
        intent_locks,
        {"subject_shape": True, "product_count": True, "angle": angle == "keep"},
    )
    job = await _submit_legacy_job("multi-file", source_ids, {
        "model": model,
        "variations": variations,
        "platter": platter,
        "fidelity": fidelity,
        "angle": angle,
        "category": category,
        "brief": brief_data,
        "intent_locks": locks,
        "legacy_session_id": session_id,
    })
    return {
        "task_id": job["id"],
        "job_id": job["id"],
        "session_id": job["session_id"],
        "file_count": len(source_ids),
        "planned_outputs": len(source_ids) * variations,
    }


@app.post("/api/cutout-batch")
async def process_cutout_batch(
    files: Optional[list[UploadFile]] = File(None),
    source_asset_ids: str = Form(""),
    session_id: str = Form(""),
    brief: str = Form(""),
):
    source_ids = _parse_source_asset_ids(source_asset_ids)
    for index, upload in enumerate(files or []):
        imported = await _persist_legacy_upload(upload, f"cutout-{index + 1}.png")
        source_ids.append(imported["id"])
    brief_data = parse_form_object(brief)
    job = await _submit_legacy_job("cutout-batch", source_ids, {
        "model": "local-rembg",
        "operation": "background-removal",
        "brief": brief_data,
        "intent_locks": {"subject_shape": True, "product_count": True},
        "legacy_session_id": session_id,
    })
    return {
        "task_id": job["id"],
        "job_id": job["id"],
        "session_id": job["session_id"],
        "file_count": len(source_ids),
    }


@app.post("/api/cutout")
async def cutout_only(
    file: Optional[UploadFile] = File(None),
    source_asset_id: str = Form(""),
    session_id: str = Form(""),
):
    """Compatibility entry point backed by the durable local-cutout queue."""
    source_ids = [source_asset_id.strip()] if source_asset_id.strip() else []
    imported = await _persist_legacy_upload(file, "cutout-source.png")
    if imported is not None:
        source_ids = [imported["id"]]
    job = await _submit_legacy_job("cutout-batch", source_ids, {
        "model": "local-rembg",
        "operation": "background-removal",
        "legacy_session_id": session_id,
    })
    return {
        "task_id": job["id"],
        "job_id": job["id"],
        "session_id": job["session_id"],
        "generation_id": job["items"][0]["generation_id"],
        "status": job["status"],
    }


@app.get("/api/thumbnail")
async def get_thumbnail(path: str):
    """Serve a local image file for display in the UI."""
    try:
        allowed_roots = (*_configured_output_roots(), ASSET_DIR.resolve())
        candidate = Path(path).resolve(strict=False)
        if not any(candidate.is_relative_to(root) for root in allowed_roots):
            return JSONResponse({"error": "access denied"}, status_code=403)
        p = Path(path).resolve(strict=True)
        if not p.is_file():
            return JSONResponse({"error": "file not found"}, status_code=404)
        ext = p.suffix.lower()
        media_type = "image/jpeg"
        if ext == ".png": media_type = "image/png"
        elif ext == ".webp": media_type = "image/webp"
        return FileResponse(str(p), media_type=media_type)
    except FileNotFoundError:
        return JSONResponse({"error": "file not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
@app.get("/api/history")
async def get_history():
    items = []
    for f in sorted(OUTPUT_DIR.glob("product_*_main.jpg"), reverse=True)[:50]:
        items.append({"name": f.name, "path": str(f), "time": f.stat().st_mtime})
    for d in sorted((OUTPUT_DIR / "multi-products").glob("*"), reverse=True)[:20]:
        if d.is_dir():
            mains = list(d.glob("*_main.jpg"))
            if mains:
                items.append({"name": d.name + " (批量)", "path": str(d), "time": d.stat().st_mtime, "batch": True})
    items.sort(key=lambda x: x["time"], reverse=True)
    return items[:50]


# ======================== BATCH FOLDER PROCESSING ========================
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.tif'}

def run_batch_folder(task_id, folder_path, mode, model_key, platter, fidelity, angle, do_refine, output_dir):
    try:
        tracker.create_task(task_id)
        folder = Path(folder_path)
        if not folder.exists() or not folder.is_dir():
            tracker.complete(task_id, error=f"文件夹不存在: {folder_path}")
            return

        images = []
        for f in sorted(folder.iterdir()):
            if f.is_file() and f.suffix.lower() in IMAGE_EXTS:
                images.append(f)

        if not images:
            tracker.complete(task_id, error="文件夹中未找到图片文件")
            return

        total = len(images)
        date_str = datetime.now().strftime("%Y-%m-%d")
        out_base = Path(output_dir) / date_str
        out_base.mkdir(parents=True, exist_ok=True)

        with tracker._lock:
            t = tracker._tasks.get(task_id, {})
            t["total"] = total
            t["current_index"] = 0
            t["current_file"] = ""
            t["success"] = 0
            t["failed"] = 0
            t["output_dir"] = str(out_base)
            t["results"] = []

        tracker.update(task_id, progress=0.01, status="processing",
                       message=f"找到 {total} 张图片，开始处理...")
        log_msg(task_id, f"批量处理开始 | 模式: {mode} | 文件夹: {folder_path} | 共{total}张")
        log_msg(task_id, f"输出目录: {out_base}")

        success_count = 0
        failed_count = 0
        results_list = []

        for idx, img_path in enumerate(images):
            fname = img_path.stem
            tracker.update(task_id, current_index=idx, current_file=img_path.name,
                           message=f"处理中 ({idx+1}/{total}): {img_path.name}")
            log_msg(task_id, f"--- [{idx+1}/{total}] {img_path.name} ---")

            try:
                img = Image.open(img_path)

                if mode == "cutout":
                    tracker.update(task_id, progress=0.1 + 0.9*(idx/total),
                                   message=f"抠图中 ({idx+1}/{total}): {img_path.name}")
                    cut = remove_bg_hd(img)
                    cut = tight_crop_alpha(cut)
                    cp = out_base / f"{fname}_cutout.png"
                    cut.save(cp, "PNG")
                    main_path = None
                    cut_path = str(cp)
                    log_msg(task_id, f"  抠图完成: {cp.name}")

                elif mode == "multi":
                    tracker.update(task_id, progress=0.05 + 0.9*(idx/total),
                                   message=f"多产品分割 ({idx+1}/{total}): {img_path.name}")
                    tmp_in = save_temp(img, f"batch_m_in_{idx}")
                    det = vlm_detect_products(tmp_in, task_id)
                    products = det.get("products", [])
                    if not products:
                        log_msg(task_id, f"  未检测到多产品，作为单产品处理")
                        products = [{"name":"产品","ptype":"food","has_container":False,"cutoff":False,"bbox":[0,0,1000,1000]}]

                    count = len(products)
                    per_prod = 0.9 / max(count, 1)
                    prod_dir = out_base / f"{fname}_products"
                    prod_dir.mkdir(exist_ok=True)

                    for pi, p in enumerate(products):
                        pname = p.get("name", f"产品{pi+1}")
                        ptype = p.get("ptype", "food")
                        has_cont = p.get("has_container", False)
                        cutoff = p.get("cutoff", False)
                        bbn = p.get("bbox", [0,0,1000,1000])
                        w,h = img.size
                        bbox = (int(bbn[0]/1000*w), int(bbn[1]/1000*h), int(bbn[2]/1000*w), int(bbn[3]/1000*h))
                        pmode = "remove" if ptype=="packaging" else ("keep" if (platter=="keep" or (platter=="auto" and has_cont)) else "remove")
                        pad = 0.20 if cutoff else 0.12
                        cropped = crop_product(img, bbox, w, h, pad_pct=pad)
                        safe = pname.replace("/","_").replace("\\","_").replace(":","_")[:20]
                        neg = build_negative(pmode)

                        p1 = make_prompt(build_multi_stage1_prompt(pname, ptype, "cutoff" if cutoff else "complete", pmode, angle), fidelity)
                        mimg = ai_i2i(p1, cropped, model_key, negative_prompt=neg, size="2048x2048", stage=f"b{idx+1}-1{pi+1}", tid_ref=task_id)
                        if do_refine:
                            p2 = make_prompt(build_stage2_prompt(pname, pmode, ptype, angle), fidelity)
                            mimg = ai_i2i(p2, mimg, model_key, negative_prompt=neg, size="2048x2048", stage=f"b{idx+1}-2{pi+1}", tid_ref=task_id)
                        mimg = post_process_enhance(mimg)
                        mp = prod_dir / f"{pi+1:02d}_{safe}_main.jpg"
                        mimg.save(mp, "JPEG", quality=96)

                        cut = remove_bg_hd(mimg)
                        cut = tight_crop_alpha(cut)
                        cp = prod_dir / f"{pi+1:02d}_{safe}_cutout.png"
                        cut.save(cp, "PNG")
                        tracker.update(task_id, progress=0.05 + 0.9*(idx/total) + per_prod*(pi+1)/count,
                                       message=f"处理中 ({idx+1}/{total}) {img_path.name}: {pname}")

                    main_path = str(prod_dir)
                    cut_path = str(prod_dir)
                    log_msg(task_id, f"  多产品完成: {count}个产品 -> {prod_dir.name}")

                else:
                    tracker.update(task_id, progress=0.05 + 0.9*(idx/total),
                                   message=f"AI生成 ({idx+1}/{total}): {img_path.name}")
                    tmp_vlm = save_temp(img, f"batch_vlm_{idx}")
                    det = vlm_detect_products(tmp_vlm, task_id)
                    pname = ""
                    ptype = "food"
                    if det.get("products"):
                        pp = det["products"][0]
                        pname = pp.get("name", "产品")
                        ptype = pp.get("ptype", "food")

                    eff_platter = platter
                    if ptype == "packaging": eff_platter = "remove"

                    neg = build_negative(eff_platter)
                    p1 = make_prompt(build_single_prompt(pname or "产品", eff_platter, ptype, angle), fidelity)
                    img1 = ai_i2i(p1, img, model_key, negative_prompt=neg, size="2048x2048", stage=f"b{idx+1}-1", tid_ref=task_id)
                    tracker.update(task_id, progress=0.05 + 0.9*(idx/total) + 0.9*0.3/total,
                                   message=f"精修 ({idx+1}/{total}): {img_path.name}")
                    p2 = make_prompt(build_stage2_prompt(pname or "产品", eff_platter, ptype, angle), fidelity)
                    img2 = ai_i2i(p2, img1, model_key, negative_prompt=neg, size="2048x2048", stage=f"b{idx+1}-2", tid_ref=task_id)
                    main_img = post_process_enhance(img2)
                    mp = out_base / f"{fname}_main.jpg"
                    main_img.save(mp, "JPEG", quality=96)
                    main_path = str(mp)

                    cut = remove_bg_hd(main_img)
                    cut = tight_crop_alpha(cut)
                    cp = out_base / f"{fname}_cutout.png"
                    cut.save(cp, "PNG")
                    cut_path = str(cp)
                    log_msg(task_id, f"  完成: {mp.name}, {cp.name}")

                success_count += 1
                results_list.append({"file": img_path.name, "main": main_path, "cutout": cut_path})

                with tracker._lock:
                    t = tracker._tasks.get(task_id, {})
                    t["current_index"] = idx + 1
                    t["success"] = success_count
                    t["failed"] = failed_count
                    t["results"] = results_list
                    t["progress"] = (idx + 1) / total

            except Exception as e:
                failed_count += 1
                log_msg(task_id, f"  [失败] {img_path.name}: {e}")
                import traceback as _tb
                _tb.print_exc()
                with tracker._lock:
                    t = tracker._tasks.get(task_id, {})
                    t["current_index"] = idx + 1
                    t["failed"] = failed_count
                    t["success"] = success_count

        tracker.update(task_id, progress=1.0,
                       message=f"批量处理完成: {success_count}成功, {failed_count}失败")
        tracker.complete(task_id, results={
            "success": success_count,
            "failed": failed_count,
            "output_dir": str(out_base),
            "total": total,
        })
        log_msg(task_id, f"=== 批量完成! 成功{success_count},失败{failed_count} ===")

    except Exception as e:
        import traceback as _tb
        _tb.print_exc()
        tracker.complete(task_id, error=str(e))

@app.post("/api/batch-folder")
async def batch_folder(
    folder_path: str = Form(...),
    mode: str = Form("single"),
    model: str = Form("gpt-image-2"),
    platter: str = Form("auto"),
    fidelity: int = Form(40),
    angle: str = Form("auto"),
    refine: str = Form("1"),
    output_dir: str = Form("D:/图像处理"),
):
    raise HTTPException(
        status_code=410,
        detail={
            "code": "BATCH_FOLDER_RETIRED",
            "message": "Folder batch processing has moved to the durable asset workspace and job queue",
        },
    )

@app.get("/api/batch-progress/{task_id}")
async def get_batch_progress(task_id: str):
    return await get_progress(task_id)

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"Product Atelier backend starting on port {port}...", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
