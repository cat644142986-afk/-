from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from PIL import Image


GROUNDING_MODEL_PATH_ENV = "PRODUCT_ATELIER_GROUNDING_MODEL_PATH"
DEFAULT_CONFIDENCE_THRESHOLD = 0.75
DEFAULT_REVIEW_CONFIDENCE_THRESHOLD = 0.60
DEFAULT_LOW_CONFIDENCE_THRESHOLD = 0.40
DEFAULT_TEXT_THRESHOLD = 0.30


class GroundingAdapterUnavailable(RuntimeError):
    """Raised when an optional local adapter cannot be used on this machine."""


class GroundingAdapter(Protocol):
    adapter_id: str

    def detect(
        self,
        image: Image.Image,
        query: str,
        *,
        box_threshold: float,
        text_threshold: float,
    ) -> Sequence[Mapping[str, Any]]:
        """Return zero or more candidates with pixel xyxy boxes and confidence."""


@dataclass(frozen=True)
class UnavailableGroundingAdapter:
    reason: str = "not_configured"
    adapter_id: str = "unavailable"

    def detect(
        self,
        image: Image.Image,
        query: str,
        *,
        box_threshold: float,
        text_threshold: float,
    ) -> Sequence[Mapping[str, Any]]:
        del image, query, box_threshold, text_threshold
        raise GroundingAdapterUnavailable(self.reason)


class TransformersGroundingDinoAdapter:
    """Lazy, local-only Grounding DINO adapter.

    The model directory must already exist on disk. `local_files_only=True` and
    a filesystem path are both enforced so opening the confirmation dialog can
    never download weights or call a hosted inference API.
    """

    adapter_id = "transformers-grounding-dino-local"

    def __init__(self, model_path: str | Path, *, device: str | None = None) -> None:
        path = Path(model_path).expanduser()
        if not path.is_absolute():
            path = path.resolve()
        self.model_path = path
        self.requested_device = str(device or "").strip()
        self._processor: Any = None
        self._model: Any = None
        self._torch: Any = None
        self._device = "cpu"
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()

    def _load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            if not self.model_path.is_dir():
                raise GroundingAdapterUnavailable("model_path_missing")
            if not (self.model_path / "config.json").is_file():
                raise GroundingAdapterUnavailable("model_config_missing")
            try:
                import torch
                from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
            except Exception as exc:  # pragma: no cover - depends on optional local runtime
                raise GroundingAdapterUnavailable("runtime_missing") from exc

            device = self.requested_device or ("cuda" if torch.cuda.is_available() else "cpu")
            try:
                processor = AutoProcessor.from_pretrained(
                    str(self.model_path),
                    local_files_only=True,
                    trust_remote_code=False,
                )
                model = AutoModelForZeroShotObjectDetection.from_pretrained(
                    str(self.model_path),
                    local_files_only=True,
                    trust_remote_code=False,
                )
                model = model.to(device).eval()
            except Exception as exc:  # pragma: no cover - requires optional model files
                raise GroundingAdapterUnavailable("model_load_failed") from exc
            self._torch = torch
            self._processor = processor
            self._model = model
            self._device = device

    def detect(
        self,
        image: Image.Image,
        query: str,
        *,
        box_threshold: float,
        text_threshold: float,
    ) -> Sequence[Mapping[str, Any]]:
        prompt = str(query or "").strip().lower()
        if not prompt:
            return []
        if _contains_cjk(prompt):
            raise GroundingAdapterUnavailable("query_translation_required")
        if not prompt.endswith("."):
            prompt += "."
        self._load()
        with self._infer_lock:
            inputs = self._processor(
                images=image.convert("RGB"),
                text=prompt,
                return_tensors="pt",
            )
            inputs = {
                key: value.to(self._device) if hasattr(value, "to") else value
                for key, value in inputs.items()
            }
            with self._torch.no_grad():
                outputs = self._model(**inputs)
            result = self._processor.post_process_grounded_object_detection(
                outputs,
                inputs["input_ids"],
                threshold=box_threshold,
                text_threshold=text_threshold,
                target_sizes=[image.size[::-1]],
            )[0]
        candidates: list[dict[str, Any]] = []
        labels = result.get("text_labels") or result.get("labels", [])
        for box, score, label in zip(
            result.get("boxes", []),
            result.get("scores", []),
            labels,
        ):
            candidates.append({
                "bbox_xyxy": _plain_list(box),
                "confidence": _plain_number(score),
                "label": str(label or prompt),
            })
        return candidates


def _plain_list(value: Any) -> list[float]:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    return [float(item) for item in value]


def _plain_number(value: Any) -> float:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "item"):
        value = value.item()
    return float(value)


def _contains_cjk(value: str) -> bool:
    return any(
        "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
        for char in value
    )


@lru_cache(maxsize=2)
def _cached_local_adapter(configured_path: str) -> GroundingAdapter:
    return TransformersGroundingDinoAdapter(configured_path)


def grounding_adapter_from_environment(
    environment: Mapping[str, str] | None = None,
) -> GroundingAdapter:
    env = os.environ if environment is None else environment
    configured_path = str(env.get(GROUNDING_MODEL_PATH_ENV, "") or "").strip()
    if not configured_path:
        return UnavailableGroundingAdapter("not_configured")
    if environment is None:
        return _cached_local_adapter(configured_path)
    return TransformersGroundingDinoAdapter(configured_path)


