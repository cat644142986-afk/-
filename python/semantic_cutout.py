from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable

from PIL import Image, ImageChops, ImageDraw


class SemanticCutoutError(ValueError):
    def __init__(self, code: str, message: str, *, stage: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage


def _query(value: Any) -> str:
    query = " ".join(str(value or "").strip().split())[:80]
    if not query:
        raise SemanticCutoutError(
            "SEMANTIC_QUERY_REQUIRED",
            "请先填写要保留的物体名称",
            stage="recognition",
        )
    return query


def _target_count(value: Any) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise SemanticCutoutError(
            "SEMANTIC_TARGET_COUNT_INVALID",
            "目标数量必须是 1 到 8 的整数",
            stage="selection",
        ) from exc
    if count < 1 or count > 8:
        raise SemanticCutoutError(
            "SEMANTIC_TARGET_COUNT_INVALID",
            "目标数量必须是 1 到 8 的整数",
            stage="selection",
        )
    return count


def _model_query(value: Any) -> str:
    model_query = " ".join(str(value or "").strip().split()).lower().rstrip(".")[:80]
    if any("\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff" for char in model_query):
        raise SemanticCutoutError(
            "SEMANTIC_MODEL_QUERY_INVALID",
            "模型识别词必须使用英文；可清空后重新自动映射",
            stage="recognition",
        )
    return model_query


def _number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SemanticCutoutError(
            "SEMANTIC_REGION_INVALID",
            f"选区 {field} 不是有效数字",
            stage="selection",
        ) from exc
    if not math.isfinite(result):
        raise SemanticCutoutError(
            "SEMANTIC_REGION_INVALID",
            f"选区 {field} 不是有限数字",
            stage="selection",
        )
    return result


def normalize_regions(regions: Any, query: str) -> list[dict[str, Any]]:
    if not isinstance(regions, list):
        raise SemanticCutoutError(
            "SEMANTIC_REGIONS_REQUIRED",
            "请在原图上框选要保留的目标",
            stage="selection",
        )
    normalized = []
    for index, region in enumerate(regions):
        if not isinstance(region, dict) or not isinstance(region.get("bbox"), (list, tuple)):
            raise SemanticCutoutError(
                "SEMANTIC_REGION_INVALID",
                f"第 {index + 1} 个选区缺少边界",
                stage="selection",
            )
        bbox = region["bbox"]
        if len(bbox) != 4:
            raise SemanticCutoutError(
                "SEMANTIC_REGION_INVALID",
                f"第 {index + 1} 个选区边界必须包含 x、y、宽、高",
                stage="selection",
            )
        x, y, width, height = (
            _number(bbox[0], "x"),
            _number(bbox[1], "y"),
            _number(bbox[2], "width"),
            _number(bbox[3], "height"),
        )
        if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
            raise SemanticCutoutError(
                "SEMANTIC_REGION_OUT_OF_BOUNDS",
                f"第 {index + 1} 个选区超出图片范围",
                stage="selection",
            )
        if width < 0.01 or height < 0.01:
            raise SemanticCutoutError(
                "SEMANTIC_REGION_TOO_SMALL",
                f"第 {index + 1} 个选区太小，请扩大后重试",
                stage="selection",
            )
        normalized_region = {
            "id": str(region.get("id") or f"target-{index + 1}")[:80],
            "label": str(region.get("label") or query).strip()[:80] or query,
            "bbox": [round(value, 6) for value in (x, y, width, height)],
            "origin": "automatic" if region.get("origin") == "automatic" else "manual",
        }
        if normalized_region["origin"] == "automatic":
            try:
                confidence = float(region.get("confidence", 0))
            except (TypeError, ValueError):
                confidence = 0.0
            normalized_region["confidence"] = round(max(0.0, min(1.0, confidence)), 4)
        normalized.append(normalized_region)
    return normalized


def _selection_digest(
    source_asset_id: str,
    query: str,
    target_count: int,
    method: str,
    regions: list[dict[str, Any]],
    model_query: str = "",
) -> str:
    payload = {
        "source_asset_id": source_asset_id,
        "query": query,
        "target_count": target_count,
        "method": method,
        "regions": regions,
    }
    if model_query:
        payload["model_query"] = model_query
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_confirmed_selection(
    *,
    source_asset_id: str,
    query: Any,
    target_count: Any,
    regions: Any,
    model_query: Any = "",
) -> dict[str, Any]:
    asset_id = str(source_asset_id or "").strip()
    if not asset_id:
        raise SemanticCutoutError(
            "SEMANTIC_SOURCE_REQUIRED",
            "请先选择一张源图片",
            stage="selection",
        )
    normalized_query = _query(query)
    normalized_model_query = _model_query(model_query)
    count = _target_count(target_count)
    normalized_regions = normalize_regions(regions, normalized_query)
    if len(normalized_regions) != count:
        raise SemanticCutoutError(
            "SEMANTIC_TARGET_COUNT_MISMATCH",
            f"要求保留 {count} 个目标，但当前确认了 {len(normalized_regions)} 个",
            stage="selection",
        )
    automatic_count = sum(region.get("origin") == "automatic" for region in normalized_regions)
    method = (
        "model-candidate-confirmed"
        if automatic_count == len(normalized_regions)
        else "model-assisted-confirmed"
        if automatic_count
        else "manual-box"
    )
    source_plan = {
        "source_asset_id": asset_id,
        "status": "confirmed",
        "method": method,
        "regions": normalized_regions,
    }
    source_plan["digest"] = _selection_digest(
        asset_id,
        normalized_query,
        count,
        method,
        normalized_regions,
        normalized_model_query,
    )
    return {
        "strategy": "semantic",
        "query": normalized_query,
        "model_query": normalized_model_query,
        "target_count": count,
        "sources": {asset_id: source_plan},
    }


def normalize_cutout_selection(value: Any) -> dict[str, Any]:
    if value in (None, "", {}):
        return {"strategy": "foreground"}
    if not isinstance(value, dict):
        raise SemanticCutoutError(
            "CUTOUT_SELECTION_INVALID",
            "抠图选择参数必须是对象",
            stage="selection",
        )
    strategy = str(value.get("strategy") or "foreground").strip().lower()
    if strategy == "foreground":
        return {"strategy": "foreground"}
    if strategy != "semantic":
        raise SemanticCutoutError(
            "CUTOUT_STRATEGY_UNSUPPORTED",
            "不支持的抠图策略",
            stage="selection",
        )
    query = _query(value.get("query"))
    model_query = _model_query(value.get("model_query"))
    count = _target_count(value.get("target_count"))
    raw_sources = value.get("sources")
    if not isinstance(raw_sources, dict):
        raw_sources = {}
    sources: dict[str, dict[str, Any]] = {}
    for key, raw_plan in raw_sources.items():
        if not isinstance(raw_plan, dict):
            raise SemanticCutoutError(
                "SEMANTIC_CONFIRMATION_INVALID",
                "目标确认记录无效，请重新确认",
                stage="selection",
            )
        asset_id = str(raw_plan.get("source_asset_id") or key).strip()
        if asset_id != str(key):
            raise SemanticCutoutError(
                "SEMANTIC_SOURCE_MISMATCH",
                "目标确认记录与源图片不一致，请重新确认",
                stage="selection",
            )
        method = str(raw_plan.get("method") or "")
        if raw_plan.get("status") != "confirmed" or method not in {
            "manual-box", "model-candidate-confirmed", "model-assisted-confirmed",
        }:
            raise SemanticCutoutError(
                "SEMANTIC_CONFIRMATION_REQUIRED",
                "智能选物必须先逐张确认目标",
                stage="selection",
            )
        regions = normalize_regions(raw_plan.get("regions"), query)
        if len(regions) != count:
            raise SemanticCutoutError(
                "SEMANTIC_TARGET_COUNT_MISMATCH",
                f"要求保留 {count} 个目标，但当前确认了 {len(regions)} 个",
                stage="selection",
            )
        expected = _selection_digest(asset_id, query, count, method, regions, model_query)
        if str(raw_plan.get("digest") or "") != expected:
            raise SemanticCutoutError(
                "SEMANTIC_CONFIRMATION_STALE",
                "目标名称、数量或选区已经变化，请重新确认",
                stage="selection",
            )
        sources[asset_id] = {
            "source_asset_id": asset_id,
            "status": "confirmed",
            "method": method,
            "digest": expected,
            "regions": regions,
        }
    return {
        "strategy": "semantic",
        "query": query,
        "model_query": model_query,
        "target_count": count,
        "sources": sources,
    }


def validate_selection_sources(selection: dict[str, Any], source_asset_ids: Iterable[str]) -> None:
    if selection.get("strategy") != "semantic":
        return
    expected = [str(asset_id) for asset_id in source_asset_ids]
    actual = list((selection.get("sources") or {}).keys())
    missing = [asset_id for asset_id in expected if asset_id not in actual]
    extra = [asset_id for asset_id in actual if asset_id not in expected]
    if missing or extra:
        raise SemanticCutoutError(
            "SEMANTIC_CONFIRMATION_REQUIRED",
            "智能选物必须对本次每张源图逐张确认，且不能沿用其他图片的选区",
            stage="selection",
        )


def apply_confirmed_regions(image: Image.Image, regions: Any) -> Image.Image:
    normalized = normalize_regions(regions, "目标")
    if not normalized:
        raise SemanticCutoutError(
            "SEMANTIC_REGIONS_REQUIRED",
            "没有可执行的目标选区",
            stage="selection",
        )
    rgba = image.convert("RGBA")
    mask = Image.new("L", rgba.size, 0)
    draw = ImageDraw.Draw(mask)
    width, height = rgba.size
    for region in normalized:
        x, y, box_width, box_height = region["bbox"]
        left = max(0, min(width, math.floor(x * width)))
        top = max(0, min(height, math.floor(y * height)))
        right = max(left + 1, min(width, math.ceil((x + box_width) * width)))
        bottom = max(top + 1, min(height, math.ceil((y + box_height) * height)))
        draw.rectangle((left, top, right - 1, bottom - 1), fill=255)
    alpha = ImageChops.multiply(rgba.getchannel("A"), mask)
    if alpha.getbbox() is None:
        raise SemanticCutoutError(
            "SEMANTIC_SEGMENTATION_EMPTY",
            "框选区域内没有得到有效前景，请扩大选区后重试",
            stage="segmentation",
        )
    rgba.putalpha(alpha)
    return rgba
