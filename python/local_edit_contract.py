# -*- coding: utf-8 -*-
"""Strict local pixel-write contracts for inpaint and outpaint operations."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from PIL import Image, ImageChops, ImageDraw


LOCAL_EDIT_CONTRACT_SCHEMA_VERSION = 1
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9._:-]{2,127}$")
_MAX_DIMENSION = 32768


class LocalEditContractError(ValueError):
    """Raised when an edit could write pixels outside the authorized contract."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise LocalEditContractError(code, message)


def _object(
    value: Any,
    *,
    label: str,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("LOCAL_EDIT_CONTRACT_INVALID", f"{label} must be an object")
    item = dict(value)
    optional = optional or set()
    missing = sorted(required - item.keys())
    unknown = sorted(item.keys() - required - optional)
    if missing:
        _fail(
            "LOCAL_EDIT_CONTRACT_INVALID",
            f"{label} is missing required fields: {', '.join(missing)}",
        )
    if unknown:
        _fail(
            "LOCAL_EDIT_CONTRACT_INVALID",
            f"{label} contains unknown fields: {', '.join(unknown)}",
        )
    return item


def _identifier(value: Any, label: str) -> str:
    candidate = str(value or "").strip()
    if _ID_PATTERN.fullmatch(candidate) is None:
        _fail("LOCAL_EDIT_CONTRACT_INVALID", f"{label} is not a valid id")
    return candidate


def _integer(
    value: Any,
    label: str,
    *,
    minimum: int = 0,
    maximum: int = _MAX_DIMENSION,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("LOCAL_EDIT_CONTRACT_INVALID", f"{label} must be an integer")
    if value < minimum or value > maximum:
        _fail("LOCAL_EDIT_CONTRACT_INVALID", f"{label} is out of range")
    return value


def _size(value: Any, label: str) -> dict[str, int]:
    size = _object(value, label=label, required={"width", "height"})
    return {
        "width": _integer(size["width"], f"{label}.width", minimum=1),
        "height": _integer(size["height"], f"{label}.height", minimum=1),
    }


def _rect(value: Any, label: str) -> dict[str, int]:
    rect = _object(value, label=label, required={"x", "y", "width", "height"})
    return {
        "x": _integer(rect["x"], f"{label}.x"),
        "y": _integer(rect["y"], f"{label}.y"),
        "width": _integer(rect["width"], f"{label}.width", minimum=1),
        "height": _integer(rect["height"], f"{label}.height", minimum=1),
    }


def _rect_within(rect: Mapping[str, int], size: Mapping[str, int]) -> bool:
    return (
        rect["x"] + rect["width"] <= size["width"]
        and rect["y"] + rect["height"] <= size["height"]
    )


def _normalize_cost(value: Any) -> dict[str, Any]:
    cost = _object(
        value,
        label="LocalEdit.cost",
        required={
            "mode",
            "confirmed_call_count",
            "user_confirmation_required",
            "user_confirmed",
            "automatic_paid_retry",
        },
    )
    mode = str(cost["mode"] or "").strip()
    if mode not in {"free", "paid"}:
        _fail("LOCAL_EDIT_CONTRACT_INVALID", "LocalEdit.cost.mode is unsupported")
    calls = _integer(
        cost["confirmed_call_count"],
        "LocalEdit.cost.confirmed_call_count",
        maximum=100,
    )
    for field in (
        "user_confirmation_required",
        "user_confirmed",
        "automatic_paid_retry",
    ):
        if not isinstance(cost[field], bool):
            _fail("LOCAL_EDIT_CONTRACT_INVALID", f"LocalEdit.cost.{field} must be boolean")
    if cost["automatic_paid_retry"]:
        _fail(
            "LOCAL_EDIT_AUTOMATIC_PAID_RETRY_FORBIDDEN",
            "局部编辑失败后不能自动追加付费调用",
        )
    if mode == "free":
        if calls != 0 or cost["user_confirmation_required"] or cost["user_confirmed"]:
            _fail(
                "LOCAL_EDIT_CONTRACT_INVALID",
                "免费本地操作不能声明付费调用或付费确认",
            )
    elif calls < 1 or not cost["user_confirmation_required"] or not cost["user_confirmed"]:
        _fail(
            "LOCAL_EDIT_COST_NOT_CONFIRMED",
            "付费局部编辑必须在执行前确认调用次数",
        )
    return {
        "mode": mode,
        "confirmed_call_count": calls,
        "user_confirmation_required": cost["user_confirmation_required"],
        "user_confirmed": cost["user_confirmed"],
        "automatic_paid_retry": False,
    }


def normalize_local_edit_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return the canonical G3 local-edit contract."""
    base_required = {
        "schema_version",
        "operation_id",
        "mode",
        "source_canvas_version_id",
        "source_layer_id",
        "source_sha256",
        "source_size",
        "roi",
        "mask",
        "strict_pixel_protection",
        "cost",
    }
    raw = _object(
        value,
        label="LocalEdit",
        required=base_required,
        optional={"outpaint"},
    )
    if raw["schema_version"] != LOCAL_EDIT_CONTRACT_SCHEMA_VERSION:
        _fail("LOCAL_EDIT_CONTRACT_INVALID", "LocalEdit.schema_version is unsupported")
    operation_id = _identifier(raw["operation_id"], "LocalEdit.operation_id")
    source_canvas_version_id = _identifier(
        raw["source_canvas_version_id"], "LocalEdit.source_canvas_version_id"
    )
    source_layer_id = _identifier(raw["source_layer_id"], "LocalEdit.source_layer_id")
    source_sha256 = str(raw["source_sha256"] or "").upper()
    if re.fullmatch(r"[A-F0-9]{64}", source_sha256) is None:
        _fail("LOCAL_EDIT_CONTRACT_INVALID", "LocalEdit.source_sha256 is invalid")
    mode = str(raw["mode"] or "").strip()
    if mode not in {"inpaint", "outpaint"}:
        _fail("LOCAL_EDIT_CONTRACT_INVALID", "LocalEdit.mode is unsupported")
    if raw["strict_pixel_protection"] is not True:
        _fail(
            "LOCAL_EDIT_STRICT_PROTECTION_REQUIRED",
            "局部编辑必须启用严格像素保护",
        )
    source_size = _size(raw["source_size"], "LocalEdit.source_size")

    roi = _object(
        raw["roi"],
        label="LocalEdit.roi",
        required={"id", "coordinate_space", "rect"},
    )
    roi_id = _identifier(roi["id"], "LocalEdit.roi.id")
    rect = _rect(roi["rect"], "LocalEdit.roi.rect")

    normalized_mask: dict[str, Any] | None
    normalized_outpaint: dict[str, int] | None = None
    if mode == "inpaint":
        if "outpaint" in raw:
            _fail("LOCAL_EDIT_CONTRACT_INVALID", "inpaint cannot include outpaint fields")
        if roi["coordinate_space"] != "source-pixel":
            _fail("LOCAL_EDIT_CONTRACT_INVALID", "inpaint ROI must use source-pixel coordinates")
        if not _rect_within(rect, source_size):
            _fail("LOCAL_EDIT_ROI_OUT_OF_BOUNDS", "局部编辑 ROI 超出原图范围")
        mask = _object(
            raw["mask"],
            label="LocalEdit.mask",
            required={"id", "roi_id", "width", "height", "sha256"},
        )
        mask_id = _identifier(mask["id"], "LocalEdit.mask.id")
        if _identifier(mask["roi_id"], "LocalEdit.mask.roi_id") != roi_id:
            _fail("LOCAL_EDIT_CONTRACT_INVALID", "Mask must reference the active ROI")
        width = _integer(mask["width"], "LocalEdit.mask.width", minimum=1)
        height = _integer(mask["height"], "LocalEdit.mask.height", minimum=1)
        if (width, height) != (source_size["width"], source_size["height"]):
            _fail("LOCAL_EDIT_MASK_SIZE_MISMATCH", "蒙版像素尺寸必须与原图一致")
        digest = str(mask["sha256"] or "").upper()
        if re.fullmatch(r"[A-F0-9]{64}", digest) is None:
            _fail("LOCAL_EDIT_CONTRACT_INVALID", "LocalEdit.mask.sha256 is invalid")
        normalized_mask = {
            "id": mask_id,
            "roi_id": roi_id,
            "width": width,
            "height": height,
            "sha256": digest,
        }
    else:
        if raw["mask"] is not None:
            _fail("LOCAL_EDIT_CONTRACT_INVALID", "outpaint write mask is derived locally")
        if "outpaint" not in raw:
            _fail("LOCAL_EDIT_CONTRACT_INVALID", "outpaint fields are required")
        outpaint = _object(
            raw["outpaint"],
            label="LocalEdit.outpaint",
            required={
                "output_width",
                "output_height",
                "source_x",
                "source_y",
                "transition_width",
            },
        )
        normalized_outpaint = {
            "output_width": _integer(
                outpaint["output_width"], "LocalEdit.outpaint.output_width", minimum=1
            ),
            "output_height": _integer(
                outpaint["output_height"], "LocalEdit.outpaint.output_height", minimum=1
            ),
            "source_x": _integer(outpaint["source_x"], "LocalEdit.outpaint.source_x"),
            "source_y": _integer(outpaint["source_y"], "LocalEdit.outpaint.source_y"),
            "transition_width": _integer(
                outpaint["transition_width"],
                "LocalEdit.outpaint.transition_width",
                maximum=2048,
            ),
        }
        output_size = {
            "width": normalized_outpaint["output_width"],
            "height": normalized_outpaint["output_height"],
        }
        if roi["coordinate_space"] != "output-pixel":
            _fail("LOCAL_EDIT_CONTRACT_INVALID", "outpaint ROI must use output-pixel coordinates")
        if not _rect_within(rect, output_size):
            _fail("LOCAL_EDIT_ROI_OUT_OF_BOUNDS", "扩图 ROI 超出输出画布范围")
        if (
            normalized_outpaint["source_x"] + source_size["width"] > output_size["width"]
            or normalized_outpaint["source_y"] + source_size["height"] > output_size["height"]
        ):
            _fail("LOCAL_EDIT_SOURCE_PLACEMENT_INVALID", "原图无法完整放入扩图画布")
        if (
            output_size["width"] == source_size["width"]
            and output_size["height"] == source_size["height"]
            and normalized_outpaint["source_x"] == 0
            and normalized_outpaint["source_y"] == 0
        ):
            _fail("LOCAL_EDIT_OUTPAINT_HAS_NO_NEW_AREA", "扩图必须包含新增像素区域")
        max_transition = min(source_size["width"], source_size["height"]) // 2
        if normalized_outpaint["transition_width"] > max_transition:
            _fail("LOCAL_EDIT_TRANSITION_TOO_WIDE", "扩图过渡带不能覆盖原图主体")
        normalized_mask = None

    normalized = {
        "schema_version": LOCAL_EDIT_CONTRACT_SCHEMA_VERSION,
        "operation_id": operation_id,
        "mode": mode,
        "source_canvas_version_id": source_canvas_version_id,
        "source_layer_id": source_layer_id,
        "source_sha256": source_sha256,
        "source_size": source_size,
        "roi": {
            "id": roi_id,
            "coordinate_space": roi["coordinate_space"],
            "rect": rect,
        },
        "mask": normalized_mask,
        "strict_pixel_protection": True,
        "cost": _normalize_cost(raw["cost"]),
    }
    if normalized_outpaint is not None:
        normalized["outpaint"] = normalized_outpaint
    return normalized


def image_fingerprint(image: Image.Image) -> str:
    """Hash dimensions, mode, and exact pixel bytes for replay and undo evidence."""
    normalized = image.copy()
    header = json.dumps(
        {"mode": normalized.mode, "width": normalized.width, "height": normalized.height},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    digest = hashlib.sha256()
    digest.update(header)
    digest.update(b"\0")
    digest.update(normalized.tobytes())
    return digest.hexdigest().upper()


def _rgba(image: Image.Image) -> Image.Image:
    return image.convert("RGBA")


def _pixel_difference_count(first: Image.Image, second: Image.Image) -> int:
    if first.size != second.size:
        _fail("LOCAL_EDIT_IMAGE_SIZE_MISMATCH", "无法比较不同尺寸的像素")
    left = _rgba(first)
    right = _rgba(second)
    difference = ImageChops.difference(left, right)
    pixels = difference.tobytes()
    return sum(1 for index in range(0, len(pixels), 4) if any(pixels[index:index + 4]))


def _difference_count_in_mask(
    first: Image.Image,
    second: Image.Image,
    mask: Image.Image,
) -> int:
    left = _rgba(first)
    right = _rgba(second)
    if left.size != right.size or mask.size != left.size:
        _fail("LOCAL_EDIT_IMAGE_SIZE_MISMATCH", "像素审计尺寸不一致")
    difference = ImageChops.difference(left, right)
    pixels = difference.tobytes()
    allowed_pixels = mask.convert("L").tobytes()
    return sum(
        1
        for index, allowed in enumerate(allowed_pixels)
        if allowed and any(pixels[index * 4:(index + 1) * 4])
    )


def _rect_mask(size: tuple[int, int], rect: Mapping[str, int]) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle(
        (
            rect["x"],
            rect["y"],
            rect["x"] + rect["width"] - 1,
            rect["y"] + rect["height"] - 1,
        ),
        fill=255,
    )
    return mask


def apply_strict_inpaint(
    source: Image.Image,
    candidate: Image.Image,
    mask: Image.Image,
    contract: Mapping[str, Any],
) -> tuple[Image.Image, dict[str, Any]]:
    """Composite a candidate through a verified mask and restore all other pixels."""
    normalized = normalize_local_edit_contract(contract)
    if normalized["mode"] != "inpaint":
        _fail("LOCAL_EDIT_MODE_MISMATCH", "当前合同不是局部编辑")
    expected_size = (
        normalized["source_size"]["width"],
        normalized["source_size"]["height"],
    )
    if source.size != expected_size:
        _fail("LOCAL_EDIT_SOURCE_SIZE_MISMATCH", "原图像素尺寸与合同不一致")
    if image_fingerprint(source) != normalized["source_sha256"]:
        _fail("LOCAL_EDIT_SOURCE_FINGERPRINT_MISMATCH", "原图版本与冻结合同不一致")
    if candidate.size != expected_size:
        _fail("LOCAL_EDIT_CANDIDATE_SIZE_MISMATCH", "候选图像素尺寸与原图不一致")
    if mask.size != expected_size:
        _fail("LOCAL_EDIT_MASK_SIZE_MISMATCH", "蒙版像素尺寸与原图不一致")
    alpha = mask.convert("L")
    if image_fingerprint(alpha) != normalized["mask"]["sha256"]:
        _fail("LOCAL_EDIT_MASK_FINGERPRINT_MISMATCH", "蒙版内容与冻结合同不一致")
    if alpha.getbbox() is None:
        _fail("LOCAL_EDIT_MASK_EMPTY", "局部编辑蒙版不能为空")

    roi_mask = _rect_mask(expected_size, normalized["roi"]["rect"])
    outside_roi = ImageChops.subtract(alpha, roi_mask)
    if outside_roi.getbbox() is not None:
        _fail("LOCAL_EDIT_MASK_OUTSIDE_ROI", "蒙版包含 ROI 之外的可写像素")

    source_rgba = _rgba(source)
    candidate_rgba = _rgba(candidate)
    result = Image.composite(candidate_rgba, source_rgba, alpha)
    outside_mask = ImageChops.invert(alpha.point(lambda value: 255 if value else 0))
    receipt = {
        "contract_schema_version": LOCAL_EDIT_CONTRACT_SCHEMA_VERSION,
        "operation_id": normalized["operation_id"],
        "mode": "inpaint",
        "source_sha256": image_fingerprint(source),
        "candidate_sha256": image_fingerprint(candidate),
        "mask_sha256": image_fingerprint(alpha),
        "output_sha256": image_fingerprint(result),
        "undo_source_sha256": image_fingerprint(source),
        "changed_pixels": _pixel_difference_count(source_rgba, result),
        "outside_mask_changed_pixels": _difference_count_in_mask(
            source_rgba, result, outside_mask
        ),
        "automatic_paid_retry": False,
    }
    if receipt["outside_mask_changed_pixels"] != 0:
        _fail("LOCAL_EDIT_PROTECTED_PIXELS_CHANGED", "严格模式检测到蒙版外像素变化")
    return result, receipt


def _outpaint_masks(
    normalized: Mapping[str, Any],
) -> tuple[Image.Image, Image.Image, Image.Image, Image.Image]:
    source_size = normalized["source_size"]
    outpaint = normalized["outpaint"]
    output_size = (outpaint["output_width"], outpaint["output_height"])
    source_rect = {
        "x": outpaint["source_x"],
        "y": outpaint["source_y"],
        "width": source_size["width"],
        "height": source_size["height"],
    }
    roi_mask = _rect_mask(output_size, normalized["roi"]["rect"])
    source_mask = _rect_mask(output_size, source_rect)
    new_area = ImageChops.multiply(ImageChops.invert(source_mask), roi_mask)

    transition = Image.new("L", output_size, 0)
    width = outpaint["transition_width"]
    if width:
        inner = {
            "x": source_rect["x"] + width,
            "y": source_rect["y"] + width,
            "width": source_rect["width"] - (2 * width),
            "height": source_rect["height"] - (2 * width),
        }
        if inner["width"] > 0 and inner["height"] > 0:
            inner_mask = _rect_mask(output_size, inner)
            transition = ImageChops.subtract(source_mask, inner_mask)
        else:
            transition = source_mask.copy()
        transition = ImageChops.multiply(transition, roi_mask)
    allowed = ImageChops.lighter(new_area, transition)
    protected = ImageChops.subtract(source_mask, transition)
    return allowed, protected, transition, new_area


def apply_strict_outpaint(
    source: Image.Image,
    candidate: Image.Image,
    contract: Mapping[str, Any],
) -> tuple[Image.Image, dict[str, Any]]:
    """Place the source on a larger canvas and only accept authorized candidate pixels."""
    normalized = normalize_local_edit_contract(contract)
    if normalized["mode"] != "outpaint":
        _fail("LOCAL_EDIT_MODE_MISMATCH", "当前合同不是扩图")
    source_size = (
        normalized["source_size"]["width"],
        normalized["source_size"]["height"],
    )
    if source.size != source_size:
        _fail("LOCAL_EDIT_SOURCE_SIZE_MISMATCH", "原图像素尺寸与合同不一致")
    if image_fingerprint(source) != normalized["source_sha256"]:
        _fail("LOCAL_EDIT_SOURCE_FINGERPRINT_MISMATCH", "原图版本与冻结合同不一致")
    outpaint = normalized["outpaint"]
    output_size = (outpaint["output_width"], outpaint["output_height"])
    if candidate.size != output_size:
        _fail("LOCAL_EDIT_CANDIDATE_SIZE_MISMATCH", "扩图候选尺寸与合同不一致")

    base = Image.new("RGBA", output_size, (0, 0, 0, 0))
    base.paste(_rgba(source), (outpaint["source_x"], outpaint["source_y"]))
    candidate_rgba = _rgba(candidate)
    allowed, protected, transition, new_area = _outpaint_masks(normalized)
    result = Image.composite(candidate_rgba, base, allowed)
    receipt = {
        "contract_schema_version": LOCAL_EDIT_CONTRACT_SCHEMA_VERSION,
        "operation_id": normalized["operation_id"],
        "mode": "outpaint",
        "source_sha256": image_fingerprint(source),
        "candidate_sha256": image_fingerprint(candidate),
        "output_sha256": image_fingerprint(result),
        "undo_source_sha256": image_fingerprint(source),
        "protected_changed_pixels": _difference_count_in_mask(base, result, protected),
        "transition_changed_pixels": _difference_count_in_mask(base, result, transition),
        "new_area_changed_pixels": _difference_count_in_mask(base, result, new_area),
        "automatic_paid_retry": False,
    }
    if receipt["protected_changed_pixels"] != 0:
        _fail("LOCAL_EDIT_PROTECTED_PIXELS_CHANGED", "扩图修改了受保护的原图像素")
    return result, receipt