def _normalized_region(
    candidate: Mapping[str, Any],
    *,
    width: int,
    height: int,
    query: str,
    index: int,
) -> dict[str, Any] | None:
    raw_box = candidate.get("bbox_xyxy")
    if not isinstance(raw_box, (list, tuple)) or len(raw_box) != 4:
        return None
    try:
        left, top, right, bottom = (float(value) for value in raw_box)
        confidence = float(candidate.get("confidence", 0))
    except (TypeError, ValueError):
        return None
    left = max(0.0, min(float(width), left))
    top = max(0.0, min(float(height), top))
    right = max(left, min(float(width), right))
    bottom = max(top, min(float(height), bottom))
    box_width = (right - left) / max(1, width)
    box_height = (bottom - top) / max(1, height)
    if box_width < 0.01 or box_height < 0.01:
        return None
    confidence = max(0.0, min(1.0, confidence))
    return {
        "id": f"candidate-{index + 1}",
        "label": str(candidate.get("label") or query).strip()[:80] or query,
        "bbox": [
            round(left / max(1, width), 6),
            round(top / max(1, height), 6),
            round(box_width, 6),
            round(box_height, 6),
        ],
        "origin": "automatic",
        "confidence": round(confidence, 4),
    }


def _unavailable_message(reason: str) -> str:
    return {
        "not_configured": "当前未配置本地目标定位模型，请手动框选",
        "model_path_missing": "本地目标定位模型目录不存在，请手动框选",
        "model_config_missing": "本地模型目录不完整，请手动框选",
        "runtime_missing": "本地目标定位运行环境不可用，请手动框选",
        "model_load_failed": "本地目标定位模型加载失败，请手动框选",
        "query_translation_required": "当前本地模型不能直接理解中文名称，请手动框选",
    }.get(reason, "本地目标定位暂不可用，请手动框选")


def ground_semantic_candidates(
    image_path: str | Path,
    query: str,
    target_count: int,
    *,
    adapter: GroundingAdapter | None = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    review_confidence_threshold: float = DEFAULT_REVIEW_CONFIDENCE_THRESHOLD,
    low_confidence_threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
    text_threshold: float = DEFAULT_TEXT_THRESHOLD,
) -> dict[str, Any]:
    """Produce editable candidate boxes; never confirms or submits a task."""

    selected_adapter = adapter or grounding_adapter_from_environment()
    started = time.perf_counter()
    try:
        with Image.open(image_path) as opened:
            image = opened.convert("RGB")
        raw_candidates = selected_adapter.detect(
            image,
            str(query or "").strip(),
            box_threshold=low_confidence_threshold,
            text_threshold=text_threshold,
        )
    except GroundingAdapterUnavailable as exc:
        reason = str(exc) or "unavailable"
        return {
            "status": "unavailable",
            "adapter_id": getattr(selected_adapter, "adapter_id", "unavailable"),
            "available": False,
            "attempted": False,
            "candidates": [],
            "confidence_threshold": confidence_threshold,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "reason": reason,
            "message": _unavailable_message(reason),
        }
    except Exception:
        return {
            "status": "failed",
            "adapter_id": getattr(selected_adapter, "adapter_id", "unknown"),
            "available": True,
            "attempted": True,
            "candidates": [],
            "confidence_threshold": confidence_threshold,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "reason": "inference_failed",
            "message": "本地自动定位失败，请手动框选；当前图片仍可继续处理",
        }

    width, height = image.size
    normalized = []
    for index, candidate in enumerate(raw_candidates):
        region = _normalized_region(
            candidate,
            width=width,
            height=height,
            query=str(query or "").strip(),
            index=index,
        )
        if region and region["confidence"] >= low_confidence_threshold:
            normalized.append(region)
    normalized.sort(key=lambda item: item["confidence"], reverse=True)
    ranked_candidates = normalized[: max(1, int(target_count))]
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    confident = [
        candidate for candidate in ranked_candidates
        if candidate["confidence"] >= confidence_threshold
    ]
    review_candidates = [
        {**candidate, "origin": "automatic-review"}
        for candidate in ranked_candidates
        if review_confidence_threshold <= candidate["confidence"] < confidence_threshold
    ]
    if len(confident) >= int(target_count):
        status = "candidates"
        candidates = confident[: int(target_count)]
        review_candidates = []
        message = f"本地模型找到 {len(candidates)} 个候选，请逐个检查后确认"
    elif confident or review_candidates:
        status = "low_confidence"
        candidates = confident
        if candidates:
            message = (
                f"找到 {len(candidates)} 个可靠候选和 {len(review_candidates)} 个待确认建议；"
                "橙色建议不会自动选中，请逐个采用或手动框选"
            )
        else:
            message = (
                f"找到 {len(review_candidates)} 个待确认建议，尚未自动选中；"
                "请逐个采用或手动框选"
            )
    elif ranked_candidates:
        status = "low_confidence"
        candidates = []
        message = "本地模型结果置信度不足，已停止自动预填；请手动框选目标"
    else:
        status = "no_match"
        candidates = []
        message = "本地模型没有找到可靠候选，请手动框选目标"
    return {
        "status": status,
        "adapter_id": getattr(selected_adapter, "adapter_id", "unknown"),
        "available": True,
        "attempted": True,
        "candidates": candidates,
        "review_candidates": review_candidates,
        "review_confidence_threshold": review_confidence_threshold,
        "review_candidate_count": len(review_candidates),
        "weak_candidate_count": max(
            0,
            len(ranked_candidates) - len(confident) - len(review_candidates),
        ),
        "confidence_threshold": confidence_threshold,
        "elapsed_ms": elapsed_ms,
        "reason": "",
        "message": message,
    }
